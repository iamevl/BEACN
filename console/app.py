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

from flask import Flask, jsonify, render_template, request

try:
    import docker
    from docker.errors import DockerException, NotFound
except ImportError:
    docker = None
    DockerException = Exception
    NotFound = Exception

try:
    from version import APP_NAME, APP_STAGE, APP_VERSION
except ImportError:
    APP_NAME = "BEACN"
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
DB_PATH = DATA_DIR / "beacn.db"
TELEMETRY_RETENTION_DAYS = int(os.getenv("TELEMETRY_RETENTION_DAYS", "30"))
TELEMETRY_MAX_POINTS = int(os.getenv("TELEMETRY_MAX_POINTS", "1000"))
METRICS_INTERVAL_SECONDS = max(5, int(os.getenv("METRICS_INTERVAL_SECONDS", "15")))
DOCKER_MONITORING_ENABLED = os.getenv("DOCKER_MONITORING_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
DOCKER_TIMEOUT_SECONDS = max(1, int(os.getenv("DOCKER_TIMEOUT_SECONDS", "5")))

app = Flask(__name__)
scan_lock = threading.Lock()
db_write_lock = threading.RLock()
scan_state = {"running": False, "last_error": None}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db_write_lock:
        with db() as conn:
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

            telemetry_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(telemetry_history)").fetchall()
            }
            telemetry_migrations = {
                "cpu_temperature_c": "REAL",
                "cpu_power_w": "REAL",
                "cpu_clock_mhz": "REAL",
                "gpu_load_percent": "REAL",
                "gpu_temperature_c": "REAL",
                "gpu_power_w": "REAL",
            }
            for column, definition in telemetry_migrations.items():
                if column not in telemetry_columns:
                    conn.execute(
                        f"ALTER TABLE telemetry_history ADD COLUMN {column} {definition}"
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


def fetch_agent_json(ip, path):
    clean_path = "/" + str(path).lstrip("/")
    url = f"http://{ip}:{AGENT_PORT}{clean_path}"
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


def fetch_agent_status(ip):
    return fetch_agent_json(ip, "/status")


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


def docker_client():
    if not DOCKER_MONITORING_ENABLED:
        raise RuntimeError("Docker monitoring is disabled.")
    if docker is None:
        raise RuntimeError("The Docker SDK is not installed.")
    return docker.from_env(timeout=DOCKER_TIMEOUT_SECONDS)


def docker_cpu_percent(stats):
    cpu_stats = stats.get("cpu_stats") or {}
    previous = stats.get("precpu_stats") or {}
    cpu_total = (
        (cpu_stats.get("cpu_usage") or {}).get("total_usage")
        or 0
    )
    previous_total = (
        (previous.get("cpu_usage") or {}).get("total_usage")
        or 0
    )
    system_total = cpu_stats.get("system_cpu_usage") or 0
    previous_system = previous.get("system_cpu_usage") or 0
    cpu_delta = cpu_total - previous_total
    system_delta = system_total - previous_system

    online_cpus = cpu_stats.get("online_cpus")
    if not online_cpus:
        percpu = (cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or []
        online_cpus = len(percpu) or 1

    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0

    return round((cpu_delta / system_delta) * online_cpus * 100.0, 2)


def docker_memory(stats):
    memory = stats.get("memory_stats") or {}
    usage = int(memory.get("usage") or 0)
    limit = int(memory.get("limit") or 0)
    cache = int(
        (memory.get("stats") or {}).get("inactive_file")
        or (memory.get("stats") or {}).get("cache")
        or 0
    )
    working_set = max(0, usage - cache)
    percent = (working_set / limit * 100.0) if limit else 0.0
    return working_set, limit, round(percent, 2)


def docker_network_totals(stats):
    networks = stats.get("networks") or {}
    received = sum(int(item.get("rx_bytes") or 0) for item in networks.values())
    transmitted = sum(int(item.get("tx_bytes") or 0) for item in networks.values())
    return received, transmitted


def docker_ports(attrs):
    ports = ((attrs.get("NetworkSettings") or {}).get("Ports") or {})
    output = []

    for container_port, bindings in ports.items():
        if not bindings:
            output.append(container_port)
            continue

        for binding in bindings:
            host_ip = binding.get("HostIp") or "0.0.0.0"
            host_port = binding.get("HostPort") or ""
            output.append(f"{host_ip}:{host_port} → {container_port}")

    return output


def docker_container_summary(container):
    container.reload()
    attrs = container.attrs or {}
    state = attrs.get("State") or {}
    config = attrs.get("Config") or {}

    stats = {}

    memory_used, memory_limit, memory_percent = docker_memory(stats)
    network_rx, network_tx = docker_network_totals(stats)

    health = (state.get("Health") or {}).get("Status")
    image_tags = getattr(container.image, "tags", None) or []
    image_name = image_tags[0] if image_tags else config.get("Image") or container.image.short_id

    return {
        "id": container.short_id,
        "name": container.name,
        "image": image_name,
        "status": container.status,
        "running": bool(state.get("Running")),
        "health": health,
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "created_at": attrs.get("Created"),
        "restart_count": int(attrs.get("RestartCount") or 0),
        "cpu_percent": docker_cpu_percent(stats),
        "memory_used_bytes": memory_used,
        "memory_limit_bytes": memory_limit,
        "memory_percent": memory_percent,
        "network_rx_bytes": network_rx,
        "network_tx_bytes": network_tx,
        "ports": docker_ports(attrs),
        "labels": config.get("Labels") or {},
    }


def docker_snapshot():
    client = docker_client()
    try:
        info = client.info()
        containers = [
            docker_container_summary(container)
            for container in client.containers.list(all=True)
        ]
    finally:
        client.close()

    containers.sort(key=lambda item: (not item["running"], item["name"].lower()))
    running = sum(1 for item in containers if item["running"])
    healthy = sum(1 for item in containers if item["health"] == "healthy")
    unhealthy = sum(1 for item in containers if item["health"] == "unhealthy")

    return {
        "available": True,
        "engine": {
            "name": info.get("Name"),
            "server_version": info.get("ServerVersion"),
            "operating_system": info.get("OperatingSystem"),
            "architecture": info.get("Architecture"),
            "containers_total": len(containers),
            "containers_running": running,
            "containers_stopped": len(containers) - running,
            "containers_healthy": healthy,
            "containers_unhealthy": unhealthy,
        },
        "containers": containers,
        "collected_at": utc_now(),
    }


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
        "error": str(error),
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
