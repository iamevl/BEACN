import base64
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "console"))

from beacn.database import Database, apply_migrations, initialise_schema
from beacn.database.migrations import Migration
from beacn.database.schema import (
    initialise_auth_schema,
    initialise_password_recovery_schema,
    initialise_security_settings_schema,
)
from beacn.management import (
    CAPABILITIES,
    ManagementRepository,
    ManagementStorageError,
    ManagementValidationError,
)
from beacn.security import (
    CredentialCipher,
    CredentialCryptoError,
    CredentialKeyUnavailable,
    CredentialValidationError,
    FernetKeyRing,
    load_credential_key_ring,
)

SYNTHETIC_PAYLOADS = {
    "username_password": {
        "username": "synthetic-user",
        "password": "synthetic-password-marker",
    },
    "ssh_private_key": {
        "username": "synthetic-ssh-user",
        "private_key": "synthetic-private-key-marker",
        "passphrase": "synthetic-passphrase-marker",
    },
    "snmp_v2_community": {
        "community": "synthetic-community-marker",
    },
    "snmp_v3": {
        "username": "synthetic-snmp-user",
        "auth_password": "synthetic-auth-marker",
        "priv_password": "synthetic-privacy-marker",
    },
    "api_token": {
        "token": "synthetic-api-token-marker",
    },
}


def key_id(key: bytes) -> str:
    raw = base64.urlsafe_b64decode(key)
    return hashlib.sha256(raw).hexdigest()[:16]


def key_ring(active: bytes, *legacy: bytes) -> FernetKeyRing:
    values = [active, *legacy]
    return FernetKeyRing(
        key_id(active),
        {key_id(value): Fernet(value) for value in values},
    )


def initialise_all(conn):
    initialise_schema(conn)
    initialise_auth_schema(conn)
    initialise_security_settings_schema(conn)
    initialise_password_recovery_schema(conn)
    apply_migrations(conn)


