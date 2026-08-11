import json
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest

from test_auth_sessions import (
    beacn_app,
    create_user,
    login,
)
from beacn import common
from beacn import runtime
from beacn.database import Database
from beacn.services import agent, commands, health, scanner


OPERATIONS_ROUTES = {
    "/api/scan": {"POST", "OPTIONS"},
    "/api/ping": {"POST", "OPTIONS"},
    "/api/ports": {"POST", "OPTIONS"},
    "/api/iperf": {"POST", "OPTIONS"},
    "/api/results": {"GET", "HEAD", "OPTIONS"},
}

VALID_TARGET = "192.0.2.25"
OTHER_TARGET = "192.0.2.26"
OUT_OF_SCOPE_TARGET = "198.51.100.25"


@pytest.fixture
def app(tmp_path, monkeypatch):
    database = Database(tmp_path / "beacn.db")
    monkeypatch.setattr(common, "database", database)
    monkeypatch.setattr(beacn_app, "db", common.db)
    monkeypatch.setattr(health, "db", common.db)
    monkeypatch.setattr(
        commands,
        "NETWORK_SUBNET",
        "192.0.2.0/24",
    )

    beacn_app.app.config.update(
        TESTING=True,
        SESSION_COOKIE_SECURE=False,
        SESSION_REFRESH_EACH_REQUEST=True,
    )
    beacn_app.app.permanent_session_lifetime = timedelta(hours=8)
    beacn_app.init_db()

    return beacn_app.app


def authenticated_client(app):
    create_user()
    client = app.test_client()
    login(client)
    return client


def command_result(*, returncode=0, stdout="synthetic output", stderr=""):
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def forbid_activity(*_args, **_kwargs):
    raise AssertionError("Unexpected operational activity")


class OpenSocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def count_results():
    with common.db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM iperf_results"
        ).fetchone()[0]


