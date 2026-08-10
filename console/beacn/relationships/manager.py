from collections import defaultdict

from .models import Relationship


class RelationshipManager:
    def __init__(self):
        self.providers = []

    def register(self, provider):
        self.providers.append(
            provider
        )

    def collect_evidence(
        self,
        context,
    ):
        evidence = []

        for provider in self.providers:
            evidence.extend(
                provider.collect(
                    context
                )
            )

        return evidence

    def evaluate(
        self,
        context,
    ):
        """
        Select the strongest candidate relationship for each
        subject.

        For now the highest confidence wins.

        The evidence list is retained so the future Topology
        Intelligence UI can explain why BEACN chose a
        relationship.
        """

        collected = self.collect_evidence(
            context
        )

        grouped = defaultdict(list)

        for item in collected:
            grouped[
                item.subject_ref
            ].append(item)

        relationships = []

        for (
            subject_ref,
            candidates,
        ) in grouped.items():

            candidates.sort(
                key=lambda item:
                    item.confidence,
                reverse=True,
            )

            winner = candidates[0]

            relationships.append(
                Relationship(
                    subject_ref=(
                        subject_ref
                    ),
                    parent_ref=(
                        winner.parent_ref
                    ),
                    provider=(
                        winner.provider
                    ),
                    confidence=(
                        winner.confidence
                    ),
                    transport=(
                        winner.transport
                    ),
                    reason=(
                        winner.reason
                    ),
                    evidence=candidates,
                )
            )

        relationships.sort(
            key=lambda item:
                item.subject_ref
        )

        return relationships
