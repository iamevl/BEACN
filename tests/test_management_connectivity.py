import base64
import hashlib
import inspect
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from beacn.management import ManagementRepository
from beacn.management.connectivity import (
    ConnectivityRateLimiter,
    ConnectivityResult,
    HostIdentity,
    ManagementConnectivityService,
    SSHTransport,
)
from beacn.security import (
    CredentialCipher,
    credential_cipher_from_environment,
    load_credential_key_ring,
)
from cryptography.fernet import Fernet
from test_auth_sessions import app as auth_app_fixture  # noqa: F401
from test_auth_sessions import beacn_app
from test_management_api import (
    SYNTHETIC_SECRETS,
    authenticated_client,
    create_credential,
    create_source,
    csrf,
    mutation,
)
from test_management_api import management_app as management_app_fixture  # noqa: F401


@pytest.fixture
def r2d3_app(request):
    return request.getfixturevalue("management_app_fixture")


def test_key_file_states_and_safe_cipher(tmp_path, monkeypatch):
    missing = tmp_path / "missing-key"
    assert not credential_cipher_from_environment(
        {"BEACN_ENCRYPTION_KEY_FILE": str(missing)}
    ).available

    empty = tmp_path / "empty-key"
    empty.write_text("")
    malformed = tmp_path / "malformed-key"
    malformed.write_text("synthetic-invalid-key")
    assert not credential_cipher_from_environment(
        {"BEACN_ENCRYPTION_KEY_FILE": str(empty)}
    ).available
    assert not credential_cipher_from_environment(
        {"BEACN_ENCRYPTION_KEY_FILE": str(malformed)}
    ).available

    valid = tmp_path / "valid-key"
    key = Fernet.generate_key().decode()
    valid.write_text(key + "\n")
    assert credential_cipher_from_environment(
        {"BEACN_ENCRYPTION_KEY_FILE": str(valid)}
    ).available

    def unreadable(*args, **kwargs):
        raise PermissionError

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert not credential_cipher_from_environment(
        {"BEACN_ENCRYPTION_KEY_FILE": str(valid)}
    ).available


def test_key_errors_never_contain_key_or_path(tmp_path):
    marker = "synthetic-key-material-marker"
    path = tmp_path / "synthetic-sensitive-path-marker"
    path.write_text(marker)
    cipher = credential_cipher_from_environment(
        {"BEACN_ENCRYPTION_KEY_FILE": str(path)}
    )
    assert not cipher.available
    output = repr(cipher)
    assert marker not in output
    assert str(path) not in output


def test_rate_limiter_is_per_admin_and_source_without_sleep():
    now = [100.0]
    limiter = ConnectivityRateLimiter(limit=2, window_seconds=10, clock=lambda: now[0])
    assert limiter.allow(1, "source-a")
    assert limiter.allow(1, "source-a")
    assert not limiter.allow(1, "source-a")
    assert limiter.allow(2, "source-a")
    assert limiter.allow(1, "source-b")
    now[0] += 11
    assert limiter.allow(1, "source-a")


class FakeRepository:
    def __init__(self, credential_type, secrets):
        self.credential = type("Credential", (), {"credential_type": credential_type})()
        self.secrets = secrets

    def get_credential(self, credential_id):
        return self.credential

    def decrypt_credential(self, credential_id):
        return self.secrets


class FakeSSH:
    def __init__(self, result=None, identity=None):
        self.result = result or ConnectivityResult("reachable", "reachable")
        self.presented = identity or HostIdentity("ssh-ed25519", "SHA256:synthetic")
        self.calls = []

    def authenticate(self, source, secrets, credential_type):
        self.calls.append((source.id, dict(secrets), credential_type))
        return self.result

    def identity(self, address, port, timeout):
        self.calls.append((address, port, timeout))
        return self.presented


def source_value(
    adapter="ssh", enabled=True, credential_id="credential", trusted=False
):
    from beacn.management import ManagementSource

    return ManagementSource(
        id=str(uuid4()),
        participant_kind="device",
        participant_id=str(uuid4()),
        adapter_type=adapter,
        management_address="device.example.invalid",
        management_port=None,
        enabled=enabled,
        credential_id=credential_id,
        connection_timeout_seconds=5,
        ssh_host_key_algorithm="ssh-ed25519" if trusted else None,
        ssh_host_key_fingerprint="SHA256:synthetic" if trusted else None,
        ssh_host_key_trusted_at="synthetic" if trusted else None,
        ssh_host_key_trusted_by=None,
        created_at="synthetic",
        updated_at="synthetic",
    )


