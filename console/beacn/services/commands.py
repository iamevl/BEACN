"""Validated operating-system command execution for BEACN diagnostics."""

import ipaddress
import os
import subprocess
from typing import Any

from beacn.config import (
    COMMAND_TIMEOUT,
    IPERF_PORT,
    NETWORK_SUBNET,
)


CommandResult = dict[str, Any]


def normalize_target(value: str) -> str:
    """Return a canonical IP-address string."""

    return str(ipaddress.ip_address(value))


def valid_target(value: str) -> bool:
    """Confirm that an IP belongs to the configured BEACN subnet."""

    try:
        ip = ipaddress.ip_address(value)
        subnet = ipaddress.ip_network(
            NETWORK_SUBNET,
            strict=False,
        )
        return ip in subnet
    except (TypeError, ValueError):
        return False


def valid_subnet(value: str) -> bool:
    """Only permit scans of the configured BEACN subnet."""

    try:
        requested = ipaddress.ip_network(
            value,
            strict=False,
        )
        configured = ipaddress.ip_network(
            NETWORK_SUBNET,
            strict=False,
        )
        return requested == configured
    except (TypeError, ValueError):
        return False


def _is_valid_ping_args(args: list[str]) -> bool:
    return (
        isinstance(args, list)
        and len(args) == 6
        and args[0] == "ping"
        and args[1] == "-c"
        and args[2] == "4"
        and args[3] == "-W"
        and args[4] == "2"
        and isinstance(args[5], str)
        and valid_target(args[5])
    )


def _is_valid_nmap_args(args: list[str]) -> bool:
    if not isinstance(args, list):
        return False

    discovery_scan = (
        len(args) == 4
        and args[0] == "nmap"
        and args[1] == "-sn"
        and args[2] == "-n"
        and isinstance(args[3], str)
        and valid_subnet(args[3])
    )

    top_ports_scan = (
        len(args) == 6
        and args[0] == "nmap"
        and args[1] == "-Pn"
        and args[2] == "-T4"
        and args[3] == "--top-ports"
        and args[4] == "100"
        and isinstance(args[5], str)
        and valid_target(args[5])
    )

    return discovery_scan or top_ports_scan


def _is_valid_iperf_args(args: list[str]) -> bool:
    if not isinstance(args, list):
        return False

    common_args_are_valid = (
        len(args) in {8, 9}
        and args[0] == "iperf3"
        and args[1] == "-c"
        and isinstance(args[2], str)
        and valid_target(args[2])
        and args[3] == "-p"
        and args[4] == str(IPERF_PORT)
        and args[5] == "-J"
        and args[6] == "-t"
        and args[7] == "10"
    )

    if not common_args_are_valid:
        return False

    return len(args) == 8 or args[8] == "-R"


COMMAND_VALIDATORS = {
    "ping": _is_valid_ping_args,
    "nmap": _is_valid_nmap_args,
    "iperf3": _is_valid_iperf_args,
}


def run_command(
    args: list[str],
    timeout: int = COMMAND_TIMEOUT,
) -> CommandResult:
    """Run a strictly validated diagnostic command."""

    if not isinstance(args, list) or not args:
        return {
            "ok": False,
            "returncode": 2,
            "stdout": "",
            "stderr": "Invalid command arguments.",
        }

    command = args[0]
    validator = COMMAND_VALIDATORS.get(command)

    if validator is None or not validator(args):
        return {
            "ok": False,
            "returncode": 2,
            "stdout": "",
            "stderr": "Command is not allowed.",
        }

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={
                **os.environ,
                "LC_ALL": "C",
            },
        )

        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""

        return {
            "ok": False,
            "returncode": 124,
            "stdout": stdout.strip(),
            "stderr": (
                f"Command timed out after {timeout} seconds."
            ),
        }
