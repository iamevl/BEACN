from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Observation:
    device_id: str
    source: str
    field: str
    value: Any
    observed_at: str = field(default_factory=utc_now)
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
