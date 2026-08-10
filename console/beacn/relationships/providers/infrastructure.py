from ..evidence import Evidence
from ..provider import RelationshipProvider


class InfrastructureProvider(RelationshipProvider):
    """
    Produce evidence from explicitly known infrastructure
    hierarchy.

    Examples:

        Internet
            -> Virgin Hub

        ASUS
            -> Loft Switch

    Infrastructure relationships are authoritative because
    these objects represent known physical/logical structure.
    """

    name = "infrastructure"

    def collect(self, context):
        evidence = []

        infrastructure = context.get(
            "infrastructure",
            [],
        )

        for item in infrastructure:
            subject_ref = str(
                item.get("ref", "")
            ).strip()

            raw_parent_ref = item.get(
                "parent_ref"
            )

            parent_ref = (
                str(raw_parent_ref).strip()
                if raw_parent_ref
                else ""
            )

            if not subject_ref or not parent_ref:
                continue

            evidence.append(
                Evidence(
                    subject_ref=subject_ref,
                    parent_ref=parent_ref,
                    provider=self.name,
                    confidence=100,
                    transport=(
                        item.get(
                            "connection_method"
                        )
                        or "unknown"
                    ),
                    reason=(
                        "configured_infrastructure_parent"
                    ),
                )
            )

        return evidence
