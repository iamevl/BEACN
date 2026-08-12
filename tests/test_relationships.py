from beacn.relationships.evidence import Evidence
from beacn.relationships.manager import RelationshipManager
from beacn.relationships.provider import RelationshipProvider
from beacn.relationships.providers.generic import GenericProvider
from beacn.relationships.providers.infrastructure import InfrastructureProvider
from beacn.relationships.providers.manual import ManualProvider


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

    relationship = manager.evaluate({
        "devices": [{"ip": "192.0.2.60"}],
        "infrastructure": [
            {"ref": "infra:low"},
            {"ref": "infra:high"},
        ],
    })[0]

    assert relationship.parent_ref == "infra:high"
    assert [item.confidence for item in relationship.evidence] == [90, 40]
    assert [item.reason for item in relationship.evidence] == [
        "high_candidate",
        "low_candidate",
    ]


def test_equal_confidence_different_parents_remains_ambiguous():
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

    context = {
        "devices": [{"ip": "192.0.2.61"}],
        "infrastructure": [
            {"ref": "infra:first"},
            {"ref": "infra:second"},
        ],
    }

    assert manager.evaluate(context) == []
    assert manager.diagnostics == [{
        "subject_ref": "device:192.0.2.61",
        "code": "ambiguous_tie",
        "message": "Equal-authority relationship candidates disagree on the parent.",
        "parent_refs": ["infra:first", "infra:second"],
        "providers": ["first", "second"],
    }]

    reversed_manager = RelationshipManager()
    reversed_manager.register(StaticProvider("second", [second]))
    reversed_manager.register(StaticProvider("first", [first]))
    assert reversed_manager.evaluate(context) == []
    assert reversed_manager.diagnostics == manager.diagnostics


def test_equal_candidates_for_same_parent_use_stable_provider_key():
    subject = "device:192.0.2.62"
    first = Evidence(
        subject, "infra:parent", "z-provider", 80,
        "wired", "candidate",
    )
    second = Evidence(
        subject, "infra:parent", "a-provider", 80,
        "wired", "candidate",
    )
    context = {
        "devices": [{"ip": "192.0.2.62"}],
        "infrastructure": [{"ref": "infra:parent"}],
    }

    for evidence in ((first, second), (second, first)):
        manager = RelationshipManager()
        manager.register(StaticProvider("one", [evidence[0]]))
        manager.register(StaticProvider("two", [evidence[1]]))
        relationship = manager.evaluate(context)[0]
        assert relationship.provider == "a-provider"


def test_relationship_output_is_sorted_by_subject_reference():
    evidence = [
        Evidence("device:192.0.2.9", "infra:x", "static", 1, "unknown", "x"),
        Evidence("device:192.0.2.2", "infra:x", "static", 1, "unknown", "x"),
    ]
    manager = RelationshipManager()
    manager.register(StaticProvider("static", evidence))

    context = {
        "devices": [
            {"ip": "192.0.2.9"},
            {"ip": "192.0.2.2"},
        ],
        "infrastructure": [{"ref": "infra:x"}],
    }

    assert [item.subject_ref for item in manager.evaluate(context)] == [
        "device:192.0.2.2",
        "device:192.0.2.9",
    ]


def test_missing_parent_and_infrastructure_cycles_are_rejected():
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

    manager = RelationshipManager()
    manager.register(InfrastructureProvider())
    relationships = manager.evaluate({
        "devices": [],
        "infrastructure": infrastructure,
    })

    assert relationships == []
    assert {
        (item["subject_ref"], item["code"])
        for item in manager.diagnostics
    } == {
        ("infra:missing-child", "invalid_parent"),
        ("infra:a", "cycle_rejected"),
        ("infra:b", "cycle_rejected"),
    }


def test_self_parent_is_rejected():
    manager = RelationshipManager()
    manager.register(StaticProvider("self", [Evidence(
        "device:192.0.2.70", "device:192.0.2.70", "self", 100,
        "wired", "self",
    )]))

    assert manager.evaluate({
        "devices": [{"ip": "192.0.2.70"}],
        "infrastructure": [],
    }) == []
    assert manager.diagnostics[0]["code"] == "self_parent"


def test_multi_node_cycle_rejects_every_participating_edge():
    evidence = [
        Evidence("infra:a", "infra:b", "static", 100, "wired", "configured"),
        Evidence("infra:b", "infra:c", "static", 100, "wired", "configured"),
        Evidence("infra:c", "infra:a", "static", 100, "wired", "configured"),
        Evidence("infra:child", "infra:a", "static", 100, "wired", "configured"),
    ]
    manager = RelationshipManager()
    manager.register(StaticProvider("static", evidence))
    relationships = manager.evaluate({
        "devices": [],
        "infrastructure": [
            {"ref": ref}
            for ref in ("infra:a", "infra:b", "infra:c", "infra:child")
        ],
    })

    assert [(item.subject_ref, item.parent_ref) for item in relationships] == [
        ("infra:child", "infra:a"),
    ]
    assert {
        item["subject_ref"] for item in manager.diagnostics
        if item["code"] == "cycle_rejected"
    } == {"infra:a", "infra:b", "infra:c"}