def insert_result(*, target, index):
    with common.db() as conn:
        conn.execute(
            """
            INSERT INTO iperf_results (
                target_ip,
                direction,
                bits_per_second,
                retransmits,
                raw_output,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                target,
                "forward",
                float(index),
                index,
                f"raw-{index}",
                f"2026-01-01T00:00:{index:02d}+00:00",
            ),
        )


def test_operations_route_map_contract(app):
    rules = {
        rule.rule: set(rule.methods)
        for rule in app.url_map.iter_rules()
        if rule.rule in OPERATIONS_ROUTES
    }

    assert rules == OPERATIONS_ROUTES


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    (
        ("post", "/api/scan", {}),
        ("post", "/api/ping", {"target": VALID_TARGET}),
        ("post", "/api/ports", {"target": VALID_TARGET}),
        ("post", "/api/iperf", {"target": VALID_TARGET}),
        ("get", "/api/results", None),
    ),
)
def test_operations_routes_require_authentication_without_activity(
    app,
    monkeypatch,
    method,
    path,
    payload,
):
    create_user()
    monkeypatch.setattr(threading, "Thread", forbid_activity)
    monkeypatch.setattr(commands.subprocess, "run", forbid_activity)
    monkeypatch.setattr(agent.socket, "create_connection", forbid_activity)
    before = count_results()

    response = getattr(app.test_client(), method)(path, json=payload)

    assert response.status_code == 401
    assert response.get_json() == {
        "ok": False,
        "error": "Authentication required.",
    }
    assert count_results() == before


def test_scan_already_running_does_not_start_thread(app, monkeypatch):
    client = authenticated_client(app)
    monkeypatch.setitem(runtime.scan_state, "running", True)
    monkeypatch.setattr(threading, "Thread", forbid_activity)

    response = client.post("/api/scan", json={})

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "message": "A scan is already running.",
    }


def test_scan_idle_starts_daemon_thread_without_running_scan(app, monkeypatch):
    client = authenticated_client(app)
    monkeypatch.setitem(runtime.scan_state, "running", False)
    constructed = []

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon
            self.started = False
            constructed.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(threading, "Thread", FakeThread)

    response = client.post("/api/scan", json={})

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "message": "Network scan started.",
    }
    assert len(constructed) == 1
    assert constructed[0].target is scanner.scan_network
    assert constructed[0].daemon is True
    assert constructed[0].started is True
    assert runtime.scan_state["running"] is False


@pytest.mark.parametrize(
    "target",
    (None, "not-an-ip", OUT_OF_SCOPE_TARGET),
)
def test_ping_rejects_invalid_targets_without_command(
    app,
    monkeypatch,
    target,
):
    client = authenticated_client(app)
    monkeypatch.setattr(commands.subprocess, "run", forbid_activity)
    payload = {} if target is None else {"target": target}

    response = client.post("/api/ping", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "error": "Target is outside the configured subnet.",
    }


@pytest.mark.parametrize("returncode", (0, 1))
def test_ping_preserves_command_contract(app, monkeypatch, returncode):
    client = authenticated_client(app)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return command_result(
            returncode=returncode,
            stdout="pong" if returncode == 0 else "",
            stderr="ping failed" if returncode else "",
        )

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    response = client.post(
        "/api/ping",
        json={"target": 3221226009},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": returncode == 0,
        "returncode": returncode,
        "stdout": "pong" if returncode == 0 else "",
        "stderr": "ping failed" if returncode else "",
    }
    assert calls[0][0] == ["ping", "-c", "4", "-W", "2", VALID_TARGET]
    assert calls[0][1]["timeout"] == 12
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is True
    assert calls[0][1]["check"] is False


@pytest.mark.parametrize(
    "target",
    (None, "not-an-ip", OUT_OF_SCOPE_TARGET),
)
def test_ports_rejects_invalid_targets_without_command(
    app,
    monkeypatch,
    target,
):
    client = authenticated_client(app)
    monkeypatch.setattr(commands.subprocess, "run", forbid_activity)
    payload = {} if target is None else {"target": target}

    response = client.post("/api/ports", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "error": "Target is outside the configured subnet.",
    }


@pytest.mark.parametrize("returncode", (0, 1))
def test_ports_preserve_command_contract(app, monkeypatch, returncode):
    client = authenticated_client(app)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return command_result(
            returncode=returncode,
            stdout="ports" if returncode == 0 else "",
            stderr="nmap failed" if returncode else "",
        )

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    response = client.post(
        "/api/ports",
        json={"target": 3221226009},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": returncode == 0,
        "returncode": returncode,
        "stdout": "ports" if returncode == 0 else "",
        "stderr": "nmap failed" if returncode else "",
    }
    assert calls[0][0] == [
        "nmap",
        "-Pn",
        "-T4",
        "--top-ports",
        "100",
        VALID_TARGET,
    ]
    assert calls[0][1]["timeout"] == 45


@pytest.mark.parametrize(
    "target",
    (None, "not-an-ip", OUT_OF_SCOPE_TARGET),
)
def test_iperf_rejects_invalid_target_without_activity(
    app,
    monkeypatch,
    target,
):
    client = authenticated_client(app)
    monkeypatch.setattr(agent.socket, "create_connection", forbid_activity)
    monkeypatch.setattr(commands.subprocess, "run", forbid_activity)

    payload = {} if target is None else {"target": target}
    response = client.post("/api/iperf", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "error": "Target is outside the configured subnet.",
    }
    assert count_results() == 0


def test_iperf_closed_preflight_returns_400_without_command_or_write(
    app,
    monkeypatch,
):
    client = authenticated_client(app)

    def closed_socket(*_args, **_kwargs):
        raise OSError("synthetic closed port")

    monkeypatch.setattr(agent.socket, "create_connection", closed_socket)
    monkeypatch.setattr(commands.subprocess, "run", forbid_activity)

    response = client.post("/api/iperf", json={"target": VALID_TARGET})

    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "error": f"No iperf3 server detected on {VALID_TARGET}:5201.",
    }
    assert count_results() == 0


@pytest.mark.parametrize(
    ("reverse", "expected_direction", "reverse_arg"),
    (
        (False, "forward", []),
        ("truthy", "reverse", ["-R"]),
    ),
)
def test_iperf_success_persists_stdout_and_parsed_result(
    app,
    monkeypatch,
    reverse,
    expected_direction,
    reverse_arg,
):
    client = authenticated_client(app)
    calls = []
    socket_calls = []
    stdout = json.dumps({
        "end": {
            "sum_received": {
                "bits_per_second": 123456.5,
                "retransmits": 7,
            }
        }
    })

    def open_socket(address, timeout):
        socket_calls.append((address, timeout))
        return OpenSocket()

    monkeypatch.setattr(agent.socket, "create_connection", open_socket)

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return command_result(stdout=stdout)

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    response = client.post(
        "/api/iperf",
        json={"target": VALID_TARGET, "reverse": reverse},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["bits_per_second"] == 123456.5
    assert payload["retransmits"] == 7
    assert payload["direction"] == expected_direction
    assert calls[0][0] == [
        "iperf3",
        "-c",
        VALID_TARGET,
        "-p",
        "5201",
        "-J",
        "-t",
        "10",
        *reverse_arg,
    ]
    assert calls[0][1]["timeout"] == 25
    assert socket_calls == [((VALID_TARGET, 5201), 1)]

    with common.db() as conn:
        row = conn.execute(
            "SELECT * FROM iperf_results"
        ).fetchone()

    assert row["target_ip"] == VALID_TARGET
    assert row["direction"] == expected_direction
    assert row["bits_per_second"] == 123456.5
    assert row["retransmits"] == 7
    assert row["raw_output"] == stdout
    assert row["created_at"]


def test_iperf_command_failure_is_persisted_and_returned_with_200(
    app,
    monkeypatch,
):
    client = authenticated_client(app)
    monkeypatch.setattr(
        agent.socket,
        "create_connection",
        lambda *_args, **_kwargs: OpenSocket(),
    )
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda *_args, **_kwargs: command_result(
            returncode=1,
            stdout="",
            stderr="synthetic iperf failure",
        ),
    )

    response = client.post("/api/iperf", json={"target": VALID_TARGET})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["bits_per_second"] is None
    assert payload["retransmits"] is None
    assert payload["direction"] == "forward"

    with common.db() as conn:
        row = conn.execute(
            "SELECT * FROM iperf_results"
        ).fetchone()

    assert row["raw_output"] == "synthetic iperf failure"
    assert row["bits_per_second"] is None
    assert row["retransmits"] is None


@pytest.mark.parametrize("target_query", (None, "", "not-an-ip", OUT_OF_SCOPE_TARGET))
def test_results_unfiltered_contract(app, target_query):
    client = authenticated_client(app)

    for index in range(1, 56):
        insert_result(
            target=VALID_TARGET if index % 2 else OTHER_TARGET,
            index=index,
        )

    path = "/api/results"
    if target_query is not None:
        path += f"?target={target_query}"

    response = client.get(path)
    payload = response.get_json()
    results = payload["results"]

    assert response.status_code == 200
    assert set(payload) == {"results"}
    assert len(results) == 50
    assert [row["id"] for row in results] == list(range(55, 5, -1))


def test_results_valid_target_filters_newest_first_with_limit(app):
    client = authenticated_client(app)

    for index in range(1, 56):
        insert_result(target=VALID_TARGET, index=index)
    insert_result(target=OTHER_TARGET, index=56)

    response = client.get(f"/api/results?target={VALID_TARGET}")
    payload = response.get_json()
    results = payload["results"]

    assert response.status_code == 200
    assert set(payload) == {"results"}
    assert len(results) == 50
    assert all(row["target_ip"] == VALID_TARGET for row in results)
    assert [row["id"] for row in results] == list(range(55, 5, -1))
