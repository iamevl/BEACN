import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "console"
sys.path.insert(0, str(CONSOLE))
os.environ.setdefault("NETWORK_SUBNET", "192.0.2.25/24")


def import_config(value):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(CONSOLE)

    if value is None:
        environment.pop("NETWORK_SUBNET", None)
    else:
        environment["NETWORK_SUBNET"] = value

    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from beacn.config import NETWORK_SUBNET; "
                "print(NETWORK_SUBNET)"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("value", (None, "", "   "))
def test_network_subnet_is_required(value):
    result = import_config(value)

    assert result.returncode != 0
    assert "NETWORK_SUBNET is required" in result.stderr


@pytest.mark.parametrize(
    ("value", "message"),
    (
        ("not-a-network", "valid IPv4 CIDR"),
        ("192.0.2.25", "valid IPv4 CIDR"),
        ("2001:db8::/32", "IPv4 CIDR"),
        ("127.0.0.0/8", "loopback"),
        ("169.254.0.0/16", "link-local"),
        ("224.0.0.0/4", "multicast"),
        ("0.0.0.0/8", "unspecified"),
    ),
)
def test_network_subnet_rejects_invalid_scopes(value, message):
    result = import_config(value)

    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("192.0.2.0/24", "192.0.2.0/24"),
        ("192.0.2.25/24", "192.0.2.0/24"),
        ("198.51.100.0/24", "198.51.100.0/24"),
    ),
)
def test_network_subnet_accepts_and_normalizes_ipv4(value, expected):
    result = import_config(value)

    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_target_validation_uses_configured_subnet(monkeypatch):
    from beacn.services import commands

    monkeypatch.setattr(
        commands,
        "NETWORK_SUBNET",
        "192.0.2.0/24",
    )

    assert commands.valid_target("192.0.2.25") is True
    assert commands.valid_target("198.51.100.25") is False


def test_discovery_receives_normalized_configured_subnet(monkeypatch):
    from beacn.services import scanner

    received = []

    assert scanner.NETWORK_SUBNET == "192.0.2.0/24"

    def synthetic_run_command(args, timeout):
        received.append((args, timeout))
        return {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "synthetic scan stop",
        }

    monkeypatch.setattr(
        scanner,
        "run_command",
        synthetic_run_command,
    )

    scanner.scan_network()

    assert received == [
        (["nmap", "-sn", "-n", "192.0.2.0/24"], scanner.SCAN_TIMEOUT)
    ]
