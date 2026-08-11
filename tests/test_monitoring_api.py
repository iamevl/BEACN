from datetime import datetime, timezone

import pytest

from test_auth_sessions import (
    app,
    beacn_app,
    create_user,
    login,
)
from beacn.services import commands


MONITORING_ROUTES = {
    "/api/health",
    "/api/docker",
    "/api/docker/<target>",
}


@pytest.fixture(autouse=True)
def documentation_network(monkeypatch):
    subnet = "192.0.2.0/24"
    monkeypatch.setenv("NETWORK_SUBNET", subnet)
    monkeypatch.setattr(
        commands,
        "NETWORK_SUBNET",
        subnet,
    )


def authenticated_client(app):
    create_user()
    client = app.test_client()
    login(client)
    return client


def unavailable_contract(payload, source):
    assert payload["available"] is False
    assert payload["source"] == source
    assert payload["error"] == (
        "Docker telemetry is currently unavailable."
    )
    assert payload["containers"] == []
    assert payload["engine"] == {
        "containers_total": 0,
        "containers_running": 0,
        "containers_stopped": 0,
        "containers_healthy": 0,
        "containers_unhealthy": 0,
    }
    assert payload["collected_at"]


def insert_device(
    *,
    target,
    agent_available,
    agent_hostname="",
):
    now = datetime.now(timezone.utc).isoformat()

    with beacn_app.db() as conn:
        conn.execute("""
            INSERT INTO devices (
                ip,
                hostname,
                is_online,
                agent_available,
                agent_hostname,
                first_seen,
                last_seen
            )
            VALUES (?, ?, 1, ?, ?, ?, ?)
        """, (
            target,
            "synthetic-device",
            int(agent_available),
            agent_hostname,
            now,
            now,
        ))
        conn.commit()


def test_monitoring_route_map_contract(app):
    rules = {
        rule.rule: set(rule.methods)
        for rule in app.url_map.iter_rules()
        if rule.rule in MONITORING_ROUTES
    }

    assert rules == {
        path: {"GET", "HEAD", "OPTIONS"}
        for path in MONITORING_ROUTES
    }


@pytest.mark.parametrize(
    "path",
    (
        "/api/health",
        "/api/docker",
        "/api/docker/192.0.2.10",
    ),
)
def test_monitoring_routes_require_authentication(
    app,
    path,
):
    create_user()
    response = app.test_client().get(path)

    assert response.status_code == 401
    assert response.is_json
    assert response.get_json() == {
        "ok": False,
        "error": "Authentication required.",
    }


def test_health_authenticated_success_contract(
    app,
    monkeypatch,
):
    client = authenticated_client(app)
    expected = {
        "greeting": "Good morning",
        "score": 100,
        "status": "healthy",
        "summary": "Synthetic health summary.",
        "checked_at": "2026-01-01T00:00:00+00:00",
        "checks": [],
        "counts": {
            "devices_total": 0,
            "devices_online": 0,
            "devices_offline": 0,
            "agents_managed": 0,
            "agents_online": 0,
            "containers_total": 0,
            "containers_running": 0,
            "containers_unhealthy": 0,
        },
    }
    monkeypatch.setattr(
        beacn_app,
        "get_health_summary",
        lambda: expected,
    )

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == expected


def test_local_docker_authenticated_success_contract(
    app,
    monkeypatch,
):
    client = authenticated_client(app)
    snapshot = {
        "available": True,
        "engine": {
            "name": "synthetic-engine",
            "server_version": "test",
            "operating_system": "test",
            "architecture": "test",
            "containers_total": 1,
            "containers_running": 1,
            "containers_stopped": 0,
            "containers_healthy": 1,
            "containers_unhealthy": 0,
        },
        "containers": [{"name": "synthetic-container"}],
    }
    monkeypatch.setattr(
        beacn_app,
        "docker_snapshot",
        lambda: snapshot,
    )

    response = client.get("/api/docker")
    payload = response.get_json()

    assert response.status_code == 200
    assert response.is_json
    assert payload["available"] is True
    assert payload["source"] == "dashboard-host"
    assert payload["engine"] == snapshot["engine"]
    assert payload["containers"] == snapshot["containers"]


def test_local_docker_authenticated_failure_contract(
    app,
    monkeypatch,
):
    client = authenticated_client(app)

    def unavailable():
        raise RuntimeError("synthetic Docker failure")

    monkeypatch.setattr(
        beacn_app,
        "docker_snapshot",
        unavailable,
    )

    response = client.get("/api/docker")

    assert response.status_code == 200
    assert response.is_json
    unavailable_contract(
        response.get_json(),
        "dashboard-host",
    )


def test_device_docker_target_validation_contract(app):
    client = authenticated_client(app)

    invalid = client.get("/api/docker/not-an-ip")
    missing = client.get("/api/docker/192.0.2.240")

    assert invalid.status_code == 400
    unavailable_contract(invalid.get_json(), "agent")
    assert missing.status_code == 404
    unavailable_contract(missing.get_json(), "agent")


def test_device_without_agent_is_unavailable(app):
    client = authenticated_client(app)
    insert_device(
        target="192.0.2.241",
        agent_available=False,
    )

    response = client.get("/api/docker/192.0.2.241")

    assert response.status_code == 200
    unavailable_contract(response.get_json(), "agent")


def test_device_agent_docker_success_contract(
    app,
    monkeypatch,
):
    client = authenticated_client(app)
    insert_device(
        target="192.0.2.242",
        agent_available=True,
        agent_hostname="synthetic-agent",
    )
    agent_payload = {
        "available": True,
        "engine": {"containers_total": 1},
        "containers": [{"name": "synthetic-container"}],
    }
    calls = []

    def fetch_agent(target, path):
        calls.append((target, path))
        return dict(agent_payload)

    monkeypatch.setattr(
        beacn_app,
        "fetch_agent_json",
        fetch_agent,
    )

    response = client.get("/api/docker/192.0.2.242")
    payload = response.get_json()

    assert response.status_code == 200
    assert calls == [("192.0.2.242", "/docker")]
    assert payload["available"] is True
    assert payload["source"] == "agent"
    assert payload["target_ip"] == "192.0.2.242"
    assert payload["target_hostname"] == (
        "synthetic-agent"
    )
    assert payload["engine"] == agent_payload["engine"]
    assert payload["containers"] == (
        agent_payload["containers"]
    )


def test_device_agent_docker_failure_contract(
    app,
    monkeypatch,
):
    client = authenticated_client(app)
    insert_device(
        target="192.0.2.243",
        agent_available=True,
        agent_hostname="synthetic-agent",
    )
    monkeypatch.setattr(
        beacn_app,
        "fetch_agent_json",
        lambda _target, _path: None,
    )

    response = client.get("/api/docker/192.0.2.243")

    assert response.status_code == 200
    unavailable_contract(response.get_json(), "agent")
