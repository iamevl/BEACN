import json
import subprocess
from pathlib import Path

import pytest

from beacn.relationships.evidence import Evidence
from beacn.relationships.manager import RelationshipManager
from beacn.relationships.provider import RelationshipProvider
from beacn.relationships.providers.generic import GenericProvider
from beacn.relationships.providers.infrastructure import InfrastructureProvider
from beacn.relationships.providers.manual import ManualProvider


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "topology_parity_runner.js"


def device(number, device_type="phone", **extra):
    ip = f"192.0.2.{number}"
    return {
        "id": f"device-{number}",
        "ip": ip,
        "hostname": f"host-{number}.example.invalid",
        "display_name": f"Example Device {number}",
        "device_type": device_type,
        "is_online": 1,
        "agent_available": 0,
        "connection_method": "automatic",
        "connection_source": "inferred",
        **extra,
    }


def infrastructure(
    object_id,
    infrastructure_type,
    parent_ref="",
    connection_method="wired",
):
    return {
        "id": object_id,
        "ref": f"infra:{object_id}",
        "name": f"Example {object_id}",
        "infrastructure_type": infrastructure_type,
        "parent_ref": parent_ref,
        "connection_method": connection_method,
        "interfaces": [],
    }


def browser_decisions(context):
    result = subprocess.run(
        ["node", str(RUNNER)],
        cwd=ROOT,
        input=json.dumps(context),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def canonical_identity(ref, context):
    for item in context["devices"]:
        if ref == f"device:{item['ip']}":
            return f"device:{item['id']}"

    for item in context["infrastructure"]:
        if ref == item["ref"]:
            return f"infrastructure:{item['id']}"

    return ref


def server_decisions(context, extra_providers=()):
    manager = RelationshipManager()
    manager.register(InfrastructureProvider())
    manager.register(ManualProvider())
    manager.register(GenericProvider())

    for provider in extra_providers:
        manager.register(provider)

    relationships = manager.evaluate(context)
    resolved_subjects = {
        relationship.subject_ref
        for relationship in relationships
    }
    all_devices = {
        f"device:{item['ip']}"
        for item in context["devices"]
    }

    return {
        "relationships": sorted(
            ({
                "subject": canonical_identity(
                    item.subject_ref,
                    context,
                ),
                "parent": canonical_identity(
                    item.parent_ref,
                    context,
                ),
                "transport": item.transport,
                "resolved": True,
            } for item in relationships),
            key=lambda item: item["subject"],
        ),
        "unresolved": sorted(
            canonical_identity(ref, context)
            for ref in all_devices - resolved_subjects
        ),
        "diagnostics": manager.diagnostics,
    }


def compare(context, extra_providers=()):
    browser = browser_decisions(context)
    server = server_decisions(context, extra_providers)
    return browser, server


def base_distribution():
    router = device(1, "router")
    switch = infrastructure(
        "distribution",
        "switch",
        f"device:{router['ip']}",
    )
    return router, switch


@pytest.mark.parametrize("endpoint", [
    device(20, "nas"),
    device(21, "server"),
    device(22, "media_tuner"),
    device(23, "ups"),
    device(24, "unknown", hostname="hdhr-example"),
    device(25, "computer", agent_available=1),
])
def test_generic_relationship_decisions_match_browser(endpoint):
    router, switch = base_distribution()
    context = {
        "devices": [router, endpoint],
        "infrastructure": [switch],
    }
    browser, server = compare(context)
    assert browser["relationships"] == server["relationships"]
    assert browser["unresolved"] == server["unresolved"]


def test_configured_nested_infrastructure_and_single_switch_match():
    router, distribution = base_distribution()
    internet = infrastructure(
        "internet", "internet", "", "virtual"
    )
    gateway = infrastructure(
        "gateway", "isp_gateway", internet["ref"]
    )
    child_switch = device(30, "switch")
    context = {
        "devices": [router, child_switch],
        "infrastructure": [internet, gateway, distribution],
    }
    browser, server = compare(context)
    assert browser["relationships"] == server["relationships"]
    assert browser["unresolved"] == server["unresolved"]


@pytest.mark.parametrize("transport,parent_kind", [
    ("wired", "infrastructure"),
    ("wired", "device"),
    ("wireless", "device"),
])
def test_manual_router_switch_wap_relationships_match(
    transport,
    parent_kind,
):
    router = device(1, "router")
    parent = (
        infrastructure("access", "access_point")
        if parent_kind == "infrastructure"
        else router
    )
    parent_ref = (
        parent["ref"]
        if parent_kind == "infrastructure"
        else f"device:{parent['ip']}"
    )
    endpoint = device(
        40,
        "phone",
        connection_source="manual",
        connection_method=transport,
        connection_parent_ref=parent_ref,
    )
    context = {
        "devices": [router, endpoint],
        "infrastructure": (
            [parent]
            if parent_kind == "infrastructure"
            else []
        ),
    }
    browser, server = compare(context)
    assert browser["relationships"] == server["relationships"]
    assert browser["unresolved"] == server["unresolved"]


def test_explicit_wireless_client_to_wap_matches():
    router = device(1, "router")
    wap = device(
        45,
        "access_point",
        connection_source="manual",
        connection_method="wired",
        connection_parent_ref=f"device:{router['ip']}",
    )
    client = device(
        46,
        "tablet",
        connection_source="manual",
        connection_method="wireless",
        connection_parent_ref=f"device:{wap['ip']}",
    )
    context = {
        "devices": [router, wap, client],
        "infrastructure": [],
    }
    browser, server = compare(context)
    assert browser["relationships"] == server["relationships"]
    assert browser["unresolved"] == server["unresolved"]


@pytest.mark.parametrize("configuration", [
    {"connection_parent_ref": "infra:missing"},
    {"connection_parent_ref": "device:192.0.2.50"},
    {"connection_method": "bluetooth"},
])
def test_invalid_manual_relationships_are_unresolved_in_both(configuration):
    relationship = {
        "connection_source": "manual",
        "connection_method": "wired",
        "connection_parent_ref": "",
        **configuration,
    }
    endpoint = device(50, "phone", **relationship)
    context = {"devices": [endpoint], "infrastructure": []}
    browser, server = compare(context)
    assert browser["relationships"] == server["relationships"] == []
    assert browser["unresolved"] == server["unresolved"]
    assert server["diagnostics"]


def test_ordinary_endpoint_and_no_evidence_are_unresolved_in_both():
    router = device(1, "router")
    endpoint = device(60, "speaker")
    context = {
        "devices": [router, endpoint],
        "infrastructure": [],
    }
    browser, server = compare(context)
    assert browser["relationships"] == server["relationships"] == []
    assert browser["unresolved"] == server["unresolved"]


@pytest.mark.parametrize("parents", [
    {70: 71, 71: 70},
    {70: 71, 71: 72, 72: 70},
])
def test_server_cycle_rejection_is_an_expected_safety_difference(parents):
    devices = [
        device(
            number,
            "switch",
            connection_source="manual",
            connection_method="wired",
            connection_parent_ref=f"device:192.0.2.{parent}",
        )
        for number, parent in parents.items()
    ]
    browser, server = compare({
        "devices": devices,
        "infrastructure": [],
    })
    assert browser["relationships"]
    assert server["relationships"] == []
    assert {
        item["code"] for item in server["diagnostics"]
    } == {"cycle_rejected"}


def test_infrastructure_cycle_rejection_is_expected_safety_difference():
    first = infrastructure("first", "switch", "infra:second")
    second = infrastructure("second", "switch", "infra:first")
    context = {
        "devices": [],
        "infrastructure": [first, second],
    }
    browser, server = compare(context)
    assert browser["relationships"]
    assert server["relationships"] == []
    assert {
        item["code"] for item in server["diagnostics"]
    } == {"cycle_rejected"}


class FixedProvider(RelationshipProvider):
    def __init__(self, name, evidence):
        self.name = name
        self.evidence = evidence

    def collect(self, context):
        return list(self.evidence)


def test_equal_confidence_ambiguity_is_server_only_capability():
    subject = device(80)
    first = infrastructure("first", "switch")
    second = infrastructure("second", "switch")
    evidence = [
        Evidence(
            f"device:{subject['ip']}",
            parent["ref"],
            "synthetic",
            75,
            "wired",
            "synthetic_evidence",
        )
        for parent in (first, second)
    ]
    context = {
        "devices": [subject],
        "infrastructure": [first, second],
    }
    _, server = compare(
        context,
        [FixedProvider("synthetic", evidence)],
    )
    assert server["relationships"] == []
    assert server["diagnostics"][0]["code"] == "ambiguous_tie"


def test_equivalent_same_parent_evidence_resolves_deterministically():
    subject = device(81)
    parent = infrastructure("parent", "switch")
    evidence = [
        Evidence(
            f"device:{subject['ip']}",
            parent["ref"],
            provider,
            75,
            "wired",
            "synthetic_evidence",
        )
        for provider in ("z-provider", "a-provider")
    ]
    context = {
        "devices": [subject],
        "infrastructure": [parent],
    }
    server = server_decisions(
        context,
        [FixedProvider("synthetic", evidence)],
    )
    assert server["relationships"] == [{
        "subject": f"device:{subject['id']}",
        "parent": f"infrastructure:{parent['id']}",
        "transport": "wired",
        "resolved": True,
    }]
