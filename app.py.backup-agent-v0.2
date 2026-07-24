import ipaddress
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

APP_PORT = int(os.getenv("APP_PORT", "8766"))
NETWORK_SUBNET = os.getenv("NETWORK_SUBNET", "192.168.1.0/24")
IPERF_PORT = int(os.getenv("IPERF_PORT", "5201"))
SCAN_TIMEOUT = int(os.getenv("SCAN_TIMEOUT", "90"))
COMMAND_TIMEOUT = int(os.getenv("COMMAND_TIMEOUT", "20"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "network-dashboard.db"

app = Flask(__name__)
scan_lock = threading.Lock()
scan_state = {"running": False, "last_error": None}

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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
        """)

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
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": f"Command timed out after {timeout} seconds.",
        }

def tcp_open(ip, port, timeout=0.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False

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
            current = {"ip": ip, "hostname": hostname, "mac": "", "vendor": ""}
            devices.append(current)
        elif current and line.startswith("MAC Address:"):
            match = re.match(r"MAC Address:\s+([0-9A-F:]+)\s*(?:\((.*?)\))?$", line, re.I)
            if match:
                current["mac"] = match.group(1).upper()
                current["vendor"] = match.group(2) or ""
    return devices

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
        found_ips = set()

        with db() as conn:
            conn.execute("UPDATE devices SET is_online = 0")
            for device in found:
                ip = device["ip"]
                if not valid_target(ip):
                    continue
                found_ips.add(ip)
                hostname = device["hostname"] or reverse_dns(ip)
                iperf_available = int(tcp_open(ip, IPERF_PORT))
                existing = conn.execute(
                    "SELECT first_seen FROM devices WHERE ip = ?", (ip,)
                ).fetchone()
                first_seen = existing["first_seen"] if existing else now
                conn.execute("""
                    INSERT INTO devices
                        (ip, hostname, mac, vendor, is_online, iperf_available, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(ip) DO UPDATE SET
                        hostname = excluded.hostname,
                        mac = CASE WHEN excluded.mac <> '' THEN excluded.mac ELSE devices.mac END,
                        vendor = CASE WHEN excluded.vendor <> '' THEN excluded.vendor ELSE devices.vendor END,
                        is_online = 1,
                        iperf_available = excluded.iperf_available,
                        last_seen = excluded.last_seen
                """, (
                    ip, hostname, device["mac"], device["vendor"],
                    iperf_available, first_seen, now
                ))
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
            int(summary.get("retransmits", 0)) if summary.get("retransmits") is not None else None,
        )
    except Exception:
        return (None, None)

@app.get("/")
def index():
    return render_template(
        "index.html",
        subnet=NETWORK_SUBNET,
        iperf_port=IPERF_PORT,
    )

@app.get("/api/devices")
def devices():
    with db() as conn:
        rows = conn.execute("""
            SELECT ip, hostname, mac, vendor, is_online, iperf_available,
                   first_seen, last_seen
            FROM devices
            ORDER BY is_online DESC,
                     CAST(substr(ip, instr(ip, '.') + 1) AS INTEGER),
                     ip
        """).fetchall()
    return jsonify({
        "devices": [dict(row) for row in rows],
        "scan": scan_state,
        "subnet": NETWORK_SUBNET,
    })

@app.post("/api/scan")
def trigger_scan():
    if scan_state["running"]:
        return jsonify({"ok": True, "message": "A scan is already running."})
    threading.Thread(target=scan_network, daemon=True).start()
    return jsonify({"ok": True, "message": "Network scan started."})

@app.post("/api/ping")
def ping():
    target = request.json.get("target", "")
    if not valid_target(target):
        return jsonify({"ok": False, "error": "Target is outside the configured subnet."}), 400
    result = run_command(["ping", "-c", "4", "-W", "2", target], timeout=12)
    return jsonify(result)

@app.post("/api/ports")
def ports():
    target = request.json.get("target", "")
    if not valid_target(target):
        return jsonify({"ok": False, "error": "Target is outside the configured subnet."}), 400
    result = run_command(
        ["nmap", "-Pn", "-T4", "--top-ports", "100", target],
        timeout=45,
    )
    return jsonify(result)

@app.post("/api/iperf")
def iperf():
    body = request.json or {}
    target = body.get("target", "")
    reverse = bool(body.get("reverse", False))
    if not valid_target(target):
        return jsonify({"ok": False, "error": "Target is outside the configured subnet."}), 400
    if not tcp_open(target, IPERF_PORT, timeout=1):
        return jsonify({
            "ok": False,
            "error": f"No iperf3 server detected on {target}:{IPERF_PORT}."
        }), 400

    args = ["iperf3", "-c", target, "-p", str(IPERF_PORT), "-J", "-t", "10"]
    direction = "reverse" if reverse else "forward"
    if reverse:
        args.append("-R")

    result = run_command(args, timeout=25)
    bits_per_second, retransmits = parse_iperf_json(result["stdout"])
    raw_output = result["stdout"] or result["stderr"]

    with db() as conn:
        conn.execute("""
            INSERT INTO iperf_results
                (target_ip, direction, bits_per_second, retransmits, raw_output, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            target, direction, bits_per_second, retransmits,
            raw_output, utc_now()
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
                ORDER BY id DESC LIMIT 20
            """, (target,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, target_ip, direction, bits_per_second,
                       retransmits, created_at
                FROM iperf_results
                ORDER BY id DESC LIMIT 20
            """).fetchall()
    return jsonify({"results": [dict(row) for row in rows]})

if __name__ == "__main__":
    init_db()
    threading.Thread(target=scan_network, daemon=True).start()
    app.run(host="0.0.0.0", port=APP_PORT, threaded=True)
