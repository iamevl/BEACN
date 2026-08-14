# ruff: noqa: F811

import base64
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from beacn.management import ManagementRepository
from beacn.management.collection import (
    CollectionError,
    CollectionResult,
    ManagementCollectionService,
    NormalizedInterface,
    parse_ip_interface_inventory,
)
from beacn.management.collectors.ssh_interfaces import (
    INTERFACE_COMMANDS,
    MAX_COMMAND_OUTPUT_BYTES,
    SSHInterfaceInventoryCollector,
)
from beacn.management.connectivity import HostIdentity
from beacn.security import credential_cipher_from_environment
from test_auth_sessions import app as auth_app_fixture  # noqa: F401
from test_auth_sessions import beacn_app
from test_management_api import (
    authenticated_client,
    create_source,
    csrf,
    mutation,
)
from test_management_api import management_app as management_app_fixture  # noqa: F401
from test_management_connectivity import source_value

LINK_OUTPUT = """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq state UP mode DEFAULT link/ether AA:BB:CC:DD:EE:FF brd ff:ff:ff:ff:ff:ff
3: br0: <BROADCAST,MULTICAST> mtu 1500 qdisc noqueue state DOWN mode DEFAULT link/ether 02:00:00:00:00:01 brd ff:ff:ff:ff:ff:ff
"""
ADDRESS_OUTPUT = """\
1: lo    inet 127.0.0.1/8 scope host lo
1: lo    inet6 ::1/128 scope host
2: eth0  inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0
2: eth0  inet6 2001:db8::10/64 scope global
2: eth0  inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0
"""


def test_parser_normalizes_multiple_interfaces_loopback_addresses_and_duplicates():
    interfaces = parse_ip_interface_inventory(LINK_OUTPUT, ADDRESS_OUTPUT)
    assert [item.interface_name for item in interfaces] == ["lo", "eth0", "br0"]
    assert interfaces[0].interface_kind == "loopback"
    assert interfaces[0].mac_address is None
    assert interfaces[1].mac_address == "aa:bb:cc:dd:ee:ff"
    assert interfaces[1].admin_state == "up"
    assert interfaces[1].operational_state == "up"
    assert interfaces[1].mtu == 1500
    assert interfaces[1].addresses == ("192.0.2.10/24", "2001:db8::10/64")
    assert interfaces[2].operational_state == "down"


def test_parser_allows_missing_link_fields_for_address_only_interface():
    interfaces = parse_ip_interface_inventory("", "7: tun0 inet 198.51.100.1/32 scope global tun0")
    assert interfaces == (
        NormalizedInterface(
            interface_name="tun0",
            interface_index=7,
            addresses=("198.51.100.1/32",),
        ),
    )


def test_parser_rejects_empty_output_instead_of_erasing_snapshot():
    with pytest.raises(CollectionError) as error:
        parse_ip_interface_inventory("", "")
    assert error.value.category == "malformed_output"


@pytest.mark.parametrize(
    "link,address",
    [
        ("unexpected", ""),
        ("1: bad name: <UP> mtu 1 state UP link/ether aa:bb:cc:dd:ee:ff", ""),
        ("", "1: eth0 inet not-an-address scope global"),
        (LINK_OUTPUT.splitlines()[1] + "\n" + LINK_OUTPUT.splitlines()[1], ""),
    ],
)
def test_parser_rejects_malformed_duplicate_or_unexpected_output(link, address):
    with pytest.raises(CollectionError, match="output is invalid"):
        parse_ip_interface_inventory(link, address)


class FakeRepository:
    def __init__(self):
        self.decrypt_calls = []
        self.persisted = []
        self.credential = type("Credential", (), {"credential_type": "username_password"})()

    def get_credential(self, _credential_id):
        return self.credential

    def decrypt_credential(self, credential_id):
        self.decrypt_calls.append(credential_id)
        return {"username": "synthetic", "password": "synthetic"}

    def replace_interface_inventory(self, source, interfaces, *, collected_at):
        self.persisted.append((source.id, interfaces, collected_at))
        return interfaces


class FakeCollector:
    def __init__(self, identity=None):
        self.presented = identity or HostIdentity("ssh-ed25519", "SHA256:synthetic")
        self.collect_calls = []

    def identity(self, _source):
        return self.presented

    def collect(self, source, secrets, credential_type):
        self.collect_calls.append((source.id, secrets, credential_type))
        return LINK_OUTPUT, ADDRESS_OUTPUT


