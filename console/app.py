import ipaddress
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
import urllib.error
import urllib.request
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, render_template, request

from beacn.database import initialise_schema
from beacn.services.health import get_health_summary
try:
    from docker.errors import DockerException
except ImportError:
    DockerException = Exception

from beacn.config import (
    AGENT_PORT,
    AGENT_TIMEOUT,
    APP_NAME,
    APP_PORT,
    APP_STAGE,
    APP_VERSION,
    COMMAND_TIMEOUT,
    DATA_DIR,
    IPERF_PORT,
    METRICS_INTERVAL_SECONDS,
    NETWORK_SUBNET,
    SCAN_TIMEOUT,
    TELEMETRY_MAX_POINTS,
    TELEMETRY_RETENTION_DAYS,
)

from beacn.services.scanner import (
    scan_network,
    collect_agent_metrics,
    parse_iperf_json,
)

from beacn.services.commands import (
    normalize_target,
    run_command,
    valid_target,
)

from beacn.services.agent import (
    fetch_agent_json,
    fetch_agent_status,
    tcp_open,
)

from beacn.services.discovery import (
    parse_nmap_discovery,
    reverse_dns,
)

from beacn.services.telemetry import (
    prune_telemetry,
    save_telemetry,
    update_device_from_agent,
)
from beacn.services.docker_monitor import (
    docker_snapshot,
)

from beacn.runtime import (
    database,
    repository,
    scan_lock,
    db_write_lock,
    scan_state,
)

from beacn.common import (
    db,
    normalise_windows_name,
    utc_now,
)

app = Flask(__name__)

def init_db():
    with db_write_lock:
        with db() as conn:
            initialise_schema(conn)


@app.get("/")
def index():
    return render_template(
        "index.html",
        subnet=NETWORK_SUBNET,
        iperf_port=IPERF_PORT,
        app_name=APP_NAME,
        app_version=APP_VERSION,
        app_stage=APP_STAGE,
    )

@app.get("/api/health")
def api_health():
    return jsonify(get_health_summary())

@app.get("/api/devices")
def devices():
    """Return canonical Device objects while preserving legacy UI fields."""
    canonical = {device.primary_ip: device for device in repository.list()}

    with db() as conn:
        rows = conn.execute("""
            SELECT
                id, ip, hostname, display_name, mac, vendor, is_online,
                iperf_available, first_seen, last_seen,
                agent_available, agent_version, agent_hostname,
                cpu_percent, memory_percent, uptime_seconds,
                agent_last_seen, os_name, os_version, device_type,
                device_type_source
            FROM devices
            ORDER BY is_online DESC, ip
        """).fetchall()

    payload = []
    for row in rows:
        item = dict(row)
        device = canonical.get(row["ip"])
        if device:
            item["device_id"] = device.id
            item["primary_ip"] = device.primary_ip
            item["primary_mac"] = device.primary_mac
            item["agent_installed"] = device.agent_installed
        payload.append(item)

    def device_ip_sort_key(item):
        try:
            address = ipaddress.ip_address(
                str(item.get("ip", "")).strip()
            )
            return (
                address.version,
                int(address),
            )
        except ValueError:
            return (99, 0)

    payload.sort(key=device_ip_sort_key)

    return jsonify({
        "devices": payload,
        "scan": scan_state,
        "subnet": NETWORK_SUBNET,
    })


@app.get("/api/device-types")
def device_type_summary():
    """Return inventory totals grouped by classified device type."""
    with db() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(
                    NULLIF(device_type, ''),
                    'unknown'
                ) AS device_type,
                COUNT(*) AS total
            FROM devices
            GROUP BY device_type
            ORDER BY total DESC, device_type
        """).fetchall()

    types = [
        {
            "device_type": row["device_type"],
            "total": row["total"],
        }
        for row in rows
    ]

    with db() as conn:
        status = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN is_online = 1 THEN 1
                        ELSE 0
                    END
                ) AS online
            FROM devices
        """).fetchone()

    total = int(status["total"] or 0)
    online = int(status["online"] or 0)

    return jsonify({
        "total": total,
        "online": online,
        "offline": max(0, total - online),
        "types": types,
    })


DEVICE_TYPES = {
    "access_point",
    "appliance",
    "camera",
    "computer",
    "doorbell",
    "game_console",
    "iot",
    "media_tuner",
    "nas",
    "phone",
    "raspberry_pi",
    "router",
    "speaker",
    "switch",
    "television",
    "unknown",
    "ups",
}


