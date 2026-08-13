import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from beacn.management import ManagementRepository
from beacn.security import CredentialCipher, load_credential_key_ring
from beacn.security.access_log import SanitizedAccessLogRequestHandler
from cryptography.fernet import Fernet
from test_auth_sessions import app as auth_app_fixture  # noqa: F401
from test_auth_sessions import beacn_app, create_user, login
from werkzeug.serving import make_server

SYNTHETIC_SECRETS = {
    "username_password": {
        "username": "synthetic-user",
        "password": "synthetic-password-marker",
    },
    "ssh_private_key": {
        "username": "synthetic-ssh-user",
        "private_key": "synthetic-private-key-marker",
        "passphrase": "synthetic-passphrase-marker",
    },
    "snmp_v2_community": {"community": "synthetic-community-marker"},
    "snmp_v3": {
        "username": "synthetic-snmp-user",
        "auth_password": "synthetic-auth-marker",
        "priv_password": "synthetic-privacy-marker",
    },
    "api_token": {"token": "synthetic-token-marker"},
}


@pytest.fixture
def management_app(auth_app_fixture, monkeypatch):  # noqa: F811
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("BEACN_ENCRYPTION_KEY", key)
    monkeypatch.delenv("BEACN_ENCRYPTION_KEY_FILE", raising=False)
    monkeypatch.delenv("BEACN_ENCRYPTION_LEGACY_KEYS", raising=False)

    now = datetime.now(timezone.utc).isoformat()
    device_id = str(uuid4())
    infrastructure_id = str(uuid4())
    with beacn_app.db() as conn:
        conn.execute(
            """
            INSERT INTO devices (id, ip, hostname, first_seen, last_seen)
            VALUES (?, '192.0.2.10', 'device.example.invalid', ?, ?)
            """,
            (device_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO infrastructure_objects (
                id, name, infrastructure_type, connection_method,
                interfaces_json, created_at, updated_at
            ) VALUES (?, 'Synthetic switch', 'switch', 'wired', '[]', ?, ?)
            """,
            (infrastructure_id, now, now),
        )
    auth_app_fixture.config["MANAGEMENT_TEST_DEVICE_ID"] = device_id
    auth_app_fixture.config["MANAGEMENT_TEST_INFRASTRUCTURE_ID"] = infrastructure_id
    return auth_app_fixture


def authenticated_client(application):
    create_user()
    client = application.test_client()
    login(client)
    return client


def csrf(client):
    response = client.get("/api/management/csrf")
    assert response.status_code == 200
    return response.get_json()["csrf_token"]


def mutation(client, method, path, *, payload=None, token=None, query=""):
    headers = {}
    if token is not None:
        headers["X-CSRF-Token"] = token
    return client.open(
        path + query,
        method=method,
        json=payload,
        headers=headers,
    )


def create_credential(client, token, credential_type="api_token"):
    return mutation(
        client,
        "POST",
        "/api/management/credentials",
        token=token,
        payload={
            "credential_type": credential_type,
            "label": f"Synthetic {credential_type}",
            "secret": SYNTHETIC_SECRETS[credential_type],
        },
    )


def create_source(client, token, application, **changes):
    payload = {
        "participant_kind": "device",
        "participant_id": application.config["MANAGEMENT_TEST_DEVICE_ID"],
        "adapter_type": "generic_network",
        "management_address": "router.example.invalid",
        "management_port": 8443,
        "enabled": False,
        "connection_timeout_seconds": 5,
        "capabilities": {"interface_inventory": True},
    }
    payload.update(changes)
    return mutation(
        client,
        "POST",
        "/api/management/sources",
        token=token,
        payload=payload,
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/management/sources"),
        ("POST", "/api/management/sources"),
        ("GET", "/api/management/credentials"),
        ("POST", "/api/management/credentials"),
    ],
)
def test_management_routes_require_authentication(management_app, method, path):
    create_user()
    response = management_app.test_client().open(path, method=method, json={})
    assert response.status_code == 401
    assert "csrf" not in response.get_data(as_text=True).lower()


def test_csrf_requires_authentication_and_admin(management_app):
    create_user()
    assert management_app.test_client().get("/api/management/csrf").status_code == 401

    client = management_app.test_client()
    login(client)
    with beacn_app.db() as conn:
        conn.execute("UPDATE auth_users SET is_admin = 0")
    assert client.get("/api/management/csrf").status_code == 403


def test_csrf_session_binding_logout_and_safe_methods(management_app):
    client = authenticated_client(management_app)
    token = csrf(client)
    second = management_app.test_client()
    login(second)

    assert client.get("/api/management/sources").status_code == 200
    assert (
        mutation(client, "POST", "/api/management/sources", payload={}).status_code
        == 403
    )
    assert (
        mutation(
            client, "POST", "/api/management/sources", payload={}, token="incorrect"
        ).status_code
        == 403
    )
    assert (
        mutation(
            second, "POST", "/api/management/sources", payload={}, token=token
        ).status_code
        == 403
    )
    assert (
        mutation(
            client,
            "POST",
            "/api/management/sources",
            payload={},
            query=f"?_csrf={token}",
        ).status_code
        == 403
    )
    assert (
        mutation(
            client, "POST", "/api/management/sources", payload={}, token="\x00invalid"
        ).status_code
        == 403
    )

    logout = client.post("/logout", data={"_csrf": token})
    assert logout.status_code == 302
    response = mutation(
        client, "POST", "/api/management/sources", payload={}, token=token
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("query", "markers"),
    [
        ("csrf_token=plain-access-log-marker", ["plain-access-log-marker"]),
        ("csrf_token=url%2Dencoded%2Dmarker", ["url-encoded-marker"]),
        ("csrf%5Ftoken=encoded-name-marker", ["encoded-name-marker"]),
        ("CsRf_ToKeN=mixed-case-marker", ["mixed-case-marker"]),
        (
            "csrf=duplicate-one-marker&csrf=duplicate-two-marker",
            ["duplicate-one-marker", "duplicate-two-marker"],
        ),
        ("limit=10&token=between-marker&view=summary", ["between-marker"]),
        ("token=", []),
        ("secret=percent%2Dencoded%2Dsecret", ["percent-encoded-secret"]),
        ("password=%ZZodd-encoding-marker", ["odd-encoding-marker"]),
        ("api_token=api-marker", ["api-marker"]),
        ("community=community-marker", ["community-marker"]),
        ("passphrase=passphrase-marker", ["passphrase-marker"]),
    ],
)
def test_werkzeug_access_log_redacts_management_query_secrets(
    management_app, caplog, query, markers
):
    client = authenticated_client(management_app)
    cookie = client.get_cookie("session").value
    caplog.set_level(logging.INFO, logger="werkzeug")
    server = make_server(
        "127.0.0.1",
        0,
        management_app,
        request_handler=SanitizedAccessLogRequestHandler,
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        request_value = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}"
            f"/api/management/sources?{query}",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Cookie": f"session={cookie}",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request_value, timeout=5)
        assert caught.value.code == 403
        assert json.loads(caught.value.read())["error"]["code"] == "csrf_failed"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert "[REDACTED]" in caplog.text
    for marker in markers:
        assert marker not in caplog.text


def test_werkzeug_access_log_preserves_harmless_management_query(management_app, caplog):
    client = authenticated_client(management_app)
    cookie = client.get_cookie("session").value
    caplog.set_level(logging.INFO, logger="werkzeug")
    server = make_server(
        "127.0.0.1",
        0,
        management_app,
        request_handler=SanitizedAccessLogRequestHandler,
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        request_value = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}"
            "/api/management/sources?limit=10&view=summary",
            headers={"Cookie": f"session={cookie}"},
        )
        with urllib.request.urlopen(request_value, timeout=5) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert "?limit=10&view=summary" in caplog.text


@pytest.mark.parametrize("credential_type", sorted(SYNTHETIC_SECRETS))
def test_credential_types_are_write_only_and_sanitized(
    management_app, credential_type, caplog
):
    client = authenticated_client(management_app)
    token = csrf(client)
    caplog.set_level(logging.INFO)
    response = create_credential(client, token, credential_type)

    assert response.status_code == 201
    body = response.get_json()
    credential = body["credential"]
    assert set(credential) == {
        "id",
        "credential_type",
        "label",
        "created_at",
        "updated_at",
        "last_rotated_at",
        "configured",
    }
    serialized = json.dumps(body)
    captured = caplog.text
    for value in SYNTHETIC_SECRETS[credential_type].values():
        assert value not in serialized
        assert value not in captured
    assert "encrypted_payload" not in serialized

    listed = client.get("/api/management/credentials").get_json()["credentials"]
    fetched = client.get(f"/api/management/credentials/{credential['id']}").get_json()[
        "credential"
    ]
    assert listed == [credential]
    assert fetched == credential


def test_empty_lists_and_nonexistent_credentials(management_app):
    client = authenticated_client(management_app)
    token = csrf(client)
    assert client.get("/api/management/credentials").get_json()["credentials"] == []
    missing = str(uuid4())
    assert client.get(f"/api/management/credentials/{missing}").status_code == 404
    assert (
        mutation(
            client,
            "PUT",
            f"/api/management/credentials/{missing}",
            token=token,
            payload={"secret": {"token": "marker"}},
        ).status_code
        == 404
    )
    assert (
        mutation(
            client, "DELETE", f"/api/management/credentials/{missing}", token=token
        ).status_code
        == 404
    )


def test_credential_validation_malformed_json_and_no_key_are_sanitized(
    management_app, monkeypatch, caplog
):
    client = authenticated_client(management_app)
    token = csrf(client)
    marker = "synthetic-do-not-echo-marker"
    caplog.set_level(logging.INFO)

    malformed = client.post(
        "/api/management/credentials",
        data="{",
        content_type="application/json",
        headers={"X-CSRF-Token": token},
    )
    assert malformed.status_code == 400
    invalid = mutation(
        client,
        "POST",
        "/api/management/credentials",
        token=token,
        payload={
            "credential_type": "api_token",
            "label": "Synthetic",
            "secret": {"unknown": marker},
        },
    )
    assert invalid.status_code == 400
    unsupported = mutation(
        client,
        "POST",
        "/api/management/credentials",
        token=token,
        payload={
            "credential_type": "unknown",
            "label": "Synthetic",
            "secret": {"token": marker},
        },
    )
    assert unsupported.status_code == 400
    assert marker not in invalid.get_data(as_text=True)
    assert marker not in unsupported.get_data(as_text=True)
    assert marker not in caplog.text

    monkeypatch.delenv("BEACN_ENCRYPTION_KEY", raising=False)
    locked = create_credential(client, token)
    assert locked.status_code == 503
    assert locked.get_json()["error"]["code"] == "encryption_unavailable"


def test_unexpected_repository_error_is_sanitized(management_app, monkeypatch, caplog):
    client = authenticated_client(management_app)
    token = csrf(client)
    marker = "synthetic-internal-database-marker"

    def fail(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(ManagementRepository, "create_credential", fail)
    caplog.set_level(logging.INFO)
    response = create_credential(client, token)
    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "internal_error"
    assert marker not in response.get_data(as_text=True)
    assert marker not in caplog.text


def test_oversized_management_payload_is_rejected_before_json_parsing(management_app):
    client = authenticated_client(management_app)
    token = csrf(client)
    response = client.post(
        "/api/management/credentials",
        data=b"x" * (1024 * 1024 + 1),
        content_type="application/json",
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"
    assert client.get("/api/management/credentials").get_json()["credentials"] == []


def test_credential_rotation_is_atomic_and_type_is_immutable(management_app):
    client = authenticated_client(management_app)
    token = csrf(client)
    created = create_credential(client, token).get_json()["credential"]
    credential_id = created["id"]

    rotated = mutation(
        client,
        "PUT",
        f"/api/management/credentials/{credential_id}",
        token=token,
        payload={"secret": {"token": "synthetic-rotated-marker"}},
    )
    assert rotated.status_code == 200
    assert rotated.get_json()["credential"]["last_rotated_at"] is not None

    invalid = mutation(
        client,
        "PUT",
        f"/api/management/credentials/{credential_id}",
        token=token,
        payload={"secret": {"wrong": "synthetic-invalid-marker"}},
    )
    assert invalid.status_code == 400
    repository = ManagementRepository(
        beacn_app.db, CredentialCipher(load_credential_key_ring())
    )
    assert repository.decrypt_credential(credential_id) == {
        "token": "synthetic-rotated-marker"
    }
    type_change = mutation(
        client,
        "PUT",
        f"/api/management/credentials/{credential_id}",
        token=token,
        payload={"credential_type": "snmp_v2_community", "secret": {}},
    )
    assert type_change.status_code == 400


def test_referenced_delete_conflict_shared_reference_and_source_cleanup(management_app):
    client = authenticated_client(management_app)
    token = csrf(client)
    credential = create_credential(client, token).get_json()["credential"]
    first = create_source(
        client, token, management_app, credential_id=credential["id"]
    ).get_json()["source"]
    second = create_source(
        client,
        token,
        management_app,
        participant_kind="infrastructure_object",
        participant_id=management_app.config["MANAGEMENT_TEST_INFRASTRUCTURE_ID"],
        management_address="switch.example.invalid",
        credential_id=credential["id"],
    ).get_json()["source"]

    conflict = mutation(
        client,
        "DELETE",
        f"/api/management/credentials/{credential['id']}",
        token=token,
    )
    assert conflict.status_code == 409
    assert (
        mutation(
            client, "DELETE", f"/api/management/sources/{first['id']}", token=token
        ).status_code
        == 204
    )
    assert (
        client.get(f"/api/management/credentials/{credential['id']}").status_code == 200
    )
    assert (
        mutation(
            client, "DELETE", f"/api/management/sources/{second['id']}", token=token
        ).status_code
        == 204
    )
    assert (
        mutation(
            client,
            "DELETE",
            f"/api/management/credentials/{credential['id']}",
            token=token,
        ).status_code
        == 204
    )


def test_source_create_list_get_capabilities_and_serialization(management_app):
    client = authenticated_client(management_app)
    token = csrf(client)
    assert client.get("/api/management/sources").get_json()["sources"] == []
    response = create_source(client, token, management_app)
    assert response.status_code == 201
    source = response.get_json()["source"]
    assert source["participant_kind"] == "device"
    assert source["capabilities"] == {"interface_inventory": True}
    assert source["credential"] is None
    assert source["ssh_trusted"] is False
    assert client.get("/api/management/sources").get_json()["sources"] == [source]
    assert (
        client.get(f"/api/management/sources/{source['id']}").get_json()["source"]
        == source
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"participant_kind": "unknown"},
        {"participant_id": "not-a-canonical-participant"},
        {"management_address": "https://invalid.example"},
        {"connection_timeout_seconds": 31},
        {"adapter_type": "bad adapter"},
        {"credential_id": "missing-credential"},
        {"capabilities": {"unknown": True}},
        {"capabilities": {"bridge_fdb": "yes"}},
        {"shell_command": "synthetic-command-marker"},
    ],
)
def test_source_validation_rejects_unsafe_input(management_app, changes):
    client = authenticated_client(management_app)
    response = create_source(client, csrf(client), management_app, **changes)
    assert response.status_code == 400
    assert "synthetic-command-marker" not in response.get_data(as_text=True)


def test_source_and_capabilities_are_atomic_on_validation_failure(management_app):
    client = authenticated_client(management_app)
    response = create_source(
        client,
        csrf(client),
        management_app,
        capabilities={"interface_inventory": True, "unknown": True},
    )
    assert response.status_code == 400
    with beacn_app.db() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM management_sources").fetchone()[0] == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM management_source_capabilities"
            ).fetchone()[0]
            == 0
        )


def test_source_patch_capabilities_and_ssh_trust_lifecycle(management_app):
    client = authenticated_client(management_app)
    token = csrf(client)
    source = create_source(client, token, management_app).get_json()["source"]
    repository = ManagementRepository(
        beacn_app.db, CredentialCipher(load_credential_key_ring())
    )
    repository.set_ssh_trust(
        source["id"],
        algorithm="ssh-ed25519",
        fingerprint="SHA256:synthetic-fingerprint",
    )

    unrelated = mutation(
        client,
        "PATCH",
        f"/api/management/sources/{source['id']}",
        token=token,
        payload={
            "enabled": True,
            "capabilities": {"bridge_fdb": True, "interface_inventory": False},
        },
    ).get_json()["source"]
    assert unrelated["ssh_trusted"] is True
    assert unrelated["capabilities"] == {
        "bridge_fdb": True,
        "interface_inventory": False,
    }

    address = mutation(
        client,
        "PATCH",
        f"/api/management/sources/{source['id']}",
        token=token,
        payload={"management_address": "replacement.example.invalid"},
    ).get_json()["source"]
    assert address["ssh_trusted"] is False
    repository.set_ssh_trust(
        source["id"],
        algorithm="ssh-ed25519",
        fingerprint="SHA256:replacement-fingerprint",
    )
    port = mutation(
        client,
        "PATCH",
        f"/api/management/sources/{source['id']}",
        token=token,
        payload={"management_port": 9443},
    ).get_json()["source"]
    assert port["ssh_trusted"] is False


def test_source_delete_cascades_capabilities_but_not_credential(management_app):
    client = authenticated_client(management_app)
    token = csrf(client)
    credential = create_credential(client, token).get_json()["credential"]
    source = create_source(
        client, token, management_app, credential_id=credential["id"]
    ).get_json()["source"]
    assert (
        mutation(
            client, "DELETE", f"/api/management/sources/{source['id']}", token=token
        ).status_code
        == 204
    )
    with beacn_app.db() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM management_source_capabilities WHERE source_id = ?",
                (source["id"],),
            ).fetchone()[0]
            == 0
        )
    assert (
        client.get(f"/api/management/credentials/{credential['id']}").status_code == 200
    )
    assert client.get(f"/api/management/sources/{source['id']}").status_code == 404


def test_existing_api_auth_behavior_and_settings_post_remain_unchanged(management_app):
    create_user()
    anonymous = management_app.test_client()
    assert anonymous.get("/api/devices").status_code == 401

    client = management_app.test_client()
    login(client)
    assert client.get("/api/health").status_code == 200
    page = client.get("/settings")
    assert page.status_code == 200
    token = csrf(client)
    saved = client.post(
        "/settings",
        data={
            "_csrf": token,
            "action": "session_timeout",
            "session_timeout_hours": "8",
        },
    )
    assert saved.status_code == 200
