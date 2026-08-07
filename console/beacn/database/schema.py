from __future__ import annotations

import sqlite3
from uuid import uuid4


DEVICE_COLUMN_MIGRATIONS = {
    "id": "TEXT",
    "display_name": "TEXT",
    "os_name": "TEXT",
    "os_version": "TEXT",
    "device_type": "TEXT",
    "device_type_source": "TEXT",
    "connection_method": "TEXT",
    "connection_parent_ip": "TEXT",
    "connection_source": "TEXT",
    "management_url": "TEXT",
    "notes": "TEXT",
    "agent_available": "INTEGER NOT NULL DEFAULT 0",
    "agent_version": "TEXT",
    "agent_hostname": "TEXT",
    "cpu_percent": "REAL",
    "memory_percent": "REAL",
    "uptime_seconds": "INTEGER",
    "agent_last_seen": "TEXT",
    "agent_payload": "TEXT",
}

TELEMETRY_COLUMN_MIGRATIONS = {
    "cpu_temperature_c": "REAL",
    "cpu_power_w": "REAL",
    "cpu_clock_mhz": "REAL",
    "gpu_load_percent": "REAL",
    "gpu_temperature_c": "REAL",
    "gpu_power_w": "REAL",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(
    conn: sqlite3.Connection,
    table: str,
    migrations: dict[str, str],
) -> None:
    existing = _columns(conn, table)
    for column, definition in migrations.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialise_schema(conn: sqlite3.Connection) -> None:
    """Create and migrate the v0.10 inventory schema without data loss.

    The existing IP-keyed table is retained during this compatibility release.
    Every row receives an immutable UUID and all new APIs use that UUID.
    A later migration can rebuild the table with ``id`` as the physical PK once
    legacy IP-based code has been removed.
    """

    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS devices (
        ip TEXT PRIMARY KEY,
        hostname TEXT,
        mac TEXT,
        vendor TEXT,
        is_online INTEGER NOT NULL DEFAULT 0,
        iperf_available INTEGER NOT NULL DEFAULT 0,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS iperf_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_ip TEXT NOT NULL,
        direction TEXT NOT NULL,
        bits_per_second REAL,
        retransmits INTEGER,
        raw_output TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS telemetry_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_ip TEXT NOT NULL,
        cpu_percent REAL,
        memory_percent REAL,
        memory_available_bytes INTEGER,
        uptime_seconds INTEGER,
        cpu_temperature_c REAL,
        cpu_power_w REAL,
        cpu_clock_mhz REAL,
        gpu_load_percent REAL,
        gpu_temperature_c REAL,
        gpu_power_w REAL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        source TEXT NOT NULL,
        field TEXT NOT NULL,
        value_json TEXT NOT NULL,
        confidence REAL,
        observed_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_telemetry_target_created
    ON telemetry_history(target_ip, created_at);

    CREATE INDEX IF NOT EXISTS idx_iperf_target_created
    ON iperf_results(target_ip, created_at);

    CREATE INDEX IF NOT EXISTS idx_observations_device_time
    ON observations(device_id, observed_at);
    """)

    _add_missing_columns(conn, "devices", DEVICE_COLUMN_MIGRATIONS)
    _add_missing_columns(conn, "telemetry_history", TELEMETRY_COLUMN_MIGRATIONS)

    conn.execute("""
        UPDATE devices
        SET connection_method = COALESCE(
                NULLIF(connection_method, ''),
                'automatic'
            ),
            connection_source = COALESCE(
                NULLIF(connection_source, ''),
                'inferred'
            )
        WHERE connection_method IS NULL
           OR connection_method = ''
           OR connection_source IS NULL
           OR connection_source = ''
    """)

    conn.execute("""
        UPDATE devices
        SET device_type_source = CASE
            WHEN agent_available = 1
                 AND NULLIF(device_type, '') IS NOT NULL
            THEN 'agent'
            WHEN NULLIF(device_type, '') IS NULL
                 OR device_type = 'unknown'
            THEN 'unknown'
            ELSE 'classifier'
        END
        WHERE device_type_source IS NULL
           OR device_type_source = ''
    """)

    rows = conn.execute("SELECT ip FROM devices WHERE id IS NULL OR id = ''").fetchall()
    for row in rows:
        conn.execute("UPDATE devices SET id = ? WHERE ip = ?", (str(uuid4()), row["ip"]))

    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_id ON devices(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_mac ON devices(mac)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_hostname ON devices(hostname)")
