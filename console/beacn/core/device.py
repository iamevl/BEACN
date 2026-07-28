from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Device:
    """Canonical BEACN device domain object.

    IP addresses and hostnames are observations. ``id`` is the immutable
    BEACN identity used by APIs and future matching logic.
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    hostname: str | None = None
    display_name: str | None = None
    primary_ip: str | None = None
    primary_mac: str | None = None
    vendor: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    device_type: str | None = None
    is_online: bool = False
    agent_installed: bool = False
    agent_version: str | None = None
    first_seen: str = field(default_factory=utc_now)
    last_seen: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.id = str(UUID(str(self.id)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