def test_connectivity_service_categories_and_no_collection():
    ssh = FakeSSH(ConnectivityResult("reachable", "reachable"))
    service = ManagementConnectivityService(
        FakeRepository("username_password", SYNTHETIC_SECRETS["username_password"]),
        ssh_transport=ssh,
    )
    assert service.test(source_value(trusted=True)).category == "reachable"
    assert service.test(source_value(enabled=False)).category == "configuration_invalid"
    assert service.test(source_value(adapter="future_adapter")).category == "unsupported_adapter"
    assert len(ssh.calls) == 2


@pytest.mark.parametrize(
    ("credential_type", "available", "expected"),
    [
        ("snmp_v2_community", True, "reachable"),
        ("snmp_v3", False, "authentication_failed"),
    ],
)
def test_snmp_connectivity_uses_only_system_probe(credential_type, available, expected):
    calls = []

    def probe(target, **kwargs):
        calls.append((target, kwargs))
        return {"available": available, "error": "authentication failed"}

    service = ManagementConnectivityService(
        FakeRepository(credential_type, SYNTHETIC_SECRETS[credential_type]),
        snmp_probe=probe,
    )
    assert service.test(source_value(adapter="snmp")).category == expected
    assert len(calls) == 1
    assert "walk" not in inspect.getsource(ManagementConnectivityService._test_snmp)


def _install_fake_connectivity(monkeypatch, result, identity=None):
    class FakeConnectivity:
        def __init__(self, repository):
            self.repository = repository

        def test(self, source):
            return result

        def candidate_identity(self, source):
            return identity or HostIdentity("ssh-ed25519", "SHA256:presented")

    monkeypatch.setattr(beacn_app, "ManagementConnectivityService", FakeConnectivity)
    beacn_app.management_connectivity_limiter._attempts.clear()


def _persist_source(client, token, app):
    credential = create_credential(client, token).get_json()["credential"]
    response = create_source(
        client,
        token,
        app,
        adapter_type="ssh",
        management_port=22,
        enabled=True,
        credential_id=credential["id"],
    )
    return response.get_json()["source"]


