#!/usr/bin/env python3
import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

try:
    import docker
    from docker.errors import DockerException
except ImportError:
    docker = None
    DockerException = Exception


VERSION = "0.7.1"
DEFAULT_CONFIG_PATH = Path("/etc/network-dashboard-agent/config.json")
CONFIG_PATH = Path(os.getenv("NETWORK_DASHBOARD_AGENT_CONFIG", str(DEFAULT_CONFIG_PATH)))
LOG_PATH = Path(os.getenv("NETWORK_DASHBOARD_AGENT_LOG", "/var/log/network-dashboard-agent.log"))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config():
    config = {
        "agent_port": 8767,
        "bind_address": "0.0.0.0",
        "api_token": "",
        "iperf_enabled": True,
        "iperf_path": shutil.which("iperf3") or "/usr/bin/iperf3",
        "iperf_port": 5201,
        "restart_delay_seconds": 3,
        "docker_enabled": True,
        "docker_timeout_seconds": 5,
    }

    if CONFIG_PATH.exists():
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("Agent configuration must contain a JSON object.")
        config.update(loaded)

    return config


CONFIG = load_config()


def log(message):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{utc_now()} {message}\n")
    except OSError:
        pass


class AgentState:
    def __init__(self):
        self.started_at = time.time()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.iperf_process = None

    def iperf_running(self):
        with self.lock:
            return self.iperf_process is not None and self.iperf_process.poll() is None


STATE = AgentState()


