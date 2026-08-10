from dataclasses import dataclass


@dataclass(slots=True)
class Evidence:

    subject_ref: str

    parent_ref: str | None

    provider: str

    confidence: int

    transport: str

    reason: str