class FailingIdentityCollector(FakeCollector):
    def identity(self, _source):
        raise OSError("synthetic raw transport marker")


@pytest.mark.parametrize(
    ("source", "category"),
    [
        (source_value(enabled=False, trusted=True), "source_disabled"),
        (source_value(enabled=True, trusted=True), "capability_disabled"),
    ],
)
def test_collection_requires_enabled_source_and_explicit_capability(source, category):
    repository = FakeRepository()
    service = ManagementCollectionService(repository, ssh_collector=FakeCollector())
    with pytest.raises(CollectionError) as error:
        service.collect_interface_inventory(source)
    assert error.value.category == category
    assert repository.decrypt_calls == []


def test_identity_mismatch_prevents_credential_decryption():
    source = replace(
        source_value(trusted=True),
        capabilities=(("interface_inventory", True),),
    )
    repository = FakeRepository()
    service = ManagementCollectionService(
        repository,
        ssh_collector=FakeCollector(HostIdentity("ssh-ed25519", "SHA256:changed")),
    )
    with pytest.raises(CollectionError) as error:
        service.collect_interface_inventory(source)
    assert error.value.category == "host_identity_changed"
    assert repository.decrypt_calls == []


def test_identity_transport_failure_is_sanitized_before_decryption():
    source = replace(
        source_value(trusted=True),
        capabilities=(("interface_inventory", True),),
    )
    repository = FakeRepository()
    service = ManagementCollectionService(
        repository,
        ssh_collector=FailingIdentityCollector(),
    )
    with pytest.raises(CollectionError) as error:
        service.collect_interface_inventory(source)
    assert error.value.category == "collection_failed"
    assert "synthetic raw transport marker" not in str(error.value)
    assert repository.decrypt_calls == []


def test_enabled_capability_reaches_collector_and_persistence_boundary():
    source = replace(
        source_value(trusted=True),
        capabilities=(("interface_inventory", True),),
    )
    repository = FakeRepository()
    collector = FakeCollector()
    result = ManagementCollectionService(
        repository, ssh_collector=collector
    ).collect_interface_inventory(source)
    assert result.category == "collected"
    assert len(result.interfaces) == 3
    assert repository.decrypt_calls == [source.credential_id]
    assert len(collector.collect_calls) == 1
    assert len(repository.persisted) == 1


class Key:
    def get_name(self):
        return "ssh-ed25519"

    def asbytes(self):
        return b"synthetic-host-key"


class Connection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Channel:
    def __init__(self, output):
        self.output = bytearray(output)
        self.command = None
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def exec_command(self, command):
        self.command = command

    def recv_ready(self):
        return bool(self.output)

    def recv(self, size):
        value = self.output[:size]
        del self.output[:size]
        return bytes(value)

    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, _size):
        return b""

    def exit_status_ready(self):
        return not self.output

    def recv_exit_status(self):
        return 0

    def close(self):
        self.closed = True


class Transport:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.channels = []
        self.authenticated = False
        self.closed = False

    def auth_password(self, _username, _password):
        self.authenticated = True

    def is_authenticated(self):
        return self.authenticated

    def open_session(self, timeout):
        assert 1 <= timeout <= 30
        channel = Channel(self.outputs.pop(0))
        self.channels.append(channel)
        return channel

    def close(self):
        self.closed = True


def _trusted_source():
    fingerprint = "SHA256:" + base64.b64encode(
        hashlib.sha256(b"synthetic-host-key").digest()
    ).decode().rstrip("=")
    return replace(
        source_value(trusted=True),
        ssh_host_key_fingerprint=fingerprint,
        capabilities=(("interface_inventory", True),),
    )


def test_ssh_collector_uses_only_allowlisted_commands_and_closes_everything(monkeypatch):
    connection = Connection()
    transport = Transport((LINK_OUTPUT.encode(), ADDRESS_OUTPUT.encode()))
    collector = SSHInterfaceInventoryCollector()
    fake_paramiko = type(
        "Paramiko",
        (),
        {"AuthenticationException": RuntimeError, "SSHException": RuntimeError},
    )
    monkeypatch.setattr(collector._transport, "_paramiko", lambda: fake_paramiko)
    monkeypatch.setattr(
        collector._transport,
        "_open",
        lambda *_args: (connection, transport, Key()),
    )
    outputs = collector.collect(
        _trusted_source(),
        {"username": "synthetic", "password": "synthetic"},
        "username_password",
    )
    assert outputs == (LINK_OUTPUT, ADDRESS_OUTPUT)
    assert tuple(channel.command for channel in transport.channels) == INTERFACE_COMMANDS
    assert all(channel.closed for channel in transport.channels)
    assert connection.closed and transport.closed


