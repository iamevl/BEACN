from datetime import datetime, timezone

import pytest

from test_auth_sessions import (
    app,
    beacn_app,
    create_user,
    login,
)


def authenticated_client(app):
    create_user()
    client = app.test_client()
    login(client)
    return client


def insert_device(
    ip,
    device_type,
    *,
    display_name=None,
    agent_available=0,
):
    now = datetime.now(timezone.utc).isoformat()
    with beacn_app.db() as conn:
        conn.execute("""
            INSERT INTO devices (
                id, ip, hostname, display_name, is_online,
                agent_available, device_type, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
        """, (
            f"id-{ip}", ip, f"host-{ip}", display_name,
            agent_available, device_type, now, now,
        ))
        conn.commit()


def insert_infrastructure(
    object_id,
    name,
    infrastructure_type,
    *,
    parent_ref=None,
    connection_method="wired",
):
    now = datetime.now(timezone.utc).isoformat()
    with beacn_app.db() as conn:
        conn.execute("""
            INSERT INTO infrastructure_objects (
                id, name, infrastructure_type, parent_ref,
                connection_method, interfaces_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '[]', ?, ?)
        """, (
            object_id, name, infrastructure_type, parent_ref,
            connection_method, now, now,
        ))
        conn.commit()


def synthetic_graph():
    insert_device("192.0.2.1", "router", display_name="Example Router")
    insert_device("192.0.2.20", "nas", display_name="Example Storage")
    insert_device("192.0.2.30", "phone", display_name="Example Handset")
    insert_infrastructure("internet", "Internet", "internet", connection_method="virtual")
    insert_infrastructure(
        "gateway", "Example Gateway", "isp_gateway",
        parent_ref="infra:internet",
    )
    insert_infrastructure(
        "switch", "Example Switch", "switch",
        parent_ref="device:192.0.2.1",
    )


def test_relationships_route_methods(app):
    rule = next(
        rule for rule in app.url_map.iter_rules()
        if rule.rule == "/api/relationships"
    )
    assert set(rule.methods) == {"GET", "HEAD", "OPTIONS"}


@pytest.mark.parametrize("method", ("get", "head"))
def test_relationships_requires_authentication(app, method):
    create_user()
    response = getattr(app.test_client(), method)("/api/relationships")

    assert response.status_code == 401
    if method == "get":
        assert response.get_json() == {
            "ok": False,
            "error": "Authentication required.",
        }


def test_relationships_api_contract_and_unresolved_handling(app):
    synthetic_graph()
    response = authenticated_client(app).get("/api/relationships")

    assert response.status_code == 200
    assert response.is_json
    payload = response.get_json()
    assert set(payload) == {
        "ok", "engine", "summary", "providers",
        "relationships", "unresolved",
    }
    assert payload["ok"] is True
    assert payload["engine"] == {
        "name": "BEACN Relationship Manager",
        "mode": "evidence_driven",
        "status": "healthy",
    }
    assert payload["summary"] == {
        "relationships": 4,
        "device_relationships": 2,
        "infrastructure_relationships": 2,
        "unresolved_devices": 1,
        "unresolved_endpoints": 1,
        "unresolved_access_points": 0,
        "core_services_without_parent": 0,
        "infrastructure_objects": 3,
        "providers": 2,
        "evidence_items": 4,
    }
    assert payload["providers"] == [
        {
            "name": "infrastructure",
            "label": "Infrastructure hierarchy",
            "status": "healthy",
            "evidence_count": 2,
            "relationship_count": 2,
        },
        {
            "name": "generic",
            "label": "Generic inference",
            "status": "healthy",
            "evidence_count": 2,
            "relationship_count": 2,
        },
    ]

    assert [item["subject_ref"] for item in payload["relationships"]] == [
        "device:192.0.2.1",
        "device:192.0.2.20",
        "infra:gateway",
        "infra:switch",
    ]
    nas = next(
        item for item in payload["relationships"]
        if item["subject_ref"] == "device:192.0.2.20"
    )
    assert nas["parent_ref"] == "infra:switch"
    assert nas["subject"]["ip"] == "192.0.2.20"
    assert nas["subject"]["object_kind"] == "device"
    assert nas["parent"]["object_kind"] == "infrastructure"
    assert nas["transport"] == "wired"
    assert nas["provider"] == "generic"
    assert nas["provider_label"] == "Generic inference"
    assert nas["confidence"] == 65
    assert nas["reason"] == "strong_wired_endpoint"
    assert nas["placement"] == "automatic"
    assert nas["evidence"] == [{
        "provider": "generic",
        "provider_label": "Generic inference",
        "parent_ref": "infra:switch",
        "parent": nas["parent"],
        "transport": "wired",
        "confidence": 65,
        "reason": "strong_wired_endpoint",
        "reason_label": "Strong wired endpoint evidence",
    }]
    assert payload["unresolved"] == [{
        "ref": "device:192.0.2.30",
        "object_kind": "device",
        "name": "Example Handset",
        "ip": "192.0.2.30",
        "hostname": "host-192.0.2.30",
        "device_type": "phone",
        "vendor": "",
        "is_online": True,
        "agent_available": False,
        "presentation_role": "endpoint",
    }]


def test_missing_relationship_parent_is_represented_as_unknown(app):
    insert_infrastructure(
        "orphan", "Orphan", "switch",
        parent_ref="infra:deleted",
    )

    payload = authenticated_client(app).get(
        "/api/relationships"
    ).get_json()
    relationship = payload["relationships"][0]

    assert relationship["subject_ref"] == "infra:orphan"
    assert relationship["parent_ref"] == "infra:deleted"
    assert relationship["parent"] == {
        "ref": "infra:deleted",
        "object_kind": "unknown",
        "name": "infra:deleted",
        "presentation_role": "unknown",
    }
    assert relationship["confidence"] == 100
