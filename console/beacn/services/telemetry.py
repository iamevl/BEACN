"""Telemetry processing and persistence for BEACN agents."""

import json
import re
from datetime import datetime, timedelta, timezone

from beacn.config import TELEMETRY_RETENTION_DAYS


def prune_telemetry(conn):
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=TELEMETRY_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    conn.execute(
        "DELETE FROM telemetry_history WHERE created_at < ?",
        (cutoff,),
    )


def _finite(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hardware_nodes(hardware):
    result = []
    def visit(node):
        if not isinstance(node, dict):
            return
        result.append(node)
        for child in node.get("subHardware", []) or []:
            visit(child)
    for node in hardware.get("hardware", []) or []:
        visit(node)
    return result


def _sensor(node, sensor_type, names=()):
    sensors = [
        item for item in (node or {}).get("sensors", []) or []
        if item.get("type") == sensor_type and _finite(item.get("value")) is not None
    ]
    for name in names:
        for item in sensors:
            if str(item.get("name", "")).lower() == name.lower():
                return _finite(item.get("value"))
    return _finite(sensors[0].get("value")) if sensors else None


def telemetry_metrics(agent_payload):
    hardware = agent_payload.get("hardware", {}) or {}
    nodes = _hardware_nodes(hardware)
    cpu = next((n for n in nodes if str(n.get("type", "")).lower() == "cpu"), {})
    gpu = next((n for n in nodes if str(n.get("type", "")).lower().startswith("gpu")), {})
    clocks = [
        _finite(item.get("value"))
        for item in cpu.get("sensors", []) or []
        if item.get("type") == "Clock"
        and re.match(r"^(P-Core|E-Core|CPU Core)", str(item.get("name", "")), re.I)
    ]
    clocks = [value for value in clocks if value is not None]
    return {
        "cpu_temperature_c": _finite(hardware.get("summary", {}).get("cpuTemperatureC")),
        "cpu_power_w": _finite(hardware.get("summary", {}).get("cpuPowerW")),
        "cpu_clock_mhz": max(clocks) if clocks else None,
        "gpu_load_percent": _sensor(gpu, "Load", ("GPU Core", "D3D 3D", "GPU Total")),
        "gpu_temperature_c": _sensor(gpu, "Temperature", ("GPU Core", "GPU Hot Spot")),
        "gpu_power_w": _sensor(gpu, "Power", ("GPU Power",)),
    }


def save_telemetry(conn, target_ip, agent_payload, created_at):
    performance = agent_payload.get("performance", {})
    device_info = agent_payload.get("device", {})
    metrics = telemetry_metrics(agent_payload)

    conn.execute("""
        INSERT INTO telemetry_history (
            target_ip, cpu_percent, memory_percent,
            memory_available_bytes, uptime_seconds,
            cpu_temperature_c, cpu_power_w, cpu_clock_mhz,
            gpu_load_percent, gpu_temperature_c, gpu_power_w,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        target_ip, performance.get("cpu_percent"),
        performance.get("memory_percent"),
        performance.get("memory_available_bytes"),
        device_info.get("uptime_seconds"),
        metrics["cpu_temperature_c"], metrics["cpu_power_w"],
        metrics["cpu_clock_mhz"], metrics["gpu_load_percent"],
        metrics["gpu_temperature_c"], metrics["gpu_power_w"],
        created_at,
    ))

def update_device_from_agent(conn, target_ip, agent_payload, seen_at):
    performance = agent_payload.get("performance", {})
    device_info = agent_payload.get("device", {})
    agent_info = agent_payload.get("agent", {})
    services = agent_payload.get("services", {})
    identity = agent_payload.get("identity", {})

    conn.execute("""
        UPDATE devices
        SET agent_available = 1,
            agent_version = ?,
            agent_hostname = ?,
            os_name = COALESCE(NULLIF(?, ''), os_name),
            os_version = COALESCE(NULLIF(?, ''), os_version),
            device_type = COALESCE(NULLIF(?, ''), device_type),
            cpu_percent = ?,
            memory_percent = ?,
            uptime_seconds = ?,
            agent_last_seen = ?,
            agent_payload = ?,
            iperf_available = ?
        WHERE ip = ?
    """, (
        str(agent_info.get("version", "")).strip(),
        str(device_info.get("hostname", "")).strip(),
        str(identity.get("os_name", "")).strip(),
        str(identity.get("os_version", "")).strip(),
        str(identity.get("device_type", "")).strip(),
        performance.get("cpu_percent"),
        performance.get("memory_percent"),
        device_info.get("uptime_seconds"),
        seen_at,
        json.dumps(agent_payload, separators=(",", ":")),
        int(bool(services.get("iperf3", {}).get("running", False))),
        target_ip,
    ))

    save_telemetry(conn, target_ip, agent_payload, seen_at)
