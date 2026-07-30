"""Communication helpers for BEACN agents."""

import json
import socket
import urllib.error
import urllib.request

from beacn.config import AGENT_PORT, AGENT_TIMEOUT


def tcp_open(ip: str, port: int, timeout: float = 0.5) -> bool:
    """Return True when a TCP connection can be opened."""

    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def fetch_agent_json(ip: str, path: str):
    """Fetch and decode a JSON object from a BEACN agent."""

    clean_path = "/" + str(path).lstrip("/")
    url = f"http://{ip}:{AGENT_PORT}{clean_path}"

    try:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )

        with urllib.request.urlopen(
            request,
            timeout=AGENT_TIMEOUT,
        ) as response:
            if response.status != 200:
                return None

            payload = json.loads(
                response.read().decode("utf-8")
            )

            return payload if isinstance(payload, dict) else None

    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
    ):
        return None


def fetch_agent_status(ip: str):
    """Fetch the standard status payload from a BEACN agent."""

    return fetch_agent_json(ip, "/status")