@pytest.fixture
def foundation(tmp_path):
    database = Database(tmp_path / "foundation.db")
    with database.connect() as conn:
        initialise_all(conn)

    active_key = Fernet.generate_key()
    repository = ManagementRepository(
        database.connect,
        CredentialCipher(key_ring(active_key)),
    )

    device_id = str(uuid4())
    infrastructure_id = str(uuid4())
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO devices (
                id, ip, hostname, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?)
        """,
            (
                device_id,
                "192.0.2.10",
                "synthetic-device.example.invalid",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO infrastructure_objects (
                id, name, infrastructure_type, connection_method,
                interfaces_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'wired', '[]', ?, ?)
        """,
            (
                infrastructure_id,
                "Synthetic infrastructure",
                "switch",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    return database, repository, device_id, infrastructure_id


@pytest.mark.parametrize("credential_type", sorted(SYNTHETIC_PAYLOADS))
def test_every_credential_type_round_trip_without_plaintext_metadata(
    credential_type,
):
    key = Fernet.generate_key()
    cipher = CredentialCipher(key_ring(key))
    secrets = SYNTHETIC_PAYLOADS[credential_type]

    encrypted = cipher.encrypt(credential_type, secrets)

    assert (
        cipher.decrypt(
            credential_type=credential_type,
            encrypted_payload=encrypted.encrypted_payload,
            encryption_format=encrypted.encryption_format,
            key_id=encrypted.key_id,
        )
        == secrets
    )
    assert all(value not in encrypted.encrypted_payload for value in secrets.values())
    assert all(value not in repr(encrypted) for value in secrets.values())


def test_key_loader_resolution_and_rotation_readiness(tmp_path):
    active = Fernet.generate_key()
    legacy = Fernet.generate_key()
    ignored_environment_key = Fernet.generate_key()
    key_file = tmp_path / "keys"
    key_file.write_text(
        active.decode() + "\n" + legacy.decode() + "\n",
        encoding="utf-8",
    )

    ring = load_credential_key_ring(
        {
            "BEACN_ENCRYPTION_KEY_FILE": str(key_file),
            "BEACN_ENCRYPTION_KEY": ignored_environment_key.decode(),
        }
    )

    assert ring.active_key_id == key_id(active)
    assert ring.key_ids == (key_id(active), key_id(legacy))
    assert ring.active_key_id == key_id(active)
    assert key_id(active) == key_id(active)


def test_environment_active_key_and_legacy_decrypt():
    active = Fernet.generate_key()
    legacy = Fernet.generate_key()
    ring = load_credential_key_ring(
        {
            "BEACN_ENCRYPTION_KEY": active.decode(),
            "BEACN_ENCRYPTION_LEGACY_KEYS": legacy.decode(),
        }
    )
    cipher = CredentialCipher(ring)

    legacy_payload = json.dumps(
        {
            "version": 1,
            "credential_type": "api_token",
            "secrets": {"token": "synthetic-legacy-token"},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    legacy_token = Fernet(legacy).encrypt(legacy_payload).decode()

    assert cipher.decrypt(
        credential_type="api_token",
        encrypted_payload=legacy_token,
        encryption_format="fernet-v1",
        key_id=key_id(legacy),
    ) == {"token": "synthetic-legacy-token"}
    assert cipher.encrypt("api_token", {"token": "new-token"}).key_id == key_id(active)


def test_no_key_is_optional_but_crypto_fails_closed():
    assert load_credential_key_ring({}) is None
    cipher = CredentialCipher(None)
    assert cipher.available is False
    with pytest.raises(CredentialKeyUnavailable):
        cipher.encrypt("api_token", {"token": "synthetic-token"})


@pytest.mark.parametrize("bad_key", ["", "not-a-fernet-key"])
def test_invalid_explicit_key_is_sanitized(bad_key):
    environment = {"BEACN_ENCRYPTION_KEY": bad_key}
    if not bad_key:
        assert load_credential_key_ring(environment) is None
        return
    with pytest.raises(CredentialCryptoError) as caught:
        load_credential_key_ring(environment)
    assert bad_key not in str(caught.value)


def test_wrong_unknown_malformed_truncated_and_modified_ciphertext_fail_closed():
    first = Fernet.generate_key()
    second = Fernet.generate_key()
    cipher = CredentialCipher(key_ring(first))
    encrypted = cipher.encrypt("api_token", {"token": "synthetic-marker"})

    cases = [
        (
            CredentialCipher(key_ring(second)),
            encrypted.encrypted_payload,
            key_id(second),
        ),
        (cipher, encrypted.encrypted_payload, "0000000000000000"),
        (cipher, "malformed", encrypted.key_id),
        (cipher, encrypted.encrypted_payload[:-8], encrypted.key_id),
        (cipher, encrypted.encrypted_payload[:-2] + "AA", encrypted.key_id),
    ]
    for candidate_cipher, token, candidate_key_id in cases:
        with pytest.raises(CredentialCryptoError) as caught:
            candidate_cipher.decrypt(
                credential_type="api_token",
                encrypted_payload=token,
                encryption_format="fernet-v1",
                key_id=candidate_key_id,
            )
        assert "synthetic-marker" not in str(caught.value)


def test_unknown_format_payload_version_and_type_fail_closed():
    key = Fernet.generate_key()
    cipher = CredentialCipher(key_ring(key))
    encrypted = cipher.encrypt("api_token", {"token": "synthetic-marker"})

    with pytest.raises(CredentialCryptoError):
        cipher.decrypt(
            credential_type="api_token",
            encrypted_payload=encrypted.encrypted_payload,
            encryption_format="future-format",
            key_id=encrypted.key_id,
        )

    future_payload = (
        Fernet(key)
        .encrypt(
            json.dumps(
                {
                    "version": 2,
                    "credential_type": "api_token",
                    "secrets": {"token": "synthetic-marker"},
                }
            ).encode()
        )
        .decode()
    )
    with pytest.raises(CredentialCryptoError):
        cipher.decrypt(
            credential_type="api_token",
            encrypted_payload=future_payload,
            encryption_format="fernet-v1",
            key_id=key_id(key),
        )

    with pytest.raises(CredentialValidationError):
        cipher.encrypt("unknown", {"token": "synthetic-marker"})


def test_validation_errors_never_echo_secret_values():
    cipher = CredentialCipher(key_ring(Fernet.generate_key()))
    marker = "do-not-echo-this-synthetic-secret"
    with pytest.raises(CredentialValidationError) as caught:
        cipher.encrypt("api_token", {"unexpected": marker})
    assert marker not in str(caught.value)


def test_credential_repository_create_sanitize_replace_and_delete(foundation):
    database, repository, _, _ = foundation
    original = repository.create_credential(
        "username_password",
        "Synthetic credential",
        SYNTHETIC_PAYLOADS["username_password"],
    )

    assert repository.get_credential(original.id) == original
    assert repository.list_credentials() == [original]
    serialized = original.to_dict()
    assert set(serialized) == {
        "id",
        "credential_type",
        "label",
        "created_at",
        "updated_at",
        "last_rotated_at",
        "configured",
    }
    with database.connect() as conn:
        stored = conn.execute(
            "SELECT * FROM management_credentials WHERE id = ?", (original.id,)
        ).fetchone()
    assert stored["encrypted_payload"] not in repr(original)
    assert all(
        value not in str(tuple(stored))
        for value in SYNTHETIC_PAYLOADS["username_password"].values()
    )

    replacement = {"username": "replacement-user", "password": "replacement-marker"}
    replaced = repository.replace_credential(original.id, replacement)
    assert replaced.last_rotated_at is not None
    assert replaced.updated_at >= original.updated_at
    assert repository.decrypt_credential(original.id) == replacement

    repository.delete_credential(original.id)
    assert repository.get_credential(original.id) is None


def test_failed_credential_replacement_is_atomic(foundation):
    _, repository, _, _ = foundation
    credential = repository.create_credential(
        "api_token", "Synthetic token", {"token": "original-marker"}
    )
    with pytest.raises(CredentialValidationError):
        repository.replace_credential(credential.id, {"wrong": "replacement-marker"})
    assert repository.decrypt_credential(credential.id) == {"token": "original-marker"}


def create_source(repository, participant_kind, participant_id, **values):
    return repository.create_source(
        participant_kind=participant_kind,
        participant_id=participant_id,
        adapter_type=values.pop("adapter_type", "ssh_generic"),
        management_address=values.pop("management_address", "router.example.invalid"),
        **values,
    )


def test_device_and_infrastructure_sources_and_no_automatic_capabilities(foundation):
    _, repository, device_id, infrastructure_id = foundation
    device_source = create_source(repository, "device", device_id)
    infrastructure_source = create_source(
        repository,
        "infrastructure_object",
        infrastructure_id,
        adapter_type="snmp",
        management_address="192.0.2.20",
    )

    assert device_source.participant_id == device_id
    assert infrastructure_source.management_address == "192.0.2.20"
    assert device_source.capabilities == ()
    assert infrastructure_source.capabilities == ()


@pytest.mark.parametrize("kind", ["", "router", "infrastructure"])
def test_invalid_participant_kind_rejected(foundation, kind):
    _, repository, device_id, _ = foundation
    with pytest.raises(ManagementValidationError):
        create_source(repository, kind, device_id)


def test_nonexistent_participant_rejected(foundation):
    _, repository, _, _ = foundation
    with pytest.raises(ManagementValidationError):
        create_source(repository, "device", str(uuid4()))


def test_source_uniqueness_endpoint_timeout_and_credential_validation(foundation):
    _, repository, device_id, _ = foundation
    create_source(repository, "device", device_id)
    with pytest.raises(ManagementValidationError):
        create_source(repository, "device", device_id)
    for address in ("", "https://router.example.invalid", "bad host"):
        with pytest.raises(ManagementValidationError):
            create_source(
                repository,
                "device",
                device_id,
                management_address=address,
                adapter_type="snmp",
            )
    for timeout in (0, 31, True):
        with pytest.raises(ManagementValidationError):
            create_source(
                repository,
                "device",
                device_id,
                adapter_type=f"snmp_{str(timeout).lower()}",
                connection_timeout_seconds=timeout,
            )
    with pytest.raises(ManagementValidationError):
        create_source(
            repository,
            "device",
            device_id,
            adapter_type="api",
            credential_id=str(uuid4()),
        )


def test_shared_credential_referenced_delete_and_source_delete_semantics(foundation):
    database, repository, device_id, infrastructure_id = foundation
    credential = repository.create_credential(
        "api_token", "Shared synthetic token", {"token": "shared-marker"}
    )
    first = create_source(repository, "device", device_id, credential_id=credential.id)
    second = create_source(
        repository,
        "infrastructure_object",
        infrastructure_id,
        credential_id=credential.id,
        management_address="controller.example.invalid",
    )
    repository.set_capability(first.id, "interface_inventory", True)

    with pytest.raises(ManagementStorageError):
        repository.delete_credential(credential.id)
    repository.delete_source(first.id)
    with database.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM management_source_capabilities WHERE source_id = ?",
                (first.id,),
            ).fetchone()[0]
            == 0
        )
    assert repository.get_credential(credential.id) is not None
    repository.delete_source(second.id)
    repository.delete_credential(credential.id)


def test_capability_vocabulary_enable_disable_and_source_isolation(foundation):
    _, repository, device_id, infrastructure_id = foundation
    first = create_source(repository, "device", device_id)
    second = create_source(
        repository,
        "infrastructure_object",
        infrastructure_id,
        management_address="switch.example.invalid",
    )

    assert CAPABILITIES == {
        "interface_inventory",
        "bridge_fdb",
        "wireless_associations",
        "neighbours",
    }
    first = repository.set_capability(first.id, "bridge_fdb", True)
    assert dict(first.capabilities) == {"bridge_fdb": True}
    assert second.capabilities == ()
    first = repository.set_capability(first.id, "bridge_fdb", False)
    assert dict(first.capabilities) == {"bridge_fdb": False}
    with pytest.raises(ManagementValidationError):
        repository.set_capability(first.id, "arbitrary", True)


def test_endpoint_change_clears_ssh_trust_and_unrelated_update_preserves_it(foundation):
    _, repository, device_id, _ = foundation
    source = create_source(repository, "device", device_id, management_port=22)
    source = repository.set_ssh_trust(
        source.id,
        algorithm="ssh-ed25519",
        fingerprint="SHA256:synthetic-fingerprint",
    )
    assert source.ssh_trusted is True

    source = repository.update_source(source.id, connection_timeout_seconds=10)
    assert source.ssh_trusted is True
    source = repository.update_source(
        source.id, management_address="new.example.invalid"
    )
    assert source.ssh_trusted is False

    source = repository.set_ssh_trust(
        source.id,
        algorithm="ssh-ed25519",
        fingerprint="SHA256:replacement-fingerprint",
    )
    source = repository.update_source(source.id, management_port=2222)
    assert source.ssh_trusted is False


def test_orphan_detection_and_collection_eligibility(foundation):
    database, repository, device_id, _ = foundation
    source = create_source(repository, "device", device_id, enabled=True)
    assert repository.eligible_sources() == [source]

    with database.connect() as conn:
        conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))

    orphan = repository.find_orphaned_sources()
    assert len(orphan) == 1
    assert orphan[0].id == source.id
    assert orphan[0].orphaned is True
    assert repository.eligible_sources() == []