def manual_context(device, *extra_devices):
    return {
        "devices": [device, *extra_devices],
        "infrastructure": [{"ref": "infra:parent"}],
    }


def test_manual_device_to_infrastructure_is_authoritative():
    device = {
        "ip": "192.0.2.80",
        "device_type": "nas",
        "connection_source": "manual",
        "connection_method": "wireless",
        "connection_parent_ref": "infra:parent",
    }
    manager = RelationshipManager()
    manager.register(GenericProvider())
    manager.register(ManualProvider())

    relationship = manager.evaluate(manual_context(device))[0]

    assert relationship.parent_ref == "infra:parent"
    assert relationship.provider == "manual"
    assert relationship.confidence == 100
    assert relationship.transport == "wireless"
    assert relationship.reason == "manual_override"


def test_manual_relationship_blocks_automatic_fallback():
    device = {
        "ip": "192.0.2.87",
        "connection_source": "manual",
        "connection_method": "wired",
        "connection_parent_ref": "infra:deleted",
    }
    manager = RelationshipManager()
    manager.register(ManualProvider())
    manager.register(StaticProvider("automatic", [Evidence(
        "device:192.0.2.87", "infra:parent", "automatic", 90,
        "wired", "automatic_candidate",
    )]))

    assert manager.evaluate(manual_context(device)) == []
    assert {
        item["code"] for item in manager.diagnostics
    } == {
        "invalid_parent",
        "manual_fallback_blocked",
    }


def test_invalid_high_confidence_parent_allows_valid_lower_candidate():
    subject = "device:192.0.2.88"
    manager = RelationshipManager()
    manager.register(StaticProvider("candidates", [
        Evidence(
            subject, "infra:deleted", "invalid", 100,
            "wired", "invalid_candidate",
        ),
        Evidence(
            subject, "infra:parent", "valid", 60,
            "wired", "valid_candidate",
        ),
    ]))

    relationship = manager.evaluate({
        "devices": [{"ip": "192.0.2.88"}],
        "infrastructure": [{"ref": "infra:parent"}],
    })[0]

    assert relationship.parent_ref == "infra:parent"
    assert relationship.confidence == 60
    assert manager.diagnostics[0]["code"] == "invalid_parent"


def test_manual_device_parent_and_legacy_ip_are_supported():
    parent = {"ip": "192.0.2.81", "device_type": "router"}
    explicit = {
        "ip": "192.0.2.82",
        "connection_source": "manual",
        "connection_method": "wired",
        "connection_parent_ref": "device:192.0.2.81",
        "connection_parent_ip": "192.0.2.99",
    }
    legacy = {
        "ip": "192.0.2.83",
        "connection_source": "manual",
        "connection_method": "wired",
        "connection_parent_ip": "192.0.2.81",
    }
    manager = RelationshipManager()
    manager.register(ManualProvider())
    relationships = manager.evaluate({
        "devices": [parent, explicit, legacy],
        "infrastructure": [],
    })

    assert {
        item.subject_ref: item.parent_ref
        for item in relationships
    } == {
        "device:192.0.2.82": "device:192.0.2.81",
        "device:192.0.2.83": "device:192.0.2.81",
    }


def test_invalid_or_incomplete_manual_data_blocks_generic_fallback():
    cases = [
        {
            "ip": "192.0.2.84",
            "device_type": "nas",
            "connection_source": "manual",
            "connection_method": "bluetooth",
            "connection_parent_ref": "infra:parent",
        },
        {
            "ip": "192.0.2.85",
            "device_type": "nas",
            "connection_source": "manual",
            "connection_method": "wired",
        },
        {
            "ip": "192.0.2.86",
            "device_type": "nas",
            "connection_source": "manual",
            "connection_method": "wired",
            "connection_parent_ref": "infra:deleted",
        },
    ]
    manager = RelationshipManager()
    manager.register(ManualProvider())
    manager.register(GenericProvider())

    assert manager.evaluate({
        "devices": [ROUTER, *cases],
        "infrastructure": [SWITCH, {"ref": "infra:parent"}],
    }) == []
    assert {
        (item["subject_ref"], item["code"])
        for item in manager.diagnostics
    } == {
        ("device:192.0.2.84", "incomplete_manual"),
        ("device:192.0.2.85", "incomplete_manual"),
        ("device:192.0.2.86", "invalid_parent"),
    }
