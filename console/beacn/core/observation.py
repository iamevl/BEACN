from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID


ATTACHMENT_FIELD = "attachment"
ATTACHMENT_SCOPES = {
    "direct",
    "downstream",
    "unknown",
}
ATTACHMENT_FRESHNESS_SECONDS = 300


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Observation:
    device_id: str
    source: str
    field: str
    value: Any
    observed_at: str = field(default_factory=utc_now)
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_mac(value: Any) -> str | None:
    """Return one canonical six-octet MAC address."""
    if isinstance(value, (bytes, bytearray)):
        if len(value) != 6:
            return None
        return ":".join(f"{octet:02x}" for octet in value)

    text = str(value or "").strip().lower()
    compact = text.replace(":", "").replace("-", "")

    if len(compact) != 12:
        return None

    try:
        raw = bytes.fromhex(compact)
    except ValueError:
        return None

    if len(raw) != 6:
        return None

    return ":".join(f"{octet:02x}" for octet in raw)


def correlate_device_by_mac(
    observed_mac: Any,
    devices,
    *,
    infrastructure_macs=(),
) -> dict[str, Any]:
    """Correlate a MAC to exactly one canonical endpoint device."""
    mac = normalize_mac(observed_mac)

    if not mac:
        return {"status": "invalid", "mac": None, "device_id": None}

    excluded = {
        normalized
        for value in infrastructure_macs
        if (normalized := normalize_mac(value))
    }

    if mac in excluded:
        return {"status": "infrastructure", "mac": mac, "device_id": None}

    matches = []

    for device in devices:
        device_mac = (
            device.get("mac") or device.get("primary_mac")
            if isinstance(device, dict)
            else getattr(device, "primary_mac", None)
        )

        if normalize_mac(device_mac) != mac:
            continue

        device_id = (
            device.get("id")
            if isinstance(device, dict)
            else getattr(device, "id", None)
        )

        if device_id:
            matches.append(str(device_id))

    unique_matches = sorted(set(matches))

    if not unique_matches:
        return {"status": "unmatched", "mac": mac, "device_id": None}

    if len(unique_matches) > 1:
        return {"status": "ambiguous", "mac": mac, "device_id": None}

    return {
        "status": "matched",
        "mac": mac,
        "device_id": unique_matches[0],
    }


def attachment_observation(
    *,
    device_id: str,
    source: str,
    device_mac: Any,
    infrastructure_id: str,
    attachment_scope: str,
    confidence: float,
    interface_index: int | None = None,
    interface_name: str | None = None,
    bridge_port: int | None = None,
    upstream_infrastructure_id: str | None = None,
    collector_ref: str | None = None,
    observed_at: str | None = None,
) -> Observation:
    """Build a validated device-scoped attachment observation."""
    canonical_device_id = str(UUID(str(device_id)))
    canonical_infrastructure_id = str(UUID(str(infrastructure_id)))
    canonical_upstream_id = (
        str(UUID(str(upstream_infrastructure_id)))
        if upstream_infrastructure_id
        else None
    )
    mac = normalize_mac(device_mac)
    scope = str(attachment_scope or "").strip().lower()

    if not mac:
        raise ValueError("Attachment observation requires a valid device MAC.")

    if scope not in ATTACHMENT_SCOPES:
        raise ValueError("Unsupported attachment scope.")

    clean_source = str(source or "").strip()
    if not clean_source:
        raise ValueError("Attachment observation requires a source.")

    confidence_value = float(confidence)
    if not 0 <= confidence_value <= 1:
        raise ValueError("Attachment confidence must be between 0 and 1.")

    clean_collector_ref = (
        str(collector_ref).strip()
        if collector_ref
        else None
    )
    if clean_collector_ref and any(
        marker in clean_collector_ref.lower()
        for marker in (
            "community=",
            "password",
            "authkey",
            "privkey",
            "secret",
            "token=",
            "@",
        )
    ):
        raise ValueError("Collector reference must not contain credentials.")

    value = {
        "device_mac": mac,
        "infrastructure_id": canonical_infrastructure_id,
        "interface_index": interface_index,
        "interface_name": str(interface_name).strip() if interface_name else None,
        "bridge_port": bridge_port,
        "attachment_scope": scope,
        "upstream_infrastructure_id": canonical_upstream_id,
        "collector_ref": clean_collector_ref,
    }

    observation_time = observed_at or utc_now()
    parsed_observation_time = parse_utc_timestamp(observation_time)
    if parsed_observation_time is None:
        raise ValueError("Attachment observation requires a UTC timestamp.")

    return Observation(
        device_id=canonical_device_id,
        source=clean_source,
        field=ATTACHMENT_FIELD,
        value=value,
        confidence=confidence_value,
        observed_at=parsed_observation_time.isoformat(),
    )


def parse_utc_timestamp(value: Any) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None

    if timestamp.tzinfo is None:
        return None

    return timestamp.astimezone(timezone.utc)


def attachment_is_fresh(
    observed_at: Any,
    *,
    now: datetime | None = None,
    freshness_seconds: int = ATTACHMENT_FRESHNESS_SECONDS,
) -> bool:
    timestamp = parse_utc_timestamp(observed_at)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if timestamp is None or freshness_seconds < 0:
        return False

    age = reference - timestamp
    return timedelta(0) <= age <= timedelta(seconds=freshness_seconds)


def select_current_attachment(
    observations,
    *,
    now: datetime | None = None,
    freshness_seconds: int = ATTACHMENT_FRESHNESS_SECONDS,
) -> dict[str, Any]:
    """Select the newest unambiguous fresh attachment observation."""
    eligible = []

    for observation in observations:
        if observation.get("field") != ATTACHMENT_FIELD:
            continue

        timestamp = parse_utc_timestamp(observation.get("observed_at"))
        if timestamp is None or not attachment_is_fresh(
            observation.get("observed_at"),
            now=now,
            freshness_seconds=freshness_seconds,
        ):
            continue

        value = observation.get("value")
        if not _valid_attachment_value(value):
            continue

        eligible.append((timestamp, observation))

    if not eligible:
        return {"status": "none", "observation": None}

    newest = max(timestamp for timestamp, _ in eligible)
    latest = [item for timestamp, item in eligible if timestamp == newest]
    signatures = {
        (
            item["value"].get("infrastructure_id"),
            item["value"].get("interface_index"),
            item["value"].get("bridge_port"),
            item["value"].get("attachment_scope"),
        )
        for item in latest
    }

    if len(signatures) != 1:
        return {"status": "ambiguous", "observation": None}

    return {"status": "current", "observation": latest[0]}


def _valid_attachment_value(value: Any) -> bool:
    if not isinstance(value, dict):
        return False

    if normalize_mac(value.get("device_mac")) is None:
        return False

    if value.get("attachment_scope") not in ATTACHMENT_SCOPES:
        return False

    try:
        UUID(str(value.get("infrastructure_id")))
    except (TypeError, ValueError):
        return False

    return True
