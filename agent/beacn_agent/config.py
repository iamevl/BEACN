import json
import sys
from pathlib import Path


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


BASE_DIR = application_dir()
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "agent.log"


def load_config() -> dict:
    config = {
        "agent_port": 8767,
        "bind_address": "0.0.0.0",
        "iperf_path": str(BASE_DIR / "iperf3.exe"),
        "iperf_port": 5201,
        "api_token": "",
        "restart_delay_seconds": 3,
        "hardware_helper_path": str(BASE_DIR / "hardware-helper.exe"),
        "hardware_helper_timeout_seconds": 8,
        "hardware_cache_seconds": 30,
    }
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")))
    return config
