"""Small, transactional schema migration runner for BEACN."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass

MigrationAction = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class Migration:
    migration_id: str
    apply: MigrationAction


def _management_foundation(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE management_credentials (
            id TEXT PRIMARY KEY,
            credential_type TEXT NOT NULL,
            label TEXT NOT NULL,
            encrypted_payload TEXT NOT NULL,
            encryption_format TEXT NOT NULL,
            key_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_rotated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE management_sources (
            id TEXT PRIMARY KEY,
            participant_kind TEXT NOT NULL CHECK (
                participant_kind IN ('device', 'infrastructure_object')
            ),
            participant_id TEXT NOT NULL,
            adapter_type TEXT NOT NULL,
            management_address TEXT NOT NULL,
            management_port INTEGER CHECK (
                management_port IS NULL
                OR management_port BETWEEN 1 AND 65535
            ),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            credential_id TEXT,
            connection_timeout_seconds INTEGER NOT NULL DEFAULT 5 CHECK (
                connection_timeout_seconds BETWEEN 1 AND 30
            ),
            ssh_host_key_algorithm TEXT,
            ssh_host_key_fingerprint TEXT,
            ssh_host_key_trusted_at TEXT,
            ssh_host_key_trusted_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(credential_id)
                REFERENCES management_credentials(id)
                ON DELETE RESTRICT,
            FOREIGN KEY(ssh_host_key_trusted_by)
                REFERENCES auth_users(id)
                ON DELETE SET NULL
        )
    """)

    conn.execute("""
        CREATE UNIQUE INDEX uq_management_source_endpoint
        ON management_sources (
            participant_kind,
            participant_id,
            adapter_type,
            management_address,
            COALESCE(management_port, -1)
        )
    """)
    conn.execute("""
        CREATE INDEX idx_management_sources_participant
        ON management_sources(participant_kind, participant_id)
    """)
    conn.execute("""
        CREATE INDEX idx_management_sources_credential
        ON management_sources(credential_id)
    """)
    conn.execute("""
        CREATE INDEX idx_management_sources_enabled_adapter
        ON management_sources(enabled, adapter_type)
    """)

    conn.execute("""
        CREATE TABLE management_source_capabilities (
            source_id TEXT NOT NULL,
            capability TEXT NOT NULL CHECK (
                capability IN (
                    'interface_inventory',
                    'bridge_fdb',
                    'wireless_associations',
                    'neighbours'
                )
            ),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source_id, capability),
            FOREIGN KEY(source_id)
                REFERENCES management_sources(id)
                ON DELETE CASCADE
        )
    """)


def _management_interface_inventory(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE management_interface_inventory (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            participant_kind TEXT NOT NULL CHECK (
                participant_kind IN ('device', 'infrastructure_object')
            ),
            participant_id TEXT NOT NULL,
            interface_name TEXT NOT NULL,
            interface_index INTEGER,
            mac_address TEXT,
            admin_state TEXT,
            operational_state TEXT,
            mtu INTEGER,
            addresses_json TEXT NOT NULL,
            interface_kind TEXT,
            collected_at TEXT NOT NULL,
            adapter_type TEXT NOT NULL,
            provenance TEXT NOT NULL,
            UNIQUE(source_id, interface_name),
            FOREIGN KEY(source_id)
                REFERENCES management_sources(id)
                ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE INDEX idx_management_interfaces_participant
        ON management_interface_inventory(participant_kind, participant_id)
    """)
    conn.execute("""
        CREATE INDEX idx_management_interfaces_collected
        ON management_interface_inventory(source_id, collected_at)
    """)
    conn.execute("""
        CREATE TABLE management_collection_status (
            source_id TEXT NOT NULL,
            capability TEXT NOT NULL CHECK (
                capability IN (
                    'interface_inventory',
                    'bridge_fdb',
                    'wireless_associations',
                    'neighbours'
                )
            ),
            status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
            item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
            collected_at TEXT NOT NULL,
            error_category TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source_id, capability),
            FOREIGN KEY(source_id)
                REFERENCES management_sources(id)
                ON DELETE CASCADE
        )
    """)


DEFAULT_MIGRATIONS = (
    Migration("20260813_01_management_foundation", _management_foundation),
    Migration("20260814_01_management_interface_inventory", _management_interface_inventory),
)


def apply_migrations(
    conn: sqlite3.Connection,
    migrations: Iterable[Migration] = DEFAULT_MIGRATIONS,
) -> None:
    """Apply each pending migration once, rolling back failed migrations."""

    migrations = tuple(migrations)
    migration_ids = [migration.migration_id for migration in migrations]
    if any(not migration_id for migration_id in migration_ids) or len(
        set(migration_ids)
    ) != len(migration_ids):
        raise ValueError("Migration identifiers must be non-empty and unique.")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    try:
        conn.execute("BEGIN IMMEDIATE")
        applied = {
            row[0]
            for row in conn.execute(
                "SELECT migration_id FROM schema_migrations"
            ).fetchall()
        }

        for migration in migrations:
            if migration.migration_id in applied:
                continue

            migration.apply(conn)
            conn.execute(
                "INSERT INTO schema_migrations (migration_id) VALUES (?)",
                (migration.migration_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
