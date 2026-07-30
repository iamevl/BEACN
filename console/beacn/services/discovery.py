"""Network discovery parsing and hostname resolution for BEACN."""

import re
import socket


def reverse_dns(ip: str) -> str:
    """Return the reverse-DNS hostname for an IP address."""

    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return ""


def parse_nmap_discovery(output: str) -> list[dict[str, str]]:
    """Parse devices from an nmap ping-scan result."""

    devices = []
    current = None

    for line in output.splitlines():
        line = line.strip()

        if line.startswith("Nmap scan report for "):
            value = line.replace(
                "Nmap scan report for ",
                "",
                1,
            )

            hostname = ""
            ip = value

            match = re.match(
                r"(.+?) \(([\d.]+)\)$",
                value,
            )

            if match:
                hostname = match.group(1)
                ip = match.group(2)

            current = {
                "ip": ip,
                "hostname": hostname,
                "mac": "",
                "vendor": "",
            }

            devices.append(current)

        elif current and line.startswith("MAC Address:"):
            match = re.match(
                r"MAC Address:\s+([0-9A-F:]+)\s*(?:\((.*?)\))?$",
                line,
                re.I,
            )

            if match:
                current["mac"] = match.group(1).upper()
                current["vendor"] = match.group(2) or ""

    return devices
