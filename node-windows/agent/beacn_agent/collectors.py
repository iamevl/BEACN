import json
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from .config import load_config
from .state import STATE

try:
    import winreg
except ImportError:
    winreg = None

CONFIG = load_config()


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
    path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    current_build = read_registry_value(path, "CurrentBuildNumber")
    ubr = read_registry_value(path, "UBR")
    build = str(current_build or "")
    if build and ubr is not None:
        build = f"{build}.{ubr}"
    return {
        "product_name": read_registry_value(path, "ProductName") or platform.system(),
        "edition": read_registry_value(path, "EditionID") or "",
        "display_version": read_registry_value(path, "DisplayVersion") or read_registry_value(path, "ReleaseId") or "",
        "build": build,
        "release": platform.release(),
        "architecture": platform.machine(),
    }


def processor_information():
    model = read_registry_value(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    frequency = psutil.cpu_freq()
    return {
        "model": str(model).strip() if model else platform.processor(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "frequency_current_mhz": frequency.current if frequency else None,
        "frequency_max_mhz": frequency.max if frequency else None,
    }


def disk_information():
    disks, seen = [], set()
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
            "device": partition.device, "mountpoint": partition.mountpoint,
            "filesystem": partition.fstype, "options": partition.opts,
            "total_bytes": usage.total, "used_bytes": usage.used,
            "free_bytes": usage.free, "percent": usage.percent,
        })
    return disks


def network_information():
    addresses = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    adapters = []
    for name, entries in addresses.items():
        stat = stats.get(name)
        ips, mac = [], ""
        for address in entries:
            family_name = str(address.family)
            if address.family == socket.AF_INET:
                ips.append({"family": "IPv4", "address": address.address, "netmask": address.netmask, "broadcast": address.broadcast})
            elif address.family == socket.AF_INET6:
                ips.append({"family": "IPv6", "address": address.address, "netmask": address.netmask, "broadcast": address.broadcast})
            elif "AF_LINK" in family_name or "AF_PACKET" in family_name:
                mac = address.address
        adapters.append({
            "name": name, "is_up": bool(stat.isup) if stat else False,
            "speed_mbps": stat.speed if stat else None, "mtu": stat.mtu if stat else None,
            "mac_address": mac, "addresses": ips,
        })
    return adapters


def unavailable_hardware(reason):
    return {"provider": "LibreHardwareMonitor", "available": False, "error": reason, "summary": {}, "hardware": [], "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def hardware_information(force=False):
    cache_seconds = max(0.0, float(CONFIG.get("hardware_cache_seconds", 30)))
    now = time.monotonic()
    with STATE.hardware_lock:
        if not force and STATE.hardware_payload is not None and now - STATE.hardware_updated_at < cache_seconds:
            return STATE.hardware_payload
        helper = Path(CONFIG["hardware_helper_path"])
        if not helper.exists():
            payload = unavailable_hardware(f"Helper not found: {helper}")
        else:
            try:
                result = subprocess.run([str(helper)], cwd=str(helper.parent), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=float(CONFIG.get("hardware_helper_timeout_seconds", 8)),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
                if result.returncode != 0:
                    payload = unavailable_hardware(f"Helper exited with code {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
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
    hardware = hardware_information()
    return {
        "agent": {
            "name": "BEACN Agent", "version": STATE.version,
            "started_at": datetime.fromtimestamp(STATE.started_at, timezone.utc).isoformat(timespec="seconds"),
            "capabilities": ["system_information", "performance", "disk_inventory", "network_adapters", "iperf3_supervision", "hardware_monitoring"],
        },
        "device": {
            "hostname": socket.gethostname(), "fqdn": socket.getfqdn(), "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "boot_time": datetime.fromtimestamp(boot_time, timezone.utc).isoformat(timespec="seconds"),
            "uptime_seconds": max(0, int(time.time() - boot_time)),
        },
        "operating_system": windows_information(), "processor": processor_information(),
        "performance": {"cpu_percent": psutil.cpu_percent(interval=0.2), "memory_percent": memory.percent,
            "memory_total_bytes": memory.total, "memory_available_bytes": memory.available, "memory_used_bytes": memory.used},
        "hardware": hardware, "disks": disk_information(), "network_adapters": network_information(),
        "services": {"iperf3": {"running": STATE.iperf_running(), "port": int(CONFIG["iperf_port"])},
            "hardware_helper": {"available": bool(hardware.get("available")), "path": str(CONFIG["hardware_helper_path"])}},
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def info_payload():
    return {
        "name": "BEACN Agent", "version": STATE.version,
        "platform": platform.system(), "hostname": socket.gethostname(),
        "runtime": "standalone" if getattr(sys, "frozen", False) else "python",
        "started_at": datetime.fromtimestamp(STATE.started_at, timezone.utc).isoformat(timespec="seconds"),
        "uptime_seconds": max(0, int(time.time() - STATE.started_at)),
    }