def schema_signature(conn):
    return [
        tuple(row)
        for row in conn.execute("""
            SELECT type, name, tbl_name, sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name
        """)
    ]


def test_migration_clean_existing_idempotent_and_schema_equivalence(tmp_path):
    clean = Database(tmp_path / "clean.db")
    upgraded = Database(tmp_path / "upgraded.db")

    with upgraded.connect() as conn:
        initialise_schema(conn)
        initialise_auth_schema(conn)
        initialise_security_settings_schema(conn)
        initialise_password_recovery_schema(conn)
    with clean.connect() as conn:
        initialise_all(conn)
        clean_signature = schema_signature(conn)
        applied_once = conn.execute(
            "SELECT migration_id FROM schema_migrations"
        ).fetchall()
        apply_migrations(conn)
        applied_twice = conn.execute(
            "SELECT migration_id FROM schema_migrations"
        ).fetchall()
    with upgraded.connect() as conn:
        apply_migrations(conn)
        upgraded_signature = schema_signature(conn)

    assert clean_signature == upgraded_signature
    assert applied_once == applied_twice
    assert [row[0] for row in applied_once] == ["20260813_01_management_foundation"]


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path):
    database = Database(tmp_path / "failed.db")

    def fail(conn):
        conn.execute("CREATE TABLE should_roll_back (id INTEGER)")
        raise RuntimeError("synthetic migration failure")

    with database.connect() as conn:
        with pytest.raises(RuntimeError):
            apply_migrations(conn, [Migration("synthetic_failure", fail)])
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'should_roll_back'"
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT 1 FROM schema_migrations WHERE migration_id = 'synthetic_failure'"
            ).fetchone()
            is None
        )
