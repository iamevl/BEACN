import json
import platform
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import servicemanager
import win32event
import win32service
import win32serviceutil

try:
    import winreg
except ImportError:
    winreg = None


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "agent.log"


def load_config():
    config = {
        "agent_port": 8767,
        "bind_address": "0.0.0.0",
        "iperf_path": str(BASE_DIR / "iperf3.exe"),
        "iperf_port": 5201,
        "api_token": "",
        "restart_delay_seconds": 3,
        "hardware_helper_path": str(BASE_DIR / "hardware-helper.exe"),
        "hardware_helper_timeout_seconds": 8,
        "hardware_cache_seconds": 2,
    }

    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))

    return config


CONFIG = load_config()


def log(message):
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        pass


class State:
    def __init__(self):
        self.started_at = time.time()
        self.iperf = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.version = "0.5.0"
        self.hardware_lock = threading.Lock()
        self.hardware_payload = None
        self.hardware_updated_at = 0.0

    def iperf_running(self):
        with self.lock:
            return self.iperf is not None and self.iperf.poll() is None


STATE = State()


def start_iperf():
    path = Path(CONFIG["iperf_path"])

    if not path.exists():
        log(f"iperf3 executable not found: {path}")
        return False

    with STATE.lock:
        if STATE.iperf and STATE.iperf.poll() is None:
            return True

        try:
            STATE.iperf = subprocess.Popen(
                [str(path), "-s", "-p", str(CONFIG["iperf_port"])],
                cwd=str(path.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log(f"Started iperf3 on port {CONFIG['iperf_port']}")
            return True
        except Exception as exc:
            log(f"Failed to start iperf3: {exc}")
            STATE.iperf = None
            return False


def stop_iperf():
    with STATE.lock:
        process = STATE.iperf
        STATE.iperf = None

    if process:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def supervise():
    while not STATE.stop.is_set():
        if not STATE.iperf_running():
            start_iperf()
        STATE.stop.wait(int(CONFIG["restart_delay_seconds"]))


def read_registry_value(path, name):
    if winreg is None:
        return None

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def windows_information():
    registry_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"

    product_name = read_registry_value(registry_path, "ProductName")
    display_version = read_registry_value(registry_path, "DisplayVersion")
    release_id = read_registry_value(registry_path, "ReleaseId")
    current_build = read_registry_value(registry_path, "CurrentBuildNumber")
    ubr = read_registry_value(registry_path, "UBR")
    edition_id = read_registry_value(registry_path, "EditionID")

    build = str(current_build or "")
    if build and ubr is not None:
        build = f"{build}.{ubr}"

    return {
        "product_name": product_name or platform.system(),
        "edition": edition_id or "",
        "display_version": display_version or release_id or "",
        "build": build,
        "release": platform.release(),
        "architecture": platform.machine(),
    }


def processor_information():
    processor_name = read_registry_value(
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        "ProcessorNameString",
    )
    physical_cores = psutil.cpu_count(logical=False)
    logical_cores = psutil.cpu_count(logical=True)
    frequency = psutil.cpu_freq()

    return {
        "model": str(processor_name).strip() if processor_name else platform.processor(),
        "physical_cores": physical_cores,
        "logical_cores": logical_cores,
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
    adapter_addresses = psutil.net_if_addrs()
    adapter_stats = psutil.net_if_stats()
    adapters = []

    for name, addresses in adapter_addresses.items():
        stats = adapter_stats.get(name)
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


def unavailable_hardware(reason):
    return {
        "provider": "LibreHardwareMonitor",
        "available": False,
        "error": reason,
        "summary": {},
        "hardware": [],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def hardware_information(force=False):
    cache_seconds = max(0.0, float(CONFIG.get("hardware_cache_seconds", 2)))
    now = time.monotonic()

    with STATE.hardware_lock:
        if (
            not force
            and STATE.hardware_payload is not None
            and now - STATE.hardware_updated_at < cache_seconds
        ):
            return STATE.hardware_payload

        helper = Path(CONFIG["hardware_helper_path"])
        if not helper.exists():
            payload = unavailable_hardware(f"Helper not found: {helper}")
        else:
            try:
                result = subprocess.run(
                    [str(helper)],
                    cwd=str(helper.parent),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=float(CONFIG.get("hardware_helper_timeout_seconds", 8)),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )

                if result.returncode != 0:
                    error = result.stderr.strip() or result.stdout.strip()
                    payload = unavailable_hardware(
                        f"Helper exited with code {result.returncode}: {error}"
                    )
                else:
                    payload = json.loads(result.stdout)
                    payload.setdefault("provider", "LibreHardwareMonitor")
                    payload.setdefault("available", True)
                    payload.setdefault("summary", {})
                    payload.setdefault("hardware", [])
            except subprocess.TimeoutExpired:
                payload = unavailable_hardware("Helper timed out")
            except json.JSONDecodeError as exc:
                payload = unavailable_hardware(f"Invalid helper JSON: {exc}")
            except Exception as exc:
                payload = unavailable_hardware(str(exc))

        STATE.hardware_payload = payload
        STATE.hardware_updated_at = now
        return payload


def status_payload():
    memory = psutil.virtual_memory()
    boot_time = psutil.boot_time()

    capabilities = [
        "system_information",
        "performance",
        "disk_inventory",
        "network_adapters",
        "iperf3_supervision",
        "hardware_monitoring",
    ]

    return {
        "agent": {
            "name": "Network Dashboard Agent",
            "version": STATE.version,
            "started_at": datetime.fromtimestamp(
                STATE.started_at, timezone.utc
            ).isoformat(timespec="seconds"),
            "capabilities": capabilities,
        },
        "device": {
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn(),
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "boot_time": datetime.fromtimestamp(
                boot_time, timezone.utc
            ).isoformat(timespec="seconds"),
            "uptime_seconds": max(0, int(time.time() - boot_time)),
        },
        "operating_system": windows_information(),
        "processor": processor_information(),
        "performance": {
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "memory_percent": memory.percent,
            "memory_total_bytes": memory.total,
            "memory_available_bytes": memory.available,
            "memory_used_bytes": memory.used,
        },
        "hardware": hardware_information(),
        "disks": disk_information(),
        "network_adapters": network_information(),
        "services": {
            "iperf3": {
                "running": STATE.iperf_running(),
                "port": int(CONFIG["iperf_port"]),
            },
            "hardware_helper": {
                "available": bool(hardware_information().get("available")),
                "path": str(CONFIG["hardware_helper_path"]),
            },
        },
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authorised():
            return self.send_json(401, {"error": "Unauthorized"})

        if self.path in ("/", "/status"):
            return self.send_json(200, status_payload())

        if self.path == "/hardware":
            return self.send_json(200, hardware_information(force=True))

        if self.path == "/health":
            hardware = hardware_information()
            return self.send_json(
                200,
                {
                    "ok": True,
                    "iperf3": STATE.iperf_running(),
                    "hardware": bool(hardware.get("available")),
                    "version": STATE.version,
                },
            )

        return self.send_json(404, {"error": "Not found"})

    def log_message(self, format_string, *args):
        log(f"HTTP {self.client_address[0]} {format_string % args}")


def serve():
    server = ThreadingHTTPServer(
        (CONFIG["bind_address"], int(CONFIG["agent_port"])),
        Handler,
    )
    server.timeout = 1
    log(f"API listening on {CONFIG['bind_address']}:{CONFIG['agent_port']}")

    while not STATE.stop.is_set():
        server.handle_request()

    server.server_close()


class NetworkDashboardAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "NetworkDashboardAgent"
    _svc_display_name_ = "Network Dashboard Agent"
    _svc_description_ = (
        "Provides system and hardware status and supervises iperf3 "
        "for Network Dashboard."
    )

    def __init__(self, args):
        super().__init__(args)
        self.stop_handle = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        STATE.stop.set()
        stop_iperf()
        win32event.SetEvent(self.stop_handle)

    def SvcDoRun(self):
        log("Service starting")
        threading.Thread(target=supervise, daemon=True).start()
        threading.Thread(target=serve, daemon=True).start()
        win32event.WaitForSingleObject(self.stop_handle, win32event.INFINITE)
        log("Service stopped")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(NetworkDashboardAgentService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(NetworkDashboardAgentService)