@app.post("/api/device/<target>/identity")
def update_device_identity(target):
    """Store a manually managed friendly name and device type."""
    if not valid_target(target):
        return jsonify({
            "ok": False,
            "error": "Invalid target.",
        }), 400

    payload = request.get_json(silent=True) or {}

    display_name = str(
        payload.get("display_name", "")
    ).strip()

    device_type = str(
        payload.get("device_type", "")
    ).strip().lower()

    if len(display_name) > 100:
        return jsonify({
            "ok": False,
            "error": "Friendly name must be 100 characters or fewer.",
        }), 400

    if device_type not in DEVICE_TYPES:
        return jsonify({
            "ok": False,
            "error": "Unsupported device type.",
        }), 400

    with db_write_lock:
        with db() as conn:
            row = conn.execute(
                "SELECT id FROM devices WHERE ip = ?",
                (target,),
            ).fetchone()

            if not row:
                return jsonify({
                    "ok": False,
                    "error": "Device not found.",
                }), 404

            conn.execute("""
                UPDATE devices
                SET display_name = ?,
                    device_type = ?,
                    device_type_source = 'manual'
                WHERE ip = ?
            """, (
                display_name,
                device_type,
                target,
            ))

            updated = conn.execute(
                """
                SELECT
                    id,
                    ip,
                    hostname,
                    display_name,
                    device_type,
                    device_type_source
                FROM devices
                WHERE ip = ?
                """,
                (target,),
            ).fetchone()

            conn.commit()

    return jsonify({
        "ok": True,
        "device": dict(updated),
    })


@app.get("/api/devices/<device_id>")
def canonical_device_details(device_id):
    device = repository.get(device_id)
    if not device:
        return jsonify({"ok": False, "error": "Device not found."}), 404

    return jsonify({
        "ok": True,
        "device": device.to_dict(),
        "observations": list(repository.observations(device.id, limit=100)),
    })


@app.get("/api/device/<target>")
def device_details(target):
    if not valid_target(target):
        return jsonify({"ok": False, "error": "Invalid target."}), 400

    refresh = request.args.get("refresh", "0") == "1"
    fresh_agent = (
        normalise_windows_name(fetch_agent_status(target))
        if refresh
        else None
    )

    database_guard = db_write_lock if fresh_agent else nullcontext()

    with database_guard:
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE ip = ?",
                (target,),
            ).fetchone()

            if not row:
                return jsonify({"ok": False, "error": "Device not found."}), 404

            if fresh_agent:
                now = utc_now()
                update_device_from_agent(conn, target, fresh_agent, now)
                prune_telemetry(conn)
                row = conn.execute(
                    "SELECT * FROM devices WHERE ip = ?",
                    (target,),
                ).fetchone()

            agent_payload = None
            if row["agent_payload"]:
                try:
                    agent_payload = normalise_windows_name(
                        json.loads(row["agent_payload"])
                    )
                except json.JSONDecodeError:
                    agent_payload = None

    return jsonify({
        "ok": True,
        "device": {
            key: row[key]
            for key in row.keys()
            if key != "agent_payload"
        },
        "agent": agent_payload,
    })


@app.get("/api/telemetry/<target>")
def telemetry(target):
    if not valid_target(target):
        return jsonify({"ok": False, "error": "Invalid target."}), 400

    ranges = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
    selected_range = request.args.get("range", "1h")
    hours = ranges.get(selected_range, 1)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")

    try:
        requested_limit = int(request.args.get("limit", str(TELEMETRY_MAX_POINTS)))
    except ValueError:
        requested_limit = TELEMETRY_MAX_POINTS
    limit = max(10, min(requested_limit, TELEMETRY_MAX_POINTS))

    with db() as conn:
        rows = conn.execute("""
            SELECT cpu_percent, memory_percent, memory_available_bytes,
                   uptime_seconds, cpu_temperature_c, cpu_power_w,
                   cpu_clock_mhz, gpu_load_percent, gpu_temperature_c,
                   gpu_power_w, created_at
            FROM telemetry_history
            WHERE target_ip = ? AND created_at >= ?
            ORDER BY id DESC
            LIMIT ?
        """, (target, cutoff, limit)).fetchall()

    return jsonify({
        "ok": True,
        "target": target,
        "range": selected_range,
        "interval_seconds": METRICS_INTERVAL_SECONDS,
        "points": [dict(row) for row in reversed(rows)],
    })


def unavailable_docker_payload(error, source="agent"):
    return {
        "available": False,
        "source": source,
        "error": "Docker telemetry is currently unavailable.",
        "engine": {
            "containers_total": 0,
            "containers_running": 0,
            "containers_stopped": 0,
            "containers_healthy": 0,
            "containers_unhealthy": 0,
        },
        "containers": [],
        "collected_at": utc_now(),
    }