def test_ssh_collector_bounds_output_and_closes_on_failure(monkeypatch):
    connection = Connection()
    transport = Transport((b"x" * (MAX_COMMAND_OUTPUT_BYTES + 1), b""))
    collector = SSHInterfaceInventoryCollector()
    fake_paramiko = type(
        "Paramiko",
        (),
        {"AuthenticationException": RuntimeError, "SSHException": RuntimeError},
    )
    monkeypatch.setattr(collector._transport, "_paramiko", lambda: fake_paramiko)
    monkeypatch.setattr(
        collector._transport,
        "_open",
        lambda *_args: (connection, transport, Key()),
    )
    with pytest.raises(CollectionError) as error:
        collector.collect(
            _trusted_source(),
            {"username": "synthetic", "password": "synthetic"},
            "username_password",
        )
    assert error.value.category == "output_too_large"
    assert connection.closed and transport.closed and transport.channels[0].closed


def test_ssh_collector_identity_mismatch_never_authenticates(monkeypatch):
    connection = Connection()
    transport = Transport((LINK_OUTPUT.encode(), ADDRESS_OUTPUT.encode()))
    collector = SSHInterfaceInventoryCollector()
    fake_paramiko = type(
        "Paramiko",
        (),
        {"AuthenticationException": RuntimeError, "SSHException": RuntimeError},
    )
    monkeypatch.setattr(collector._transport, "_paramiko", lambda: fake_paramiko)
    monkeypatch.setattr(
        collector._transport,
        "_open",
        lambda *_args: (connection, transport, Key()),
    )
    source = replace(_trusted_source(), ssh_host_key_fingerprint="SHA256:changed")
    with pytest.raises(CollectionError) as error:
        collector.collect(
            source,
            {"username": "synthetic", "password": "synthetic"},
            "username_password",
        )
    assert error.value.category == "host_identity_changed"
    assert not transport.authenticated
    assert transport.channels == []
    assert connection.closed and transport.closed


def test_inventory_persistence_is_idempotent_and_removes_stale(
    management_app_fixture,
):
    repository = ManagementRepository(beacn_app.db, credential_cipher_from_environment())
    client = authenticated_client(management_app_fixture)
    token = csrf(client)
    source_payload = create_source(
        client,
        token,
        management_app_fixture,
        enabled=True,
        capabilities={"interface_inventory": True},
    ).get_json()["source"]
    source = repository.get_source(source_payload["id"])
    first = repository.replace_interface_inventory(
        source,
        (
            NormalizedInterface("eth0", 2, addresses=("192.0.2.10/24",)),
            NormalizedInterface("stale0", 3),
        ),
        collected_at="2026-08-14T09:00:00+00:00",
    )
    second = repository.replace_interface_inventory(
        source,
        (NormalizedInterface("eth0", 2, mtu=1500),),
        collected_at="2026-08-14T09:01:00+00:00",
    )
    assert len(first) == 2
    assert len(second) == 1
    assert second[0].id == next(item.id for item in first if item.interface_name == "eth0")
    assert second[0].mtu == 1500
    assert repository.interface_inventory_status(source.id)["item_count"] == 1


