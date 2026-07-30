"""Central application configuration for BEACN."""

import os
from pathlib import Path

try:
    from version import APP_NAME, APP_STAGE, APP_VERSION
except ImportError:
    APP_NAME = "BEACN"
    APP_VERSION = "0.4.0"
    APP_STAGE = "Live Monitoring"


APP_PORT = int(os.getenv("APP_PORT", "8766"))

NETWORK_SUBNET = os.getenv(
    "NETWORK_SUBNET",
    "192.168.1.0/24",
)

IPERF_PORT = int(os.getenv("IPERF_PORT", "5201"))
AGENT_PORT = int(os.getenv("AGENT_PORT", "8767"))
AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "1.5"))

SCAN_TIMEOUT = int(os.getenv("SCAN_TIMEOUT", "90"))
COMMAND_TIMEOUT = int(os.getenv("COMMAND_TIMEOUT", "20"))

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "beacn.db"

TELEMETRY_RETENTION_DAYS = int(
    os.getenv("TELEMETRY_RETENTION_DAYS", "30")
)

TELEMETRY_MAX_POINTS = int(
    os.getenv("TELEMETRY_MAX_POINTS", "1000")
)

METRICS_INTERVAL_SECONDS = max(
    5,
    int(os.getenv("METRICS_INTERVAL_SECONDS", "15")),
)

DOCKER_MONITORING_ENABLED = os.getenv(
    "DOCKER_MONITORING_ENABLED",
    "true",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}

DOCKER_TIMEOUT_SECONDS = max(
    1,
    int(os.getenv("DOCKER_TIMEOUT_SECONDS", "5")),
)