@pytest.mark.parametrize(
    "category",
    [
        "reachable",
        "timeout",
        "connection_refused",
        "authentication_failed",
        "host_identity_untrusted",
        "host_identity_changed",
        "unsupported_adapter",
        "encryption_unavailable",
        "configuration_invalid",
        "internal_failure",
    ],
)
def test_connectivity_endpoint_categories_are_sanitized(
    r2d3_app, monkeypatch, category
):
    client = authenticated_client(r2d3_app)
    token = csrf(client)
    source = _persist_source(client, token, r2d3_app)
    _install_fake_connectivity(
        monkeypatch,
        ConnectivityResult(category, "Sanitized connectivity result."),
    )
    response = mutation(
        client,
        "POST",
        f"/api/management/sources/{source['id']}/test",
        token=token,
        payload={},
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["category"] == category
    with beacn_app.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_connectivity_endpoint_rejects_commands_and_rate_limits(
    r2d3_app, monkeypatch
):
    client = authenticated_client(r2d3_app)
    token = csrf(client)
    source = _persist_source(client, token, r2d3_app)
    _install_fake_connectivity(
        monkeypatch, ConnectivityResult("reachable", "reachable")
    )
    command = mutation(
        client,
        "POST",
        f"/api/management/sources/{source['id']}/test",
        token=token,
        payload={"command": "synthetic-command-marker"},
    )
    assert command.status_code == 400
    assert "synthetic-command-marker" not in command.get_data(as_text=True)
    statuses = [
        mutation(
            client,
            "POST",
            f"/api/management/sources/{source['id']}/test",
            token=token,
            payload={},
        ).status_code
        for _ in range(6)
    ]
    assert statuses == [200, 200, 200, 200, 200, 429]


def test_new_connectivity_routes_require_authentication_and_csrf(r2d3_app):
    missing = str(uuid4())
    anonymous = r2d3_app.test_client()
    assert (
        anonymous.post(f"/api/management/sources/{missing}/test", json={}).status_code
        == 401
    )
    assert (
        anonymous.post(f"/api/management/sources/{missing}/trust", json={}).status_code
        == 401
    )
    client = authenticated_client(r2d3_app)
    assert (
        client.post(f"/api/management/sources/{missing}/test", json={}).status_code
        == 403
    )
    assert (
        client.post(f"/api/management/sources/{missing}/trust", json={}).status_code
        == 403
    )


def test_connectivity_with_missing_key_fails_closed(r2d3_app, monkeypatch):
    client = authenticated_client(r2d3_app)
    token = csrf(client)
    source = _persist_source(client, token, r2d3_app)
    repository = ManagementRepository(
        beacn_app.db,
        CredentialCipher(load_credential_key_ring()),
    )
    repository.set_ssh_trust(
        source["id"],
        algorithm="ssh-ed25519",
        fingerprint="SHA256:synthetic",
    )

    class LockedConnectivity(ManagementConnectivityService):
        def __init__(self, repository_value):
            super().__init__(repository_value, ssh_transport=FakeSSH())

    monkeypatch.setattr(beacn_app, "ManagementConnectivityService", LockedConnectivity)
    monkeypatch.delenv("BEACN_ENCRYPTION_KEY", raising=False)
    beacn_app.management_connectivity_limiter._attempts.clear()
    response = mutation(
        client,
        "POST",
        f"/api/management/sources/{source['id']}/test",
        token=token,
        payload={},
    )
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "encryption_unavailable"


def test_explicit_trust_rescans_endpoint_and_changed_identity_is_blocked(
    r2d3_app, monkeypatch
):
    client = authenticated_client(r2d3_app)
    token = csrf(client)
    source = _persist_source(client, token, r2d3_app)
    candidate = HostIdentity("ssh-ed25519", "SHA256:candidate")
    _install_fake_connectivity(
        monkeypatch,
        ConnectivityResult("host_identity_untrusted", "untrusted", candidate=candidate),
        candidate,
    )
    trusted = mutation(
        client,
        "POST",
        f"/api/management/sources/{source['id']}/trust",
        token=token,
        payload=candidate.to_dict(),
    )
    assert trusted.status_code == 200
    assert trusted.get_json()["source"]["ssh_trusted"] is True

    changed = mutation(
        client,
        "POST",
        f"/api/management/sources/{source['id']}/trust",
        token=token,
        payload={"algorithm": "ssh-ed25519", "fingerprint": "SHA256:old"},
    )
    assert changed.status_code == 409
    assert changed.get_json()["result"]["category"] == "host_identity_changed"
    with beacn_app.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_ssh_transport_has_no_shell_command_or_auto_acceptance():
    source = inspect.getsource(SSHTransport)
    forbidden = ["AutoAddPolicy", "exec_command", "invoke_shell", "shell=True"]
    assert not any(value in source for value in forbidden)


def test_ssh_transport_verifies_identity_authenticates_and_closes(monkeypatch):
    class AuthenticationException(Exception):
        pass

    class FakeParamiko:
        SSHException = RuntimeError

    FakeParamiko.AuthenticationException = AuthenticationException

    class Key:
        def get_name(self):
            return "ssh-ed25519"

        def asbytes(self):
            return b"synthetic-host-key"

    class Connection:
        closed = False

        def close(self):
            self.closed = True

    class Transport:
        def __init__(self):
            self.closed = False
            self.authenticated = False
            self.password_calls = []

        def auth_password(self, username, password):
            self.password_calls.append((username, password))
            self.authenticated = True

        def is_authenticated(self):
            return self.authenticated

        def close(self):
            self.closed = True

    connection = Connection()
    transport = Transport()
    ssh = SSHTransport()
    monkeypatch.setattr(ssh, "_paramiko", lambda: FakeParamiko)
    monkeypatch.setattr(ssh, "_open", lambda *args: (connection, transport, Key()))
    identity = HostIdentity(
        "ssh-ed25519",
        "SHA256:"
        + base64.b64encode(hashlib.sha256(b"synthetic-host-key").digest())
        .decode()
        .rstrip("="),
    )
    source = replace(
        source_value(trusted=True),
        ssh_host_key_algorithm=identity.algorithm,
        ssh_host_key_fingerprint=identity.fingerprint,
    )
    result = ssh.authenticate(
        source,
        SYNTHETIC_SECRETS["username_password"],
        "username_password",
    )
    assert result.category == "reachable"
    assert len(transport.password_calls) == 1
    assert connection.closed and transport.closed


def test_management_ui_does_not_persist_or_repopulate_secrets():
    script = Path("console/static/js/management-settings.js").read_text()
    template = Path("console/templates/settings.html").read_text()
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "console." not in script
    assert "secret form" not in template.casefold()
    assert "management-secret-fields" in template
    assert "resetCredentialForm();" in script


def test_management_ui_requires_explicit_endpoint_bound_ssh_trust_review():
    script = Path("console/static/js/management-settings.js").read_text()
    template = Path("console/templates/settings.html").read_text()
    stylesheet = Path("console/static/css/app.css").read_text()

    assert "Review SSH host identity" in script
    assert "The SSH server presented this host identity." in script
    assert "Algorithm: ${pending.identity.algorithm}" in script
    assert "Fingerprint: ${pending.identity.fingerprint}" in script
    assert "Trust this identity" in script
    assert "Cancel / Dismiss" in script
    assert "sourceId: item.id" in script
    assert "endpoint: sourceEndpoint(item)" in script
    assert "candidateIsCurrent(current, pending)" in script
    assert "invalidateCandidate(item.id);" in script
    assert "JSON.stringify(pending.identity)" in script
    assert "await refresh();" in script
    assert "host_identity_changed" in script
    assert "No trust change was made." in script
    assert ".management-trust-review[hidden]" in stylesheet
    assert "display: none" in stylesheet
    assert "body.result.category === 'host_identity_untrusted'" in script
    assert "!item.ssh_trusted" in script
    assert script.count("/test`") == 1
    assert script.count("/trust`") == 1
    assert "Add management source" in template
    assert "Configured sources" in template
    assert 'class="management-layout"' in template
    assert "management-add-panel" in template
    assert "management-configured-panel" in template
    assert script.find("card.append(trustPanel)") < script.find("card.append(actions, result, review)")


def test_management_source_cards_show_persisted_state_not_add_form_state():
    script = Path("console/static/js/management-settings.js").read_text()
    template = Path("console/templates/settings.html").read_text()

    assert "detail('Persisted endpoint', sourceEndpoint(item))" in script
    assert "detail('Explicit management actions', item.enabled ? 'Enabled' : 'Disabled')" in script
    assert "detail('Credential', credential)" in script
    assert "participantLabels.get" in script
    assert "sourceForm.elements.management_address.value = item.management_address" in script
    assert "sourceForm.elements.enabled.checked = item.enabled" in script
    assert "sourceFormTitle.textContent = `Editing ${participant}`" in script
    assert "sourceFormState.textContent = 'Editing persisted source'" in script
    assert "sourceFormTitle.textContent = 'New source configuration'" in script
    assert "Operational source state below always comes from persisted source data." in template


def test_management_ui_presents_trusted_identity_and_compact_safe_credentials():
    script = Path("console/static/js/management-settings.js").read_text()
    template = Path("console/templates/settings.html").read_text()
    stylesheet = Path("console/static/css/app.css").read_text()

    assert "Trusted SSH identity" in script
    assert "item.ssh_host_key_algorithm" in script
    assert "item.ssh_host_key_fingerprint" in script
    assert "item.ssh_host_key_trusted_at" in script
    assert "management-fingerprint" in script
    assert "user-select: text" in stylesheet
    assert "overflow-wrap: anywhere" in stylesheet
    assert "Only labels and credential types are shown." in template
    assert '<details id="management-credential-editor"' in template
    assert "item.credential.label" in script
    assert "item.credential.credential_type" in script


def test_management_ui_preserves_disabled_defaults_and_does_not_test_after_trust():
    script = Path("console/static/js/management-settings.js").read_text()
    template = Path("console/templates/settings.html").read_text()

    assert '<input name="enabled" type="checkbox">' in template
    assert template.count('name="capability"') == 4
    assert "Supported does not mean enabled." in template
    trust_block = script.split("const trust = actionButton", 1)[1].split(
        "reviewActions.append", 1
    )[0]
    assert "/test" not in trust_block
    assert "await refresh();" in trust_block


def test_management_ui_clears_stale_trust_review_state():
    script = Path("console/static/js/management-settings.js").read_text()

    render_block = script.split("function renderSources()", 1)[1].split(
        "async function loadParticipants", 1
    )[0]
    test_block = render_block.split("actionButton('Test'", 1)[1]
    trust_block = render_block.split("const trust = actionButton", 1)[1].split(
        "reviewActions.append", 1
    )[0]

    assert "if (item.ssh_trusted) invalidateCandidate(item.id);" in render_block
    assert "body.result.category === 'host_identity_untrusted'" in test_block
    assert "&& body.result.candidate" in test_block
    assert "!item.ssh_trusted" in test_block
    assert "invalidateCandidate(item.id);" in trust_block
    assert "review.hidden = true;" in trust_block
    assert "body.result.category === 'host_identity_changed'" in test_block
    assert "No trust change was made." in test_block
    assert test_block.count("review.hidden = false;") == 1
    assert script.count("/test`") == 1
    assert script.count("/trust`") == 1
