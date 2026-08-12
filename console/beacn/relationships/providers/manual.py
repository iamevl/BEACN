from ..evidence import Evidence
from ..provider import RelationshipProvider


SUPPORTED_TRANSPORTS = {
    "wired",
    "wireless",
}


class ManualProvider(RelationshipProvider):
    """Produce authoritative evidence from explicit device assignments."""

    name = "manual"

    def __init__(self):
        self.diagnostics = []

    @staticmethod
    def _device_ref(device):
        ip = str(device.get("ip", "")).strip()
        return f"device:{ip}" if ip else ""

    def collect(self, context):
        self.diagnostics = []
        evidence = []

        for device in context.get("devices", []):
            if str(device.get("connection_source", "")).strip().lower() != "manual":
                continue

            subject_ref = self._device_ref(device)
            parent_ref = str(device.get("connection_parent_ref") or "").strip()
            legacy_parent_ip = str(device.get("connection_parent_ip") or "").strip()
            transport = str(device.get("connection_method") or "").strip().lower()

            if not parent_ref and legacy_parent_ip:
                parent_ref = f"device:{legacy_parent_ip}"

            if not subject_ref or not parent_ref or transport not in SUPPORTED_TRANSPORTS:
                self.diagnostics.append({
                    "subject_ref": subject_ref or None,
                    "code": "incomplete_manual",
                    "message": "Manual relationship requires an existing parent and supported transport.",
                    "parent_ref": parent_ref or None,
                    "provider": self.name,
                })
                continue

            evidence.append(Evidence(
                subject_ref=subject_ref,
                parent_ref=parent_ref,
                provider=self.name,
                confidence=100,
                transport=transport,
                reason="manual_override",
            ))

        return evidence
