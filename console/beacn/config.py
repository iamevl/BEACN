"""Central application configuration for BEACN."""

import ipaddress
import os
from pathlib import Path

try:
    from version import APP_NAME, APP_STAGE, APP_VERSION
except ImportError:
    APP_NAME = "BEACN"
    APP_VERSION = "0.4.0"
    APP_STAGE = "Live Monitoring"


APP_PORT = int(os.getenv("APP_PORT", "8766"))

def _network_subnet():
    value = os.getenv("NETWORK_SUBNET")

    if value is None or not value.strip():
        raise RuntimeError(
            "NETWORK_SUBNET is required. Set it to the IPv4 CIDR "
            "BEACN is authorised to monitor."
        )

    if "/" not in value:
        raise RuntimeError(
            "NETWORK_SUBNET must be a valid IPv4 CIDR."
        )

    try:
        network = ipaddress.ip_network(
            value.strip(),
            strict=False,
        )
    except ValueError as exc:
        raise RuntimeError(
            "NETWORK_SUBNET must be a valid IPv4 CIDR."
        ) from exc

    if network.version != 4:
        raise RuntimeError(
            "NETWORK_SUBNET must be an IPv4 CIDR; IPv6 is not "
            "currently supported."
        )

    prohibited_scopes = (
        ("unspecified", ipaddress.ip_network("0.0.0.0/8")),
        ("loopback", ipaddress.ip_network("127.0.0.0/8")),
        ("link-local", ipaddress.ip_network("169.254.0.0/16")),
        ("multicast", ipaddress.ip_network("224.0.0.0/4")),
    )

    for label, prohibited in prohibited_scopes:
        if network.overlaps(prohibited):
            raise RuntimeError(
                f"NETWORK_SUBNET must not include {label} address space."
            )

    return str(network)


NETWORK_SUBNET = _network_subnet()

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
