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

app = Flask(__name__)

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")



def db():
    """Compatibility connection helper for legacy routes during v0.10."""
    return database.connect()


def init_db():
    with db_write_lock:
        with db() as conn:
            initialise_schema(conn)



def normalise_windows_name(agent_payload):
    if not agent_payload:
        return agent_payload

    operating_system = agent_payload.get("operating_system", {})
    product_name = str(operating_system.get("product_name", ""))
    build_text = str(operating_system.get("build", ""))

    try:
        build_number = int(build_text.split(".", 1)[0])
    except (TypeError, ValueError):
        build_number = 0

    if build_number >= 22000 and product_name.startswith("Windows 10"):
        operating_system["product_name"] = product_name.replace(
            "Windows 10",
            "Windows 11",
            1,
        )

    return agent_payload


def scan_network():
    if not scan_lock.acquire(blocking=False):
        return

    scan_state["running"] = True
    scan_state["last_error"] = None

    try:
        result = run_command(
            ["nmap", "-sn", "-n", NETWORK_SUBNET],
            timeout=SCAN_TIMEOUT,
        )

        if not result["ok"] and not result["stdout"]:
            raise RuntimeError(result["stderr"] or "Network scan failed.")

        found = parse_nmap_discovery(result["stdout"])
        now = utc_now()

        with db_write_lock:
            with db() as conn:
                conn.execute("UPDATE devices SET is_online = 0")

                for device in found:
                    ip = device["ip"]

                    if not valid_target(ip):
                        continue

                    discovered_hostname = device["hostname"] or reverse_dns(ip)
                    agent = normalise_windows_name(fetch_agent_status(ip))

                    agent_available = int(agent is not None)
                    agent_hostname = ""
                    agent_version = ""
                    cpu_percent = None
                    memory_percent = None
                    uptime_seconds = None
                    agent_last_seen = None
                    agent_payload = None

                    if agent:
                        agent_hostname = str(
                            agent.get("device", {}).get("hostname", "")
                        ).strip()
                        agent_version = str(
                            agent.get("agent", {}).get("version", "")
                        ).strip()
                        performance = agent.get("performance", {})
                        cpu_percent = performance.get("cpu_percent")
                        memory_percent = performance.get("memory_percent")
                        uptime_seconds = agent.get("device", {}).get("uptime_seconds")
                        agent_last_seen = now
                        agent_payload = json.dumps(agent, separators=(",", ":"))
                        iperf_available = int(
                            bool(
                                agent.get("services", {})
                                .get("iperf3", {})
                                .get("running", False)
                            )
                        )
                    else:
                        iperf_available = int(tcp_open(ip, IPERF_PORT))

                    hostname = agent_hostname or discovered_hostname or ip
                    existing = conn.execute(
                        "SELECT id, first_seen FROM devices WHERE ip = ?",
                        (ip,),
                    ).fetchone()
                    device_id = existing["id"] if existing and existing["id"] else str(uuid4())
                    first_seen = existing["first_seen"] if existing else now

                    conn.execute("""
                        INSERT INTO devices (
                            id, ip, hostname, mac, vendor, is_online,
                            iperf_available, first_seen, last_seen,
                            agent_available, agent_version, agent_hostname,
                            cpu_percent, memory_percent, uptime_seconds,
                            agent_last_seen, agent_payload
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, 1, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(ip) DO UPDATE SET
                            hostname = excluded.hostname,
                            mac = CASE
                                WHEN excluded.mac <> '' THEN excluded.mac
                                ELSE devices.mac
                            END,
                            vendor = CASE
                                WHEN excluded.vendor <> '' THEN excluded.vendor
                                ELSE devices.vendor
                            END,
                            is_online = 1,
                            iperf_available = excluded.iperf_available,
                            last_seen = excluded.last_seen,
                            agent_available = excluded.agent_available,
                            agent_version = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.agent_version
                                ELSE devices.agent_version
                            END,
                            agent_hostname = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.agent_hostname
                                ELSE devices.agent_hostname
                            END,
                            cpu_percent = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.cpu_percent
                                ELSE devices.cpu_percent
                            END,
                            memory_percent = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.memory_percent
                                ELSE devices.memory_percent
                            END,
                            uptime_seconds = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.uptime_seconds
                                ELSE devices.uptime_seconds
                            END,
                            agent_last_seen = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.agent_last_seen
                                ELSE devices.agent_last_seen
                            END,
                            agent_payload = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.agent_payload
                                ELSE devices.agent_payload
                            END
                    """, (
                        device_id,
                        ip,
                        hostname,
                        device["mac"],
                        device["vendor"],
                        iperf_available,
                        first_seen,
                        now,
                        agent_available,
                        agent_version,
                        agent_hostname,
                        cpu_percent,
                        memory_percent,
                        uptime_seconds,
                        agent_last_seen,
                        agent_payload,
                    ))

                    if agent:
                        save_telemetry(conn, ip, agent, now)

                prune_telemetry(conn)

    except Exception as exc:
        scan_state["last_error"] = str(exc)

    finally:
        scan_state["running"] = False
        scan_lock.release()


def collect_agent_metrics():
    while True:
        try:
            with db() as conn:
                rows = conn.execute(
                    "SELECT ip FROM devices WHERE agent_available = 1 AND is_online = 1"
                ).fetchall()
            for row in rows:
                payload = normalise_windows_name(fetch_agent_status(row["ip"]))
                if payload:
                    with db_write_lock:
                        with db() as conn:
                            update_device_from_agent(conn, row["ip"], payload, utc_now())
                            prune_telemetry(conn)
            if str(scan_state.get("last_error") or "").startswith("Metrics collector:"):
                scan_state["last_error"] = None
        except Exception as exc:
            scan_state["last_error"] = f"Metrics collector: {exc}"
        threading.Event().wait(METRICS_INTERVAL_SECONDS)


def parse_iperf_json(raw):
    try:
        payload = json.loads(raw)
        end = payload.get("end", {})
        summary = end.get("sum_received") or end.get("sum_sent") or {}

        return (
            float(summary.get("bits_per_second", 0)),
            int(summary.get("retransmits", 0))
            if summary.get("retransmits") is not None
            else None,
        )
    except Exception:
        return (None, None)


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
                agent_last_seen, os_name, os_version, device_type
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

    return jsonify({
        "devices": payload,
        "scan": scan_state,
        "subnet": NETWORK_SUBNET,
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

    if not tcp_open(target, IPERF_PORT, timeout=1):
        return jsonify({
            "ok": False,
            "error": f"No iperf3 server detected on {target}:{IPERF_PORT}.",
        }), 400

    args = [
        "iperf3", "-c", target,
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
                target,
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
