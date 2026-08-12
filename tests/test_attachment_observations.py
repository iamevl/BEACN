import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from beacn.core.observation import (
    ATTACHMENT_FRESHNESS_SECONDS,
    attachment_is_fresh,
    attachment_observation,
    correlate_device_by_mac,
    normalize_mac,
    select_current_attachment,
)
from beacn.database import Database, initialise_schema
from beacn.inventory import DeviceRepository


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [
    "aa:bb:cc:dd:ee:ff",
    "AA:BB:CC:DD:EE:FF",
    "aa-bb-cc-dd-ee-ff",
    "aabbccddeeff",
    b"\xaa\xbb\xcc\xdd\xee\xff",
])
def test_mac_normalization(value):
    assert normalize_mac(value) == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize("value", [
    "",
    "aa:bb:cc:dd:ee",
    "gg:bb:cc:dd:ee:ff",
    b"\x00\x01",
    None,
])
def test_malformed_mac_is_rejected(value):
    assert normalize_mac(value) is None


def test_canonical_device_correlation_and_ip_independence():
    canonical_id = str(uuid4())
    devices = [{
        "id": canonical_id,
        "ip": "192.0.2.20",
        "mac": "AA-BB-CC-DD-EE-FF",
    }]
    first = correlate_device_by_mac("aabbccddeeff", devices)
    devices[0]["ip"] = "198.51.100.20"
    second = correlate_device_by_mac("aa:bb:cc:dd:ee:ff", devices)
    assert first == second == {
        "status": "matched",
        "mac": "aa:bb:cc:dd:ee:ff",
        "device_id": canonical_id,
    }


def test_zero_duplicate_and_infrastructure_mac_correlation():
    first_id = str(uuid4())
    second_id = str(uuid4())
    devices = [
        {"id": first_id, "mac": "00:11:22:33:44:55"},
        {"id": second_id, "mac": "00-11-22-33-44-55"},
    ]
    assert correlate_device_by_mac("aa:bb:cc:dd:ee:ff", devices)[
        "status"
    ] == "unmatched"
    assert correlate_device_by_mac("00:11:22:33:44:55", devices)[
        "status"
    ] == "ambiguous"
    assert correlate_device_by_mac(
        "aa:bb:cc:dd:ee:ff",
        devices,
        infrastructure_macs=["AA-BB-CC-DD-EE-FF"],
    )["status"] == "infrastructure"


@pytest.mark.parametrize("scope", ["direct", "downstream", "unknown"])
def test_attachment_observation_contract_and_serialization(scope):
    device_id = str(uuid4())
    infrastructure_id = str(uuid4())
    upstream_id = str(uuid4())
    observation = attachment_observation(
        device_id=device_id,
        source="snmp_bridge_fdb",
        device_mac="AA-BB-CC-DD-EE-FF",
        infrastructure_id=infrastructure_id,
        interface_index=9,
        interface_name="ethernet9",
        bridge_port=4,
        attachment_scope=scope,
        upstream_infrastructure_id=upstream_id,
        collector_ref="infrastructure:example",
        confidence=0.9,
        observed_at=NOW.isoformat(),
    )
    serialized = json.dumps(observation.to_dict())
    assert observation.device_id == device_id
    assert observation.field == "attachment"
    assert observation.value["device_mac"] == "aa:bb:cc:dd:ee:ff"
    assert observation.value["infrastructure_id"] == infrastructure_id
    assert observation.value["attachment_scope"] == scope
    assert "password" not in serialized.lower()
    assert "community" not in serialized.lower()


def test_attachment_contract_requires_canonical_ids_and_rejects_credentials():
    arguments = {
        "device_id": str(uuid4()),
        "source": "snmp_bridge_fdb",
        "device_mac": "00:11:22:33:44:55",
        "infrastructure_id": str(uuid4()),
        "attachment_scope": "direct",
        "confidence": 0.9,
    }
    for override in (
        {"device_id": "not-a-uuid"},
        {"infrastructure_id": "not-a-uuid"},
        {"attachment_scope": "nearby"},
        {"collector_ref": "community=synthetic"},
        {"observed_at": "not-a-time"},
        {"confidence": 1.01},
    ):
        with pytest.raises(ValueError):
            attachment_observation(**{**arguments, **override})


