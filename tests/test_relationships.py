from beacn.relationships.evidence import Evidence
from beacn.relationships.manager import RelationshipManager
from beacn.relationships.provider import RelationshipProvider
from beacn.relationships.providers.generic import GenericProvider
from beacn.relationships.providers.infrastructure import InfrastructureProvider


ROUTER = {
    "ip": "192.0.2.1",
    "device_type": "router",
}
GATEWAY = {
    "ref": "infra:gateway",
    "infrastructure_type": "isp_gateway",
}
SWITCH = {
    "ref": "infra:distribution",
    "infrastructure_type": "switch",
    "parent_ref": "device:192.0.2.1",
    "connection_method": "wired",
}


def evaluate(*devices, infrastructure=(GATEWAY, SWITCH)):
    manager = RelationshipManager()
    manager.register(InfrastructureProvider())
    manager.register(GenericProvider())
    return manager.evaluate({
        "devices": [ROUTER, *devices],
        "infrastructure": list(infrastructure),
    })


def relationship_for(relationships, subject_ref):
    return next(
        item for item in relationships
        if item.subject_ref == subject_ref
    )


def test_configured_infrastructure_parent_is_authoritative():
    relationships = evaluate()
    relationship = relationship_for(
        relationships,
        "infra:distribution",
    )

    assert relationship.parent_ref == "device:192.0.2.1"
    assert relationship.provider == "infrastructure"
    assert relationship.confidence == 100
    assert relationship.transport == "wired"
    assert relationship.reason == "configured_infrastructure_parent"


def test_single_gateway_places_primary_router():
    relationship = relationship_for(
        evaluate(),
        "device:192.0.2.1",
    )

    assert relationship.parent_ref == "infra:gateway"
    assert relationship.provider == "generic"
    assert relationship.confidence == 85
    assert relationship.transport == "wired"
    assert relationship.reason == "single_known_isp_gateway"


def test_single_distribution_switch_places_unresolved_switch():
    relationship = relationship_for(
        evaluate({
            "ip": "192.0.2.20",
            "device_type": "switch",
        }),
        "device:192.0.2.20",
    )

    assert relationship.parent_ref == "infra:distribution"
    assert relationship.confidence == 70
    assert relationship.transport == "wired"
    assert relationship.reason == "single_distribution_switch"


def test_multiple_distribution_switches_disable_downstream_inference():
    other = {
        "ref": "infra:other-switch",
        "infrastructure_type": "switch",
        "parent_ref": "device:192.0.2.1",
        "connection_method": "wired",
    }
    relationships = evaluate(
        {"ip": "192.0.2.20", "device_type": "switch"},
        infrastructure=(GATEWAY, SWITCH, other),
    )

    assert not any(
        item.subject_ref == "device:192.0.2.20"
        for item in relationships
    )


def test_strong_wired_device_types_use_confidence_65():
    devices = [
        {"ip": "192.0.2.30", "device_type": "nas"},
        {"ip": "192.0.2.31", "device_type": "server"},
        {"ip": "192.0.2.32", "device_type": "media_tuner"},
        {"ip": "192.0.2.33", "device_type": "ups"},
    ]

    relationships = evaluate(*devices)

    for device in devices:
        relationship = relationship_for(
            relationships,
            f"device:{device['ip']}",
        )
        assert relationship.parent_ref == "infra:distribution"
        assert relationship.confidence == 65
        assert relationship.transport == "wired"
        assert relationship.reason == "strong_wired_endpoint"


def test_hdhomerun_names_and_agent_computer_are_strongly_wired():
    devices = [
        {
            "ip": "192.0.2.40",
            "device_type": "unknown",
            "hostname": "HDHR-EXAMPLE",
        },
        {
            "ip": "192.0.2.41",
            "device_type": "unknown",
            "display_name": "Example HD HomeRun",
        },
        {
            "ip": "192.0.2.42",
            "device_type": "computer",
            "agent_available": 1,
        },
    ]

    relationships = evaluate(*devices)

    assert {
        item.subject_ref
        for item in relationships
        if item.reason == "strong_wired_endpoint"
    } == {
        "device:192.0.2.40",
        "device:192.0.2.41",
        "device:192.0.2.42",
    }


def test_ambiguous_devices_have_no_evidence_and_remain_unresolved():
    ambiguous = [
        {"ip": "192.0.2.50", "device_type": "phone"},
        {"ip": "192.0.2.51", "device_type": "television"},
        {"ip": "192.0.2.52", "device_type": "computer"},
        {"ip": "192.0.2.53", "device_type": "access_point"},
    ]

    relationships = evaluate(*ambiguous)
    resolved = {item.subject_ref for item in relationships}

    assert all(
        f"device:{device['ip']}" not in resolved
        for device in ambiguous
    )


class StaticProvider(RelationshipProvider):
    def __init__(self, name, evidence):
        self.name = name
        self._evidence = evidence

    def collect(self, context):
        return list(self._evidence)


def test_highest_confidence_wins_and_losing_candidates_are_retained():
    manager = RelationshipManager()
    manager.register(StaticProvider("low", [Evidence(
        "device:192.0.2.60", "infra:low", "low", 40,
        "unknown", "low_candidate",
    )]))
    manager.register(StaticProvider("high", [Evidence(
        "device:192.0.2.60", "infra:high", "high", 90,
        "wired", "high_candidate",
    )]))

    relationship = manager.evaluate({})[0]

    assert relationship.parent_ref == "infra:high"
    assert [item.confidence for item in relationship.evidence] == [90, 40]
    assert [item.reason for item in relationship.evidence] == [
        "high_candidate",
        "low_candidate",
    ]


def test_equal_confidence_preserves_provider_registration_order():
    first = Evidence(
        "device:192.0.2.61", "infra:first", "first", 80,
        "wired", "first_candidate",
    )
    second = Evidence(
        "device:192.0.2.61", "infra:second", "second", 80,
        "wireless", "second_candidate",
    )
    manager = RelationshipManager()
    manager.register(StaticProvider("first", [first]))
    manager.register(StaticProvider("second", [second]))

    relationship = manager.evaluate({})[0]

    assert relationship.parent_ref == "infra:first"
    assert relationship.evidence == [first, second]


def test_relationship_output_is_sorted_by_subject_reference():
    evidence = [
        Evidence("device:192.0.2.9", "infra:x", "static", 1, "unknown", "x"),
        Evidence("device:192.0.2.2", "infra:x", "static", 1, "unknown", "x"),
    ]
    manager = RelationshipManager()
    manager.register(StaticProvider("static", evidence))

    assert [item.subject_ref for item in manager.evaluate({})] == [
        "device:192.0.2.2",
        "device:192.0.2.9",
    ]


def test_current_provider_emits_missing_parent_and_cycles_without_validation():
    infrastructure = [
        {
            "ref": "infra:missing-child",
            "infrastructure_type": "switch",
            "parent_ref": "infra:deleted",
            "connection_method": "wired",
        },
        {
            "ref": "infra:a",
            "infrastructure_type": "switch",
            "parent_ref": "infra:b",
            "connection_method": "wired",
        },
        {
            "ref": "infra:b",
            "infrastructure_type": "switch",
            "parent_ref": "infra:a",
            "connection_method": "wired",
        },
    ]

    relationships = InfrastructureProvider().collect({
        "infrastructure": infrastructure,
    })

    assert [(item.subject_ref, item.parent_ref) for item in relationships] == [
        ("infra:missing-child", "infra:deleted"),
        ("infra:a", "infra:b"),
        ("infra:b", "infra:a"),
    ]
