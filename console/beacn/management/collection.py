"""Explicit capability-gated management evidence collection."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from beacn.core.observation import normalize_mac
from beacn.management import ManagementSource
from beacn.management.connectivity import HostIdentity

INTERFACE_INVENTORY = "interface_inventory"
_INTERFACE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_LINK_LINE = re.compile(
    r"^(?P<index>\d+):\s+(?P<name>[^:]+):\s+<(?P<flags>[^>]*)>"
    r".*?\bmtu\s+(?P<mtu>\d+).*?\bstate\s+(?P<state>\S+)",
)
_ADDRESS_LINE = re.compile(
    r"^(?P<index>\d+):\s+(?P<name>\S+)\s+"
    r"(?P<family>inet6?)\s+(?P<address>\S+)",
)


class CollectionError(RuntimeError):
    """Stable collection failure without raw output or credential material."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class NormalizedInterface:
    interface_name: str
    interface_index: int | None = None
    mac_address: str | None = None
    admin_state: str | None = None
    operational_state: str | None = None
    mtu: int | None = None
    addresses: tuple[str, ...] = ()
    interface_kind: str | None = None
    provenance: str = "ssh_iproute2"


@dataclass(frozen=True, slots=True)
class CollectionResult:
    category: str
    message: str
    collected_at: str
    interfaces: tuple[object, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "message": self.message,
            "collected_at": self.collected_at,
            "count": len(self.interfaces),
            "interfaces": [
                item.to_dict() if hasattr(item, "to_dict") else asdict(item)
                for item in self.interfaces
            ],
        }


def _clean_name(value: str) -> str:
    name = str(value or "").strip().split("@", 1)[0]
    if not _INTERFACE_NAME.fullmatch(name):
        raise CollectionError("malformed_output", "Interface inventory output is invalid.")
    return name


def parse_ip_interface_inventory(
    link_output: str,
    address_output: str,
) -> tuple[NormalizedInterface, ...]:
    """Normalize bounded `ip -o` output without retaining the raw text."""
    records: dict[str, dict[str, object]] = {}
    for line in str(link_output or "").splitlines():
        if not line.strip():
            continue
        match = _LINK_LINE.match(line.strip())
        if match is None:
            raise CollectionError("malformed_output", "Interface inventory output is invalid.")
        name = _clean_name(match.group("name"))
        if name in records:
            raise CollectionError("malformed_output", "Interface inventory output is invalid.")
        flags = {value.strip().upper() for value in match.group("flags").split(",")}
        remainder = line[match.end():]
        link_match = re.search(r"\blink/(?P<kind>\S+)(?:\s+(?P<address>\S+))?", remainder)
        kind = link_match.group("kind").lower() if link_match else None
        mac = None
        if link_match and kind not in {"loopback", "none"}:
            mac = normalize_mac(link_match.group("address"))
        state = match.group("state").lower()
        records[name] = {
            "interface_name": name,
            "interface_index": int(match.group("index")),
            "mac_address": mac,
            "admin_state": "up" if "UP" in flags else "down",
            "operational_state": (
                "up" if "LOWER_UP" in flags else state if state != "unknown" else "unknown"
            ),
            "mtu": int(match.group("mtu")),
            "addresses": set(),
            "interface_kind": kind,
        }

    for line in str(address_output or "").splitlines():
        if not line.strip():
            continue
        match = _ADDRESS_LINE.match(line.strip())
        if match is None:
            raise CollectionError("malformed_output", "Interface inventory output is invalid.")
        name = _clean_name(match.group("name"))
        try:
            address = str(ipaddress.ip_interface(match.group("address")))
        except ValueError as exc:
            raise CollectionError(
                "malformed_output", "Interface inventory output is invalid."
            ) from exc
        record = records.setdefault(
            name,
            {
                "interface_name": name,
                "interface_index": int(match.group("index")),
                "mac_address": None,
                "admin_state": None,
                "operational_state": None,
                "mtu": None,
                "addresses": set(),
                "interface_kind": None,
            },
        )
        record["addresses"].add(address)

    normalized = []
    if not records:
        raise CollectionError("malformed_output", "Interface inventory output is invalid.")
    for record in records.values():
        record["addresses"] = tuple(
            sorted(
                record["addresses"],
                key=lambda value: (
                    ipaddress.ip_interface(value).version,
                    int(ipaddress.ip_interface(value).ip),
                    ipaddress.ip_interface(value).network.prefixlen,
                ),
            )
        )
        normalized.append(NormalizedInterface(**record))
    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item.interface_index is None,
                item.interface_index or 0,
                item.interface_name,
            ),
        )
    )


class ManagementCollectionService:
    def __init__(self, repository, *, ssh_collector):
        self._repository = repository
        self._ssh = ssh_collector

    def collect_interface_inventory(self, source: ManagementSource) -> CollectionResult:
        capabilities = dict(source.capabilities)
        if source.orphaned or not source.enabled:
            raise CollectionError("source_disabled", "Management source is not eligible.")
        if not capabilities.get(INTERFACE_INVENTORY, False):
            raise CollectionError(
                "capability_disabled", "Interface inventory collection is not enabled."
            )
        if source.adapter_type != "ssh" or not source.credential_id:
            raise CollectionError(
                "configuration_invalid", "Interface inventory source is not configured."
            )

        try:
            presented = self._ssh.identity(source)
        except TimeoutError as exc:
            raise CollectionError(
                "timeout", "Interface inventory collection timed out."
            ) from exc
        except Exception as exc:
            raise CollectionError(
                "collection_failed", "Interface inventory collection failed safely."
            ) from exc
        expected = HostIdentity(
            source.ssh_host_key_algorithm or "",
            source.ssh_host_key_fingerprint or "",
        )
        if not source.ssh_trusted or presented != expected:
            raise CollectionError(
                "host_identity_changed", "SSH host identity verification failed."
            )

        credential = self._repository.get_credential(source.credential_id)
        if credential is None:
            raise CollectionError(
                "configuration_invalid", "Interface inventory source is not configured."
            )
        secrets = self._repository.decrypt_credential(source.credential_id)
        link_output, address_output = self._ssh.collect(
            source,
            secrets,
            credential.credential_type,
        )
        interfaces = parse_ip_interface_inventory(link_output, address_output)
        collected_at = datetime.now(timezone.utc).isoformat()
        persisted = self._repository.replace_interface_inventory(
            source,
            interfaces,
            collected_at=collected_at,
        )
        return CollectionResult(
            "collected",
            "Interface inventory collected.",
            collected_at,
            tuple(persisted),
        )
