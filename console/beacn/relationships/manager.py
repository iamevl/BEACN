from collections import defaultdict

from .models import Relationship


class RelationshipManager:
    def __init__(self):
        self.providers = []
        self.diagnostics = []

    def register(self, provider):
        self.providers.append(
            provider
        )

    def collect_evidence(
        self,
        context,
    ):
        evidence = []
        self.diagnostics = []

        for provider in self.providers:
            evidence.extend(provider.collect(context))
            self.diagnostics.extend(
                getattr(provider, "diagnostics", [])
            )

        return evidence

    def evaluate(
        self,
        context,
    ):
        """
        Validate candidates and select one deterministic,
        acyclic relationship per subject.

        Higher confidence wins. Manual and configured
        infrastructure evidence has authority over automatic
        evidence at equal confidence. Equal-authority candidates
        that disagree on the parent remain unresolved.
        """

        collected = self.collect_evidence(context)

        object_refs = {
            f"device:{str(device.get('ip', '')).strip()}"
            for device in context.get("devices", [])
            if str(device.get("ip", "")).strip()
        }
        object_refs.update(
            str(item.get("ref", "")).strip()
            for item in context.get("infrastructure", [])
            if str(item.get("ref", "")).strip()
        )
        manual_subjects = {
            f"device:{str(device.get('ip', '')).strip()}"
            for device in context.get("devices", [])
            if (
                str(device.get("ip", "")).strip()
                and str(
                    device.get(
                        "connection_source",
                        "",
                    )
                ).strip().lower() == "manual"
            )
        }

        grouped = defaultdict(list)

        for item in collected:
            if (
                item.subject_ref in manual_subjects
                and item.provider != "manual"
            ):
                self._reject(
                    item,
                    "manual_fallback_blocked",
                )
                continue

            if item.subject_ref not in object_refs:
                self._reject(item, "missing_subject")
                continue

            if not item.parent_ref or item.parent_ref not in object_refs:
                self._reject(item, "invalid_parent")
                continue

            if item.subject_ref == item.parent_ref:
                self._reject(item, "self_parent")
                continue

            grouped[item.subject_ref].append(item)

        relationships = []

        for (
            subject_ref,
            candidates,
        ) in grouped.items():

            winner = self._winner(subject_ref, candidates)

            if winner is None:
                continue

            candidates = sorted(
                candidates,
                key=self._stable_key,
            )

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

        relationships.sort(key=lambda item: item.subject_ref)

        cyclic_subjects = self._cyclic_subjects(relationships)

        if cyclic_subjects:
            retained = []

            for relationship in relationships:
                if relationship.subject_ref in cyclic_subjects:
                    self.diagnostics.append({
                        "subject_ref": relationship.subject_ref,
                        "code": "cycle_rejected",
                        "message": "Relationship was rejected because it participates in a cycle.",
                        "parent_ref": relationship.parent_ref,
                        "provider": relationship.provider,
                    })
                else:
                    retained.append(relationship)

            relationships = retained

        return relationships

    @staticmethod
    def _authority(item):
        if item.provider in {"manual", "infrastructure"}:
            return 1
        return 0

    @classmethod
    def _stable_key(cls, item):
        return (
            -item.confidence,
            -cls._authority(item),
            str(item.parent_ref or ""),
            item.provider,
            item.transport,
            item.reason,
        )

    def _winner(self, subject_ref, candidates):
        ordered = sorted(candidates, key=self._stable_key)
        best = ordered[0]
        top = [
            item for item in ordered
            if (
                item.confidence,
                self._authority(item),
            ) == (
                best.confidence,
                self._authority(best),
            )
        ]

        if len({item.parent_ref for item in top}) > 1:
            self.diagnostics.append({
                "subject_ref": subject_ref,
                "code": "ambiguous_tie",
                "message": "Equal-authority relationship candidates disagree on the parent.",
                "parent_refs": sorted({item.parent_ref for item in top}),
                "providers": sorted({item.provider for item in top}),
            })
            return None

        return best

    def _reject(self, item, code):
        messages = {
            "missing_subject": "Relationship subject is not present in the inventory.",
            "invalid_parent": "Relationship parent is not present in the inventory.",
            "self_parent": "A relationship subject cannot be its own parent.",
            "manual_fallback_blocked": "Automatic evidence cannot replace an explicit manual relationship.",
        }
        self.diagnostics.append({
            "subject_ref": item.subject_ref,
            "code": code,
            "message": messages[code],
            "parent_ref": item.parent_ref,
            "provider": item.provider,
        })

    @staticmethod
    def _cyclic_subjects(relationships):
        parents = {
            item.subject_ref: item.parent_ref
            for item in relationships
        }
        cyclic = set()

        for start in sorted(parents):
            path = []
            positions = {}
            current = start

            while current in parents:
                if current in positions:
                    cyclic.update(path[positions[current]:])
                    break

                if current in cyclic:
                    break

                positions[current] = len(path)
                path.append(current)
                current = parents[current]

        return cyclic