def test_inventory_refresh_rolls_back_and_retains_snapshot_on_write_failure(
    management_app_fixture,
):
    repository = ManagementRepository(beacn_app.db, credential_cipher_from_environment())
    client = authenticated_client(management_app_fixture)
    token = csrf(client)
    source_id = create_source(
        client,
        token,
        management_app_fixture,
        enabled=True,
        capabilities={"interface_inventory": True},
    ).get_json()["source"]["id"]
    source = repository.get_source(source_id)
    original = repository.replace_interface_inventory(
        source,
        (NormalizedInterface("eth0", 2, mtu=1500),),
        collected_at="2026-08-14T09:00:00+00:00",
    )
    with beacn_app.db() as conn:
        conn.execute("""
            CREATE TRIGGER reject_synthetic_interface
            BEFORE INSERT ON management_interface_inventory
            WHEN NEW.interface_name = 'boom0'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic rollback');
            END
        """)
    with pytest.raises(Exception, match="synthetic rollback"):
        repository.replace_interface_inventory(
            source,
            (
                NormalizedInterface("eth0", 2, mtu=9000),
                NormalizedInterface("boom0", 3),
            ),
            collected_at="2026-08-14T09:01:00+00:00",
        )
    retained = repository.list_interface_inventory(source.id)
    assert len(retained) == 1
    assert retained[0].id == original[0].id
    assert retained[0].mtu == 1500
    assert repository.interface_inventory_status(source.id)["collected_at"] == (
        "2026-08-14T09:00:00+00:00"
    )


def test_collection_api_is_admin_csrf_rate_limited_and_explicit(
    management_app_fixture, monkeypatch
):
    client = authenticated_client(management_app_fixture)
    token = csrf(client)
    source = create_source(
        client,
        token,
        management_app_fixture,
        enabled=True,
        capabilities={"interface_inventory": True},
    ).get_json()["source"]

    class FakeService:
        def __init__(self, *_args, **_kwargs):
            pass

        def collect_interface_inventory(self, _source):
            return CollectionResult(
                "collected",
                "Interface inventory collected.",
                "2026-08-14T09:00:00+00:00",
                (NormalizedInterface("eth0", 2),),
            )

    monkeypatch.setattr(beacn_app, "ManagementCollectionService", FakeService)
    beacn_app.management_collection_limiter._attempts.clear()
    path = f"/api/management/sources/{source['id']}/collect/interface-inventory"
    assert management_app_fixture.test_client().post(path, json={}).status_code == 401
    assert client.post(path, json={}).status_code == 403
    for _ in range(3):
        response = mutation(client, "POST", path, token=token, payload={})
        assert response.status_code == 200
        body = response.get_json()["result"]
        assert body["category"] == "collected"
        assert "raw" not in body
        assert "command" not in body
    assert mutation(client, "POST", path, token=token, payload={}).status_code == 429


@pytest.mark.parametrize(
    "changes,expected_code",
    [
        (
            {"enabled": False, "capabilities": {"interface_inventory": True}},
            "source_disabled",
        ),
        (
            {"enabled": True, "capabilities": {"interface_inventory": False}},
            "capability_disabled",
        ),
    ],
)
def test_collection_api_authority_fails_before_transport(
    management_app_fixture, changes, expected_code
):
    client = authenticated_client(management_app_fixture)
    token = csrf(client)
    source = create_source(
        client,
        token,
        management_app_fixture,
        **changes,
    ).get_json()["source"]
    beacn_app.management_collection_limiter._attempts.clear()
    response = mutation(
        client,
        "POST",
        f"/api/management/sources/{source['id']}/collect/interface-inventory",
        token=token,
        payload={},
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == expected_code


def test_management_ui_exposes_only_semantic_explicit_collection():
    script = Path("console/static/js/management-settings.js").read_text()
    assert "Collect interface inventory" in script
    assert "item.capabilities.interface_inventory" in script
    assert "/collect/interface-inventory" in script
    assert "ip -o" not in script
    assert "exec_command" not in script
    assert script.count("/collect/interface-inventory") == 1
    trust_block = script.split("const trust = actionButton", 1)[1].split(
        "reviewActions.append", 1
    )[0]
    assert "/collect/" not in trust_block
    assert "/test" not in trust_block
    source_submit = script.split("sourceForm.addEventListener('submit'", 1)[1].split(
        "document.getElementById('management-credential-cancel')", 1
    )[0]
    assert "/collect/" not in source_submit
    assert "Collect interface inventory" not in source_submit
    assert INTERFACE_COMMANDS == ("ip -o link show", "ip -o address show")


def test_relationship_and_topology_code_do_not_reference_interface_inventory():
    paths = [
        Path("console/beacn/relationships"),
        Path("console/static/js/topology-tree.js"),
        Path("console/static/js/topology-view-model.js"),
    ]
    for path in paths:
        files = path.rglob("*.py") if path.is_dir() else (path,)
        assert all("interface_inventory" not in file.read_text() for file in files)
