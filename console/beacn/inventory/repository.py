from __future__ import annotations

import json
import sqlite3
from typing import Iterable
from uuid import UUID

from beacn.core import Device, Observation
from beacn.core.observation import (
    ATTACHMENT_FIELD,
    ATTACHMENT_FRESHNESS_SECONDS,
    select_current_attachment,
)
from beacn.database import Database


class DeviceRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _device_from_row(row: sqlite3.Row) -> Device:
        return Device(
            id=row["id"],
            hostname=row["hostname"] or None,
            display_name=row["display_name"] or None,
            primary_ip=row["ip"] or None,
            primary_mac=row["mac"] or None,
            vendor=row["vendor"] or None,
            os_name=row["os_name"] or None,
            os_version=row["os_version"] or None,
            device_type=row["device_type"] or None,
            is_online=bool(row["is_online"]),
            agent_installed=bool(row["agent_available"]),
            agent_version=row["agent_version"] or None,
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )

    def list(self) -> list[Device]:
        with self.database.connect() as conn:
            rows = conn.execute("""
                SELECT id, ip, hostname, display_name, mac, vendor,
                       os_name, os_version, device_type, is_online,
                       agent_available, agent_version, first_seen, last_seen
                FROM devices
                ORDER BY is_online DESC, hostname COLLATE NOCASE, ip
            """).fetchall()
        return [self._device_from_row(row) for row in rows]

    def get(self, device_id: str) -> Device | None:
        try:
            canonical_id = str(UUID(device_id))
        except (ValueError, TypeError):
            return None

        with self.database.connect() as conn:
            row = conn.execute("""
                SELECT id, ip, hostname, display_name, mac, vendor,
                       os_name, os_version, device_type, is_online,
                       agent_available, agent_version, first_seen, last_seen
                FROM devices WHERE id = ?
            """, (canonical_id,)).fetchone()
        return self._device_from_row(row) if row else None

    def get_by_ip(self, ip: str) -> Device | None:
        with self.database.connect() as conn:
            row = conn.execute("""
                SELECT id, ip, hostname, display_name, mac, vendor,
                       os_name, os_version, device_type, is_online,
                       agent_available, agent_version, first_seen, last_seen
                FROM devices WHERE ip = ?
            """, (ip,)).fetchone()
        return self._device_from_row(row) if row else None

    def save(self, device: Device) -> Device:
        with self.database.connect() as conn:
            conn.execute("""
                INSERT INTO devices (
                    id, ip, hostname, display_name, mac, vendor,
                    os_name, os_version, device_type, is_online,
                    agent_available, agent_version, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ip) DO UPDATE SET
                    id = COALESCE(devices.id, excluded.id),
                    hostname = excluded.hostname,
                    display_name = excluded.display_name,
                    mac = excluded.mac,
                    vendor = excluded.vendor,
                    os_name = excluded.os_name,
                    os_version = excluded.os_version,
                    device_type = excluded.device_type,
                    is_online = excluded.is_online,
                    agent_available = excluded.agent_available,
                    agent_version = excluded.agent_version,
                    last_seen = excluded.last_seen
            """, (
                device.id, device.primary_ip, device.hostname,
                device.display_name, device.primary_mac, device.vendor,
                device.os_name, device.os_version, device.device_type,
                int(device.is_online), int(device.agent_installed),
                device.agent_version, device.first_seen, device.last_seen,
            ))
        return self.get_by_ip(device.primary_ip or "") or device

    def add_observation(self, observation: Observation) -> None:
        with self.database.connect() as conn:
            conn.execute("""
                INSERT INTO observations (
                    device_id, source, field, value_json,
                    confidence, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                observation.device_id,
                observation.source,
                observation.field,
                json.dumps(observation.value, separators=(",", ":")),
                observation.confidence,
                observation.observed_at,
            ))

    def observations(self, device_id: str, limit: int = 100) -> Iterable[dict]:
        limit = max(1, min(int(limit), 1000))
        with self.database.connect() as conn:
            rows = conn.execute("""
                SELECT source, field, value_json, confidence, observed_at
                FROM observations
                WHERE device_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (device_id, limit)).fetchall()
        for row in rows:
            item = dict(row)
            item["value"] = json.loads(item.pop("value_json"))
            yield item

    def current_attachment(
        self,
        device_id: str,
        source: str,
        *,
        now=None,
        freshness_seconds: int = ATTACHMENT_FRESHNESS_SECONDS,
    ) -> dict:
        with self.database.connect() as conn:
            rows = conn.execute("""
                SELECT source, field, value_json, confidence, observed_at
                FROM observations
                WHERE device_id = ? AND source = ? AND field = ?
                ORDER BY observed_at DESC, id DESC
            """, (device_id, source, ATTACHMENT_FIELD)).fetchall()

        observations = []
        for row in rows:
            item = dict(row)
            try:
                item["value"] = json.loads(item.pop("value_json"))
            except (json.JSONDecodeError, TypeError):
                continue
            observations.append(item)

        return select_current_attachment(
            observations,
            now=now,
            freshness_seconds=freshness_seconds,
        )
