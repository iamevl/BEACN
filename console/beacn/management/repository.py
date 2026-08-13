"""SQLite repository for management credentials, sources and capabilities."""

from __future__ import annotations

import ipaddress
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

from beacn.security import CredentialCipher

CAPABILITIES = frozenset(
    {
        "interface_inventory",
        "bridge_fdb",
        "wireless_associations",
        "neighbours",
    }
)
PARTICIPANT_KINDS = frozenset({"device", "infrastructure_object"})
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 30
_ADAPTER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class ManagementStorageError(RuntimeError):
    """Stable persistence error that never contains submitted secrets."""


class ManagementValidationError(ManagementStorageError, ValueError):
    pass


class ManagementNotFoundError(ManagementStorageError, LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ManagementCredential:
    id: str
    credential_type: str
    label: str
    created_at: str
    updated_at: str
    last_rotated_at: str | None
    configured: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ManagementSource:
    id: str
    participant_kind: str
    participant_id: str
    adapter_type: str
    management_address: str
    management_port: int | None
    enabled: bool
    credential_id: str | None
    connection_timeout_seconds: int
    ssh_host_key_algorithm: str | None
    ssh_host_key_fingerprint: str | None
    ssh_host_key_trusted_at: str | None
    ssh_host_key_trusted_by: int | None
    created_at: str
    updated_at: str
    capabilities: tuple[tuple[str, bool], ...] = ()
    orphaned: bool = False

    @property
    def ssh_trusted(self) -> bool:
        return bool(
            self.ssh_host_key_algorithm
            and self.ssh_host_key_fingerprint
            and self.ssh_host_key_trusted_at
        )

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["capabilities"] = dict(self.capabilities)
        value["ssh_trusted"] = self.ssh_trusted
        return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_label(label: str) -> str:
    value = str(label or "").strip()
    if not value or len(value) > 120:
        raise ManagementValidationError("Credential label is invalid.")
    return value


def _validate_address(address: str) -> str:
    value = str(address or "").strip()
    if not value or len(value) > 253 or any(char.isspace() for char in value):
        raise ManagementValidationError("Management address is invalid.")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass

    hostname = value.rstrip(".").lower()
    if not hostname or any(
        not _HOST_LABEL.fullmatch(label) for label in hostname.split(".")
    ):
        raise ManagementValidationError("Management address is invalid.")
    return hostname


class ManagementRepository:
    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        cipher: CredentialCipher,
    ):
        self._connect = connect
        self._cipher = cipher

    @staticmethod
    def _credential(row: sqlite3.Row) -> ManagementCredential:
        return ManagementCredential(
            id=row["id"],
            credential_type=row["credential_type"],
            label=row["label"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_rotated_at=row["last_rotated_at"],
        )

    def create_credential(
        self,
        credential_type: str,
        label: str,
        secret_fields: Mapping[str, str],
    ) -> ManagementCredential:
        safe_label = _validate_label(label)
        encrypted = self._cipher.encrypt(credential_type, secret_fields)
        credential_id = str(uuid4())
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO management_credentials (
                    id, credential_type, label, encrypted_payload,
                    encryption_format, key_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    credential_id,
                    credential_type,
                    safe_label,
                    encrypted.encrypted_payload,
                    encrypted.encryption_format,
                    encrypted.key_id,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM management_credentials WHERE id = ?",
                (credential_id,),
            ).fetchone()
        return self._credential(row)

    def get_credential(self, credential_id: str) -> ManagementCredential | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, credential_type, label, created_at,
                       updated_at, last_rotated_at
                FROM management_credentials WHERE id = ?
            """,
                (credential_id,),
            ).fetchone()
        return self._credential(row) if row else None

    def list_credentials(self) -> list[ManagementCredential]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, credential_type, label, created_at,
                       updated_at, last_rotated_at
                FROM management_credentials
                ORDER BY label COLLATE NOCASE, id
            """).fetchall()
        return [self._credential(row) for row in rows]

    def decrypt_credential(self, credential_id: str) -> dict[str, str]:
        """Explicit internal-only decryption; never used by ordinary DTOs."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM management_credentials WHERE id = ?",
                (credential_id,),
            ).fetchone()
        if row is None:
            raise ManagementNotFoundError("Management credential was not found.")
        return self._cipher.decrypt(
            credential_type=row["credential_type"],
            encrypted_payload=row["encrypted_payload"],
            encryption_format=row["encryption_format"],
            key_id=row["key_id"],
        )

    def replace_credential(
        self,
        credential_id: str,
        secret_fields: Mapping[str, str],
    ) -> ManagementCredential:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT credential_type FROM management_credentials WHERE id = ?",
                (credential_id,),
            ).fetchone()
        if existing is None:
            raise ManagementNotFoundError("Management credential was not found.")

        encrypted = self._cipher.encrypt(existing["credential_type"], secret_fields)
        now = _utc_now()
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE management_credentials
                SET encrypted_payload = ?, encryption_format = ?, key_id = ?,
                    updated_at = ?, last_rotated_at = ?
                WHERE id = ?
            """,
                (
                    encrypted.encrypted_payload,
                    encrypted.encryption_format,
                    encrypted.key_id,
                    now,
                    now,
                    credential_id,
                ),
            )
            if updated.rowcount != 1:
                raise ManagementNotFoundError("Management credential was not found.")
            row = conn.execute(
                "SELECT * FROM management_credentials WHERE id = ?",
                (credential_id,),
            ).fetchone()
        return self._credential(row)

    def delete_credential(self, credential_id: str) -> None:
        with self._connect() as conn:
            referenced = conn.execute(
                "SELECT 1 FROM management_sources WHERE credential_id = ? LIMIT 1",
                (credential_id,),
            ).fetchone()
            if referenced:
                raise ManagementStorageError(
                    "Management credential is still referenced by a source."
                )
            deleted = conn.execute(
                "DELETE FROM management_credentials WHERE id = ?",
                (credential_id,),
            )
            if deleted.rowcount != 1:
                raise ManagementNotFoundError("Management credential was not found.")

    @staticmethod
    def _participant_exists(
        conn: sqlite3.Connection,
        participant_kind: str,
        participant_id: str,
    ) -> bool:
        if participant_kind == "device":
            table = "devices"
        elif participant_kind == "infrastructure_object":
            table = "infrastructure_objects"
        else:
            return False
        return (
            conn.execute(
                f"SELECT 1 FROM {table} WHERE id = ? LIMIT 1",
                (participant_id,),
            ).fetchone()
            is not None
        )

    def _validate_source_values(
        self,
        conn: sqlite3.Connection,
        *,
        participant_kind: str,
        participant_id: str,
        adapter_type: str,
        management_address: str,
        management_port: int | None,
        credential_id: str | None,
        connection_timeout_seconds: int,
    ) -> tuple[str, str]:
        if participant_kind not in PARTICIPANT_KINDS:
            raise ManagementValidationError("Participant kind is invalid.")
        participant_id = str(participant_id or "").strip()
        if not participant_id or len(participant_id) > 128:
            raise ManagementValidationError("Participant identity is invalid.")
        if not self._participant_exists(conn, participant_kind, participant_id):
            raise ManagementValidationError("Participant does not exist.")
        adapter_type = str(adapter_type or "").strip()
        if not _ADAPTER_PATTERN.fullmatch(adapter_type):
            raise ManagementValidationError("Adapter type is invalid.")
        address = _validate_address(management_address)
        if management_port is not None and (
            isinstance(management_port, bool)
            or not isinstance(management_port, int)
            or not 1 <= management_port <= 65535
        ):
            raise ManagementValidationError("Management port is invalid.")
        if (
            isinstance(connection_timeout_seconds, bool)
            or not isinstance(connection_timeout_seconds, int)
            or not MIN_TIMEOUT_SECONDS
            <= connection_timeout_seconds
            <= MAX_TIMEOUT_SECONDS
        ):
            raise ManagementValidationError("Connection timeout is invalid.")
        if (
            credential_id is not None
            and conn.execute(
                "SELECT 1 FROM management_credentials WHERE id = ?",
                (credential_id,),
            ).fetchone()
            is None
        ):
            raise ManagementValidationError("Management credential does not exist.")
        return adapter_type, address

    def create_source(
        self,
        *,
        participant_kind: str,
        participant_id: str,
        adapter_type: str,
        management_address: str,
        management_port: int | None = None,
        enabled: bool = False,
        credential_id: str | None = None,
        connection_timeout_seconds: int = 5,
        capabilities: Mapping[str, bool] | None = None,
    ) -> ManagementSource:
        if not isinstance(enabled, bool):
            raise ManagementValidationError(
                "Management source enabled state is invalid."
            )
        source_id = str(uuid4())
        now = _utc_now()
        try:
            with self._connect() as conn:
                adapter_type, address = self._validate_source_values(
                    conn,
                    participant_kind=participant_kind,
                    participant_id=participant_id,
                    adapter_type=adapter_type,
                    management_address=management_address,
                    management_port=management_port,
                    credential_id=credential_id,
                    connection_timeout_seconds=connection_timeout_seconds,
                )
                conn.execute(
                    """
                    INSERT INTO management_sources (
                        id, participant_kind, participant_id, adapter_type,
                        management_address, management_port, enabled,
                        credential_id, connection_timeout_seconds,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        source_id,
                        participant_kind,
                        participant_id,
                        adapter_type,
                        address,
                        management_port,
                        int(bool(enabled)),
                        credential_id,
                        connection_timeout_seconds,
                        now,
                        now,
                    ),
                )
                self._set_capabilities(conn, source_id, capabilities or {})
        except sqlite3.IntegrityError as exc:
            raise ManagementValidationError(
                "An equivalent management source already exists."
            ) from exc
        return self.get_source(source_id)

    def _source(self, conn: sqlite3.Connection, row: sqlite3.Row) -> ManagementSource:
        capabilities = tuple(
            (item["capability"], bool(item["enabled"]))
            for item in conn.execute(
                """
                SELECT capability, enabled
                FROM management_source_capabilities
                WHERE source_id = ? ORDER BY capability
            """,
                (row["id"],),
            ).fetchall()
        )
        orphaned = not self._participant_exists(
            conn,
            row["participant_kind"],
            row["participant_id"],
        )
        return ManagementSource(
            id=row["id"],
            participant_kind=row["participant_kind"],
            participant_id=row["participant_id"],
            adapter_type=row["adapter_type"],
            management_address=row["management_address"],
            management_port=row["management_port"],
            enabled=bool(row["enabled"]),
            credential_id=row["credential_id"],
            connection_timeout_seconds=row["connection_timeout_seconds"],
            ssh_host_key_algorithm=row["ssh_host_key_algorithm"],
            ssh_host_key_fingerprint=row["ssh_host_key_fingerprint"],
            ssh_host_key_trusted_at=row["ssh_host_key_trusted_at"],
            ssh_host_key_trusted_by=row["ssh_host_key_trusted_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            capabilities=capabilities,
            orphaned=orphaned,
        )

    def get_source(self, source_id: str) -> ManagementSource | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM management_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
            return self._source(conn, row) if row else None

    def list_sources(self) -> list[ManagementSource]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM management_sources ORDER BY created_at, id"
            ).fetchall()
            return [self._source(conn, row) for row in rows]

    def eligible_sources(self) -> list[ManagementSource]:
        return [
            source
            for source in self.list_sources()
            if source.enabled and not source.orphaned
        ]

    def update_source(self, source_id: str, **changes: object) -> ManagementSource:
        allowed = {
            "management_address",
            "management_port",
            "enabled",
            "credential_id",
            "connection_timeout_seconds",
            "capabilities",
        }
        if not changes or set(changes) - allowed:
            raise ManagementValidationError("Management source update is invalid.")

        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM management_sources WHERE id = ?",
                    (source_id,),
                ).fetchone()
                if row is None:
                    raise ManagementNotFoundError("Management source was not found.")
                current = dict(row)
                capability_changes = changes.pop("capabilities", None)
                current.update(changes)
                if not isinstance(current["enabled"], (bool, int)) or current[
                    "enabled"
                ] not in (0, 1, False, True):
                    raise ManagementValidationError(
                        "Management source enabled state is invalid."
                    )
                _, address = self._validate_source_values(
                    conn,
                    participant_kind=current["participant_kind"],
                    participant_id=current["participant_id"],
                    adapter_type=current["adapter_type"],
                    management_address=current["management_address"],
                    management_port=current["management_port"],
                    credential_id=current["credential_id"],
                    connection_timeout_seconds=current["connection_timeout_seconds"],
                )
                endpoint_changed = (
                    address != row["management_address"]
                    or current["management_port"] != row["management_port"]
                )
                trust = (
                    (None, None, None, None)
                    if endpoint_changed
                    else (
                        row["ssh_host_key_algorithm"],
                        row["ssh_host_key_fingerprint"],
                        row["ssh_host_key_trusted_at"],
                        row["ssh_host_key_trusted_by"],
                    )
                )
                conn.execute(
                    """
                    UPDATE management_sources
                    SET management_address = ?, management_port = ?, enabled = ?,
                        credential_id = ?, connection_timeout_seconds = ?,
                        ssh_host_key_algorithm = ?, ssh_host_key_fingerprint = ?,
                        ssh_host_key_trusted_at = ?, ssh_host_key_trusted_by = ?,
                        updated_at = ?
                    WHERE id = ?
                """,
                    (
                        address,
                        current["management_port"],
                        int(bool(current["enabled"])),
                        current["credential_id"],
                        current["connection_timeout_seconds"],
                        *trust,
                        _utc_now(),
                        source_id,
                    ),
                )
                if capability_changes is not None:
                    self._set_capabilities(conn, source_id, capability_changes)
        except sqlite3.IntegrityError as exc:
            raise ManagementValidationError(
                "An equivalent management source already exists."
            ) from exc
        return self.get_source(source_id)

    def set_ssh_trust(
        self,
        source_id: str,
        *,
        algorithm: str,
        fingerprint: str,
        trusted_by: int | None = None,
    ) -> ManagementSource:
        algorithm = str(algorithm or "").strip()
        fingerprint = str(fingerprint or "").strip()
        if (
            not algorithm
            or len(algorithm) > 128
            or not fingerprint
            or len(fingerprint) > 256
        ):
            raise ManagementValidationError("SSH host identity is invalid.")
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE management_sources
                SET ssh_host_key_algorithm = ?, ssh_host_key_fingerprint = ?,
                    ssh_host_key_trusted_at = ?, ssh_host_key_trusted_by = ?,
                    updated_at = ?
                WHERE id = ?
            """,
                (
                    algorithm,
                    fingerprint,
                    _utc_now(),
                    trusted_by,
                    _utc_now(),
                    source_id,
                ),
            )
            if updated.rowcount != 1:
                raise ManagementNotFoundError("Management source was not found.")
        return self.get_source(source_id)

    def set_capability(
        self,
        source_id: str,
        capability: str,
        enabled: bool,
    ) -> ManagementSource:
        if capability not in CAPABILITIES:
            raise ManagementValidationError("Management capability is invalid.")
        if not isinstance(enabled, bool):
            raise ManagementValidationError("Management capability state is invalid.")
        now = _utc_now()
        with self._connect() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM management_sources WHERE id = ?",
                    (source_id,),
                ).fetchone()
                is None
            ):
                raise ManagementNotFoundError("Management source was not found.")
            conn.execute(
                """
                INSERT INTO management_source_capabilities (
                    source_id, capability, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, capability) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
            """,
                (source_id, capability, int(bool(enabled)), now, now),
            )
        return self.get_source(source_id)

    @staticmethod
    def _set_capabilities(
        conn: sqlite3.Connection,
        source_id: str,
        capabilities: Mapping[str, bool],
    ) -> None:
        if not isinstance(capabilities, Mapping):
            raise ManagementValidationError("Management capabilities are invalid.")
        if any(capability not in CAPABILITIES for capability in capabilities):
            raise ManagementValidationError("Management capability is invalid.")
        if any(not isinstance(enabled, bool) for enabled in capabilities.values()):
            raise ManagementValidationError("Management capability state is invalid.")

        now = _utc_now()
        for capability, enabled in capabilities.items():
            conn.execute(
                """
                INSERT INTO management_source_capabilities (
                    source_id, capability, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, capability) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (source_id, capability, int(enabled), now, now),
            )

    def delete_source(self, source_id: str) -> None:
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM management_sources WHERE id = ?",
                (source_id,),
            )
            if deleted.rowcount != 1:
                raise ManagementNotFoundError("Management source was not found.")

    def find_orphaned_sources(self) -> list[ManagementSource]:
        return [source for source in self.list_sources() if source.orphaned]