def start_iperf():
    if not CONFIG.get("iperf_enabled", True):
        return False

    path = Path(str(CONFIG.get("iperf_path", "/usr/bin/iperf3")))
    if not path.exists():
        log(f"iperf3 not found: {path}")
        return False

    with STATE.lock:
        if STATE.iperf_process and STATE.iperf_process.poll() is None:
            return True

        try:
            STATE.iperf_process = subprocess.Popen(
                [str(path), "-s", "-p", str(int(CONFIG.get("iperf_port", 5201)))],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log(f"Started iperf3 on port {CONFIG.get('iperf_port', 5201)}")
            return True
        except OSError as exc:
            STATE.iperf_process = None
            log(f"Unable to start iperf3: {exc}")
            return False


def stop_iperf():
    with STATE.lock:
        process = STATE.iperf_process
        STATE.iperf_process = None

    if process is None:
        return

    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def supervise_iperf():
    while not STATE.stop.is_set():
        if CONFIG.get("iperf_enabled", True) and not STATE.iperf_running():
            start_iperf()
        STATE.stop.wait(max(1, int(CONFIG.get("restart_delay_seconds", 3))))


def read_os_release():
    values = {}
    path = Path("/etc/os-release")

    if path.exists():
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            values[key] = value.strip().strip('"')

    return values


def operating_system_information():
    release = read_os_release()
    return {
        "product_name": release.get("PRETTY_NAME") or platform.system(),
        "edition": release.get("VARIANT") or release.get("VARIANT_ID") or "",
        "display_version": release.get("VERSION_ID") or platform.release(),
        "build": platform.release(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "kernel": platform.version(),
    }


def cpu_model():
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            lowered = line.lower()
            if lowered.startswith("model name") or lowered.startswith("hardware"):
                return line.split(":", 1)[-1].strip()
    return platform.processor() or platform.machine()


def processor_information():
    frequency = psutil.cpu_freq()
    return {
        "model": cpu_model(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "frequency_current_mhz": frequency.current if frequency else None,
        "frequency_max_mhz": frequency.max if frequency else None,
    }


def disk_information():
    disks = []
    seen = set()

    for partition in psutil.disk_partitions(all=False):
        identity = (partition.device, partition.mountpoint)
        if identity in seen:
            continue
        seen.add(identity)

        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError):
            continue

        disks.append({
            "device": partition.device,
            "mountpoint": partition.mountpoint,
            "filesystem": partition.fstype,
            "options": partition.opts,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent": usage.percent,
        })

    return disks


def network_information():
    addresses_by_name = psutil.net_if_addrs()
    stats_by_name = psutil.net_if_stats()
    adapters = []

    for name, addresses in addresses_by_name.items():
        stats = stats_by_name.get(name)
        ip_addresses = []
        mac_address = ""

        for address in addresses:
            family_name = str(address.family)
            if address.family == socket.AF_INET:
                ip_addresses.append({
                    "family": "IPv4",
                    "address": address.address,
                    "netmask": address.netmask,
                    "broadcast": address.broadcast,
                })
            elif address.family == socket.AF_INET6:
                ip_addresses.append({
                    "family": "IPv6",
                    "address": address.address,
                    "netmask": address.netmask,
                    "broadcast": address.broadcast,
                })
            elif "AF_LINK" in family_name or "AF_PACKET" in family_name:
                mac_address = address.address

        adapters.append({
            "name": name,
            "is_up": bool(stats.isup) if stats else False,
            "speed_mbps": stats.speed if stats else None,
            "mtu": stats.mtu if stats else None,
            "mac_address": mac_address,
            "addresses": ip_addresses,
        })

    return adapters


def hardware_information():
    temperatures = {}
    fans = {}

    try:
        temperatures = psutil.sensors_temperatures(fahrenheit=False) or {}
    except (AttributeError, OSError):
        pass

    try:
        fans = psutil.sensors_fans() or {}
    except (AttributeError, OSError):
        pass

    hardware = []
    temperature_values = []

    for group, entries in temperatures.items():
        sensors = []
        for index, entry in enumerate(entries):
            value = entry.current
            if value is None:
                continue
            temperature_values.append(float(value))
            sensors.append({
                "name": entry.label or f"Temperature {index + 1}",
                "type": "Temperature",
                "value": float(value),
                "min": None,
                "max": entry.high,
            })

        if sensors:
            hardware.append({
                "name": group,
                "hardwareType": "Temperature",
                "sensors": sensors,
                "subHardware": [],
            })

    for group, entries in fans.items():
        sensors = []
        for index, entry in enumerate(entries):
            if entry.current is None:
                continue
            sensors.append({
                "name": entry.label or f"Fan {index + 1}",
                "type": "Fan",
                "value": float(entry.current),
                "min": None,
                "max": None,
            })

        if sensors:
            hardware.append({
                "name": group,
                "hardwareType": "FanController",
                "sensors": sensors,
                "subHardware": [],
            })

    summary = {}
    if temperature_values:
        summary["cpu_temperature_c"] = max(temperature_values)

    return {
        "provider": "psutil",
        "available": bool(hardware),
        "error": "" if hardware else "No Linux hardware sensors were exposed by the host.",
        "summary": summary,
        "hardware": hardware,
        "timestamp": utc_now(),
    }


def docker_client():
    if not CONFIG.get("docker_enabled", True):
        raise RuntimeError("Docker monitoring is disabled in the Linux agent.")
    if docker is None:
        raise RuntimeError("The Docker Python SDK is not installed.")
    return docker.from_env(timeout=max(1, int(CONFIG.get("docker_timeout_seconds", 5))))


def docker_cpu_percent(stats):
    cpu_stats = stats.get("cpu_stats") or {}
    previous = stats.get("precpu_stats") or {}
    cpu_total = ((cpu_stats.get("cpu_usage") or {}).get("total_usage") or 0)
    previous_total = ((previous.get("cpu_usage") or {}).get("total_usage") or 0)
    system_total = cpu_stats.get("system_cpu_usage") or 0
    previous_system = previous.get("system_cpu_usage") or 0
    cpu_delta = cpu_total - previous_total
    system_delta = system_total - previous_system

    online_cpus = cpu_stats.get("online_cpus")
    if not online_cpus:
        online_cpus = len((cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or []) or 1

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
    mappings = ((attrs.get("NetworkSettings") or {}).get("Ports") or {})
    output = []

    for container_port, bindings in mappings.items():
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

    if state.get("Running"):
        try:
            stats = container.stats(stream=False)
        except DockerException:
            stats = {}

    memory_used, memory_limit, memory_percent = docker_memory(stats)
    network_rx, network_tx = docker_network_totals(stats)
    image_tags = getattr(container.image, "tags", None) or []
    image_name = image_tags[0] if image_tags else config.get("Image") or container.image.short_id

    return {
        "id": container.short_id,
        "name": container.name,
        "image": image_name,
        "status": container.status,
        "running": bool(state.get("Running")),
        "health": (state.get("Health") or {}).get("Status"),
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
        "source": "agent",
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


def docker_status():
    try:
        return docker_snapshot()
    except (DockerException, RuntimeError, OSError) as exc:
        return {
            "available": False,
            "source": "agent",
            "error": str(exc),
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


def status_payload():
    memory = psutil.virtual_memory()
    boot_time = psutil.boot_time()
    hardware = hardware_information()

    capabilities = [
        "system_information",
        "performance",
        "disk_inventory",
        "network_adapters",
        "iperf3_supervision",
        "hardware_monitoring",
        "docker_monitoring",
    ]

    return {
        "agent": {
            "name": "Network Dashboard Linux Agent",
            "version": VERSION,
            "started_at": datetime.fromtimestamp(
                STATE.started_at, timezone.utc
            ).isoformat(timespec="seconds"),
            "capabilities": capabilities,
        },
        "device": {
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn(),
            "platform": "linux",
            "python_version": platform.python_version(),
            "boot_time": datetime.fromtimestamp(
                boot_time, timezone.utc
            ).isoformat(timespec="seconds"),
            "uptime_seconds": max(0, int(time.time() - boot_time)),
        },
        "operating_system": operating_system_information(),
        "processor": processor_information(),
        "performance": {
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "memory_percent": memory.percent,
            "memory_total_bytes": memory.total,
            "memory_available_bytes": memory.available,
            "memory_used_bytes": memory.used,
        },
        "hardware": hardware,
        "disks": disk_information(),
        "network_adapters": network_information(),
        "services": {
            "iperf3": {
                "running": STATE.iperf_running(),
                "port": int(CONFIG.get("iperf_port", 5201)),
            },
            "docker": {
                "enabled": bool(CONFIG.get("docker_enabled", True)),
                "socket": "/var/run/docker.sock",
            },
            "hardware_helper": {
                "available": bool(hardware.get("available")),
                "path": "psutil.sensors_*",
            },
        },
        "timestamp": utc_now(),
    }


class Handler(BaseHTTPRequestHandler):
    def authorised(self):
        token = str(CONFIG.get("api_token", "")).strip()
        return not token or self.headers.get("Authorization", "") == f"Bearer {token}"

    def send_json(self, code, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if not self.authorised():
            return self.send_json(401, {"error": "Unauthorized"})

        if path in ("/", "/status"):
            return self.send_json(200, status_payload())

        if path == "/hardware":
            return self.send_json(200, hardware_information())

        if path == "/docker":
            return self.send_json(200, docker_status())

        if path == "/health":
            docker_payload = docker_status()
            return self.send_json(200, {
                "ok": True,
                "version": VERSION,
                "iperf3": STATE.iperf_running(),
                "hardware": bool(hardware_information().get("available")),
                "docker": bool(docker_payload.get("available")),
            })

        return self.send_json(404, {"error": "Not found"})

    def log_message(self, format_string, *args):
        log(f"HTTP {self.client_address[0]} {format_string % args}")


def serve():
    server = ThreadingHTTPServer(
        (str(CONFIG.get("bind_address", "0.0.0.0")), int(CONFIG.get("agent_port", 8767))),
        Handler,
    )
    server.timeout = 1
    log(
        f"Linux agent {VERSION} listening on "
        f"{CONFIG.get('bind_address', '0.0.0.0')}:{CONFIG.get('agent_port', 8767)}"
    )

    while not STATE.stop.is_set():
        server.handle_request()

    server.server_close()


def main():
    log(f"Starting Network Dashboard Linux Agent {VERSION}")
    supervisor = threading.Thread(target=supervise_iperf, daemon=True)
    supervisor.start()

    try:
        serve()
    except KeyboardInterrupt:
        pass
    finally:
        STATE.stop.set()
        stop_iperf()
        supervisor.join(timeout=2)
        log("Linux agent stopped")


if __name__ == "__main__":
    main()
