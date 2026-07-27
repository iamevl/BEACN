import subprocess
import threading
from pathlib import Path

from .collectors import CONFIG
from .logging_utils import log
from .state import STATE


def start_iperf() -> bool:
    path = Path(CONFIG["iperf_path"])
    if not path.exists():
        log(f"iperf3 executable not found: {path}")
        return False
    with STATE.lock:
        if STATE.iperf and STATE.iperf.poll() is None:
            return True
        try:
            STATE.iperf = subprocess.Popen(
                [str(path), "-s", "-p", str(CONFIG["iperf_port"])], cwd=str(path.parent),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log(f"Started iperf3 on port {CONFIG['iperf_port']}")
            return True
        except Exception as exc:
            log(f"Failed to start iperf3: {exc}")
            STATE.iperf = None
            return False


def stop_iperf() -> None:
    with STATE.lock:
        process, STATE.iperf = STATE.iperf, None
    if process:
        try:
            process.terminate(); process.wait(timeout=5)
        except Exception:
            try: process.kill()
            except Exception: pass


def supervise() -> None:
    while not STATE.stop.is_set():
        if not STATE.iperf_running():
            start_iperf()
        STATE.stop.wait(int(CONFIG["restart_delay_seconds"]))


def start_supervisor() -> threading.Thread:
    thread = threading.Thread(target=supervise, daemon=True, name="iperf-supervisor")
    thread.start()
    return thread


def shutdown() -> None:
    STATE.stop.set()
    stop_iperf()
