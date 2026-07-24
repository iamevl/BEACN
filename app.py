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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

try:
    from version import APP_NAME, APP_STAGE, APP_VERSION
except ImportError:
    APP_NAME = "Network Dashboard"
    APP_VERSION = "0.4.0"
    APP_STAGE = "Live Monitoring"

APP_PORT = int(os.getenv("APP_PORT", "8766"))
NETWORK_SUBNET = os.getenv("NETWORK_SUBNET", "192.168.1.0/24")
IPERF_PORT = int(os.getenv("IPERF_PORT", "5201"))
AGENT_PORT = int(os.getenv("AGENT_PORT", "8767"))
AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "1.5"))
SCAN_TIMEOUT = int(os.getenv("SCAN_TIMEOUT", "90"))
COMMAND_TIMEOUT = int(os.getenv("COMMAND_TIMEOUT", "20"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "network-dashboard.db"
TELEMETRY_RETENTION_DAYS = int(os.getenv("TELEMETRY_RETENTION_DAYS", "30"))
TELEMETRY_MAX_POINTS = int(os.getenv("TELEMETRY_MAX_POINTS", "240"))

app = Flask(__name__)
scan_lock = threading.Lock()
scan_state = {"running": False, "last_error": None}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
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
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_telemetry_target_created
        ON telemetry_history(target_ip, created_at);

        CREATE INDEX IF NOT EXISTS idx_iperf_target_created
        ON iperf_results(target_ip, created_at);
        """)

        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(devices)").fetchall()
        }

        migrations = {
            "agent_available": "INTEGER NOT NULL DEFAULT 0",
            "agent_version": "TEXT",
            "agent_hostname": "TEXT",
            "cpu_percent": "REAL",
            "memory_percent": "REAL",
            "uptime_seconds": "INTEGER",
            "agent_last_seen": "TEXT",
            "agent_payload": "TEXT",
        }

        for column, definition in migrations.items():
            if column not in existing_columns:
                conn.execute(
                    f"ALTER TABLE devices ADD COLUMN {column} {definition}"
                )


def valid_target(value):
    try:
        ip = ipaddress.ip_address(value)
        subnet = ipaddress.ip_network(NETWORK_SUBNET, strict=False)
        return ip in subnet
    except ValueError:
        return False


def run_command(args, timeout=COMMAND_TIMEOUT):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": 124,
            "stdout": (exc.stdout or "").strip()
            if isinstance(exc.stdout, str)
            else "",
            "stderr": f"Command timed out after {timeout} seconds.",
        }


def tcp_open(ip, port, timeout=0.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def fetch_agent_status(ip):
    url = f"http://{ip}:{AGENT_PORT}/status"
    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
    ):
        return None


def reverse_dns(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return ""


def parse_nmap_discovery(output):
    devices = []
    current = None

    for line in output.splitlines():
        line = line.strip()

        if line.startswith("Nmap scan report for "):
            value = line.replace("Nmap scan report for ", "", 1)
            hostname = ""
            ip = value
            match = re.match(r"(.+?) \(([\d.]+)\)$", value)

            if match:
                hostname, ip = match.group(1), match.group(2)

            current = {
                "ip": ip,
                "hostname": hostname,
                "mac": "",
                "vendor": "",
            }
            devices.append(current)

        elif current and line.startswith("MAC Address:"):
            match = re.match(
                r"MAC Address:\s+([0-9A-F:]+)\s*(?:\((.*?)\))?$",
                line,
                re.I,
            )
            if match:
                current["mac"] = match.group(1).upper()
                current["vendor"] = match.group(2) or ""

    return devices


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


def prune_telemetry(conn):
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=TELEMETRY_RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    conn.execute(
        "DELETE FROM telemetry_history WHERE created_at < ?",
        (cutoff,),
    )


def save_telemetry(conn, target_ip, agent_payload, created_at):
    performance = agent_payload.get("performance", {})
    device_info = agent_payload.get("device", {})

    conn.execute("""
        INSERT INTO telemetry_history (
            target_ip,
            cpu_percent,
            memory_percent,
            memory_available_bytes,
            uptime_seconds,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        target_ip,
        performance.get("cpu_percent"),
        performance.get("memory_percent"),
        performance.get("memory_available_bytes"),
        device_info.get("uptime_seconds"),
        created_at,
    ))


def update_device_from_agent(conn, target_ip, agent_payload, seen_at):
    performance = agent_payload.get("performance", {})
    device_info = agent_payload.get("device", {})
    agent_info = agent_payload.get("agent", {})
    services = agent_payload.get("services", {})

    conn.execute("""
        UPDATE devices
        SET agent_available = 1,
            agent_version = ?,
            agent_hostname = ?,
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
        performance.get("cpu_percent"),
        performance.get("memory_percent"),
        device_info.get("uptime_seconds"),
        seen_at,
        json.dumps(agent_payload, separators=(",", ":")),
        int(bool(services.get("iperf3", {}).get("running", False))),
        target_ip,
    ))

    save_telemetry(conn, target_ip, agent_payload, seen_at)


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
                    "SELECT first_seen FROM devices WHERE ip = ?",
                    (ip,),
                ).fetchone()
                first_seen = existing["first_seen"] if existing else now

                conn.execute("""
                    INSERT INTO devices (
                        ip, hostname, mac, vendor, is_online,
                        iperf_available, first_seen, last_seen,
                        agent_available, agent_version, agent_hostname,
                        cpu_percent, memory_percent, uptime_seconds,
                        agent_last_seen, agent_payload
                    )
                    VALUES (
                        ?, ?, ?, ?, 1, ?, ?, ?,
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
    with db() as conn:
        rows = conn.execute("""
            SELECT
                ip, hostname, mac, vendor, is_online,
                iperf_available, first_seen, last_seen,
                agent_available, agent_version, agent_hostname,
                cpu_percent, memory_percent, uptime_seconds,
                agent_last_seen
            FROM devices
            ORDER BY is_online DESC, ip
        """).fetchall()

    return jsonify({
        "devices": [dict(row) for row in rows],
        "scan": scan_state,
        "subnet": NETWORK_SUBNET,
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

    try:
        requested_limit = int(request.args.get("limit", "120"))
    except ValueError:
        requested_limit = 120

    limit = max(10, min(requested_limit, TELEMETRY_MAX_POINTS))

    with db() as conn:
        rows = conn.execute("""
            SELECT
                cpu_percent,
                memory_percent,
                memory_available_bytes,
                uptime_seconds,
                created_at
            FROM telemetry_history
            WHERE target_ip = ?
            ORDER BY id DESC
            LIMIT ?
        """, (target, limit)).fetchall()

    return jsonify({
        "ok": True,
        "target": target,
        "points": [dict(row) for row in reversed(rows)],
    })


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

    return jsonify(run_command(
        ["ping", "-c", "4", "-W", "2", target],
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

    return jsonify(run_command(
        ["nmap", "-Pn", "-T4", "--top-ports", "100", target],
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
    app.run(host="0.0.0.0", port=APP_PORT, threaded=True)