def test_attachment_freshness_boundary_and_invalid_timestamps():
    fresh = NOW - timedelta(seconds=ATTACHMENT_FRESHNESS_SECONDS - 1)
    boundary = NOW - timedelta(seconds=ATTACHMENT_FRESHNESS_SECONDS)
    stale = NOW - timedelta(seconds=ATTACHMENT_FRESHNESS_SECONDS + 1)
    assert attachment_is_fresh(fresh.isoformat(), now=NOW)
    assert attachment_is_fresh(boundary.isoformat(), now=NOW)
    assert not attachment_is_fresh(stale.isoformat(), now=NOW)
    assert not attachment_is_fresh(None, now=NOW)
    assert not attachment_is_fresh("not-a-time", now=NOW)
    assert not attachment_is_fresh((NOW + timedelta(seconds=1)).isoformat(), now=NOW)


def attachment_record(infrastructure_id, observed_at, *, source="snmp_bridge_fdb"):
    return {
        "source": source,
        "field": "attachment",
        "value": {
            "device_mac": "00:11:22:33:44:55",
            "infrastructure_id": infrastructure_id,
            "interface_index": 9,
            "bridge_port": 4,
            "attachment_scope": "direct",
        },
        "confidence": 0.9,
        "observed_at": observed_at,
    }


def test_newest_attachment_supersedes_history_and_mac_movement():
    old_parent = str(uuid4())
    new_parent = str(uuid4())
    observations = [
        attachment_record(old_parent, (NOW - timedelta(seconds=30)).isoformat()),
        attachment_record(new_parent, (NOW - timedelta(seconds=5)).isoformat()),
        {"field": "hostname", "value": "ignored", "observed_at": NOW.isoformat()},
    ]
    selected = select_current_attachment(observations, now=NOW)
    assert selected["status"] == "current"
    assert selected["observation"]["value"]["infrastructure_id"] == new_parent


def test_equal_time_conflict_is_ambiguous_but_equivalent_rows_are_safe():
    timestamp = NOW.isoformat()
    first = attachment_record(str(uuid4()), timestamp)
    second = attachment_record(str(uuid4()), timestamp)
    assert select_current_attachment([first, second], now=NOW)["status"] == (
        "ambiguous"
    )
    assert select_current_attachment([first, dict(first)], now=NOW)["status"] == (
        "current"
    )


def test_stale_and_unrelated_observations_are_not_current():
    stale = attachment_record(
        str(uuid4()),
        (NOW - timedelta(seconds=ATTACHMENT_FRESHNESS_SECONDS + 1)).isoformat(),
    )
    unrelated = attachment_record(str(uuid4()), NOW.isoformat(), source="wireless")
    assert select_current_attachment([stale], now=NOW)["status"] == "none"
    assert unrelated["source"] == "wireless"
    malformed = attachment_record(str(uuid4()), NOW.isoformat())
    malformed["value"].pop("device_mac")
    assert select_current_attachment([malformed], now=NOW)["status"] == "none"


def test_repository_current_attachment_uses_disposable_database(tmp_path: Path):
    database = Database(tmp_path / "beacn.db")
    with database.connect() as conn:
        initialise_schema(conn)
    repository = DeviceRepository(database)
    device_id = str(uuid4())
    source = "snmp_bridge_fdb"
    older = attachment_observation(
        device_id=device_id,
        source=source,
        device_mac="00:11:22:33:44:55",
        infrastructure_id=str(uuid4()),
        attachment_scope="direct",
        confidence=0.9,
        observed_at=(NOW - timedelta(seconds=20)).isoformat(),
    )
    newer = attachment_observation(
        device_id=device_id,
        source=source,
        device_mac="00:11:22:33:44:55",
        infrastructure_id=str(uuid4()),
        attachment_scope="downstream",
        confidence=0.8,
        observed_at=(NOW - timedelta(seconds=5)).isoformat(),
    )
    repository.add_observation(older)
    repository.add_observation(newer)
    repository.add_observation(attachment_observation(
        device_id=device_id,
        source="wireless_association",
        device_mac="00:11:22:33:44:55",
        infrastructure_id=str(uuid4()),
        attachment_scope="direct",
        confidence=0.95,
        observed_at=NOW.isoformat(),
    ))
    repository.add_observation(attachment_observation(
        device_id=str(uuid4()),
        source=source,
        device_mac="00:11:22:33:44:55",
        infrastructure_id=str(uuid4()),
        attachment_scope="direct",
        confidence=0.9,
        observed_at=NOW.isoformat(),
    ))
    selected = repository.current_attachment(device_id, source, now=NOW)
    assert selected["status"] == "current"
    assert selected["observation"]["value"]["attachment_scope"] == "downstream"
