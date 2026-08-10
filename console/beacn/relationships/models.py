from dataclasses import dataclass, field

from .evidence import Evidence


@dataclass(slots=True)
class Relationship:

    subject_ref: str

    parent_ref: str | None

    provider: str

    confidence: int

    transport: str

    reason: str

    evidence: list[Evidence] = field(default_factory=list)