@app.get("/api/docker")
def docker_overview():
    """Legacy local Docker endpoint retained for compatibility."""
    try:
        payload = docker_snapshot()
        payload["source"] = "dashboard-host"
        return jsonify(payload)
    except (DockerException, RuntimeError, OSError) as exc:
        app.logger.exception("Failed to collect local Docker telemetry: %s", exc)
        return jsonify(unavailable_docker_payload(exc, "dashboard-host"))


@app.get("/api/docker/<target>")
def docker_for_device(target):
    if not valid_target(target):
        return jsonify(unavailable_docker_payload("Invalid target.")), 400

    with db() as conn:
        row = conn.execute(
            "SELECT agent_available, agent_hostname FROM devices WHERE ip = ?",
            (target,),
        ).fetchone()

    if not row:
        return jsonify(unavailable_docker_payload("Device not found.")), 404

    if not row["agent_available"]:
        return jsonify(unavailable_docker_payload(
            "The selected device does not have a BEACN Agent."
        ))

    payload = fetch_agent_json(target, "/docker")
    if payload is None:
        return jsonify(unavailable_docker_payload(
            f"The agent on {target} did not return Docker telemetry."
        ))

    payload.setdefault("source", "agent")
    payload["target_ip"] = target
    payload["target_hostname"] = row["agent_hostname"] or target
    return jsonify(payload)




@app.post("/api/scan")
def trigger_scan():
    if scan_state["running"]:
        return jsonify({"ok": True, "message": "A scan is already running."})

    threading.Thread(target=scan_network, daemon=True).start()
    return jsonify({"ok": True, "message": "Network scan started."})


@app.post("/api/ping")
def ping():
    target = (request.json or {}).get("target", "")

    if not valid_target(target):
        return jsonify({
            "ok": False,
            "error": "Target is outside the configured subnet.",
        }), 400

    safe_target = normalize_target(target)

    return jsonify(run_command(
        ["ping", "-c", "4", "-W", "2", safe_target],
        timeout=12,
    ))


@app.post("/api/ports")
def ports():
    target = (request.json or {}).get("target", "")

    if not valid_target(target):
        return jsonify({
            "ok": False,
            "error": "Target is outside the configured subnet.",
        }), 400

    safe_target = normalize_target(target)

    return jsonify(run_command(
        ["nmap", "-Pn", "-T4", "--top-ports", "100", safe_target],
        timeout=45,
    ))


@app.post("/api/iperf")
def iperf():
    body = request.json or {}
    target = body.get("target", "")
    reverse = bool(body.get("reverse", False))

    if not valid_target(target):
        return jsonify({
            "ok": False,
            "error": "Target is outside the configured subnet.",
        }), 400

    safe_target = normalize_target(target)

    if not tcp_open(safe_target, IPERF_PORT, timeout=1):
        return jsonify({
            "ok": False,
            "error": f"No iperf3 server detected on {safe_target}:{IPERF_PORT}.",
        }), 400

    args = [
        "iperf3", "-c", safe_target,
        "-p", str(IPERF_PORT),
        "-J", "-t", "10",
    ]
    direction = "reverse" if reverse else "forward"

    if reverse:
        args.append("-R")

    result = run_command(args, timeout=25)
    bits_per_second, retransmits = parse_iperf_json(result["stdout"])
    raw_output = result["stdout"] or result["stderr"]

    with db_write_lock:
        with db() as conn:
            conn.execute("""
                INSERT INTO iperf_results
                    (target_ip, direction, bits_per_second,
                     retransmits, raw_output, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                safe_target,
                direction,
                bits_per_second,
                retransmits,
                raw_output,
                utc_now(),
            ))

    result["bits_per_second"] = bits_per_second
    result["retransmits"] = retransmits
    result["direction"] = direction
    return jsonify(result)


@app.get("/api/results")
def results():
    target = request.args.get("target", "")

    with db() as conn:
        if target and valid_target(target):
            rows = conn.execute("""
                SELECT id, target_ip, direction, bits_per_second,
                       retransmits, created_at
                FROM iperf_results
                WHERE target_ip = ?
                ORDER BY id DESC LIMIT 50
            """, (target,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, target_ip, direction, bits_per_second,
                       retransmits, created_at
                FROM iperf_results
                ORDER BY id DESC LIMIT 50
            """).fetchall()

    return jsonify({"results": [dict(row) for row in rows]})


if __name__ == "__main__":
    init_db()
    threading.Thread(target=scan_network, daemon=True).start()
    threading.Thread(target=collect_agent_metrics, daemon=True).start()
    app.run(host="0.0.0.0", port=APP_PORT, threaded=True)
