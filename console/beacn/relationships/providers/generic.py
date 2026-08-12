from ..evidence import Evidence
from ..provider import RelationshipProvider


STRONG_WIRED_TYPES = {
    "nas",
    "server",
    "media_tuner",
    "ups",
}


class GenericProvider(RelationshipProvider):
    """
    Conservative vendor-neutral relationship inference.

    This provider deliberately avoids guessing when evidence is
    ambiguous.

    Current evidence rules:

      * one ISP gateway -> primary router
      * one distribution switch -> unresolved downstream switch
      * strong wired endpoint -> distribution switch
      * agent-equipped computer -> distribution switch

    Future providers such as SNMP, LLDP and wireless controller
    integrations will provide stronger evidence than these
    generic rules.
    """

    name = "generic"

    @staticmethod
    def _ref(device):
        ip = str(
            device.get("ip", "")
        ).strip()

        return (
            f"device:{ip}"
            if ip
            else ""
        )

    @staticmethod
    def _has_explicit_parent(device):
        return bool(
            str(
                device.get(
                    "connection_source",
                    "",
                )
            ).strip().lower() == "manual"
            or
            device.get("connection_parent_ref")
            or device.get(
                "connection_parent_ip"
            )
        )

    @staticmethod
    def _strongly_wired(device):
        device_type = str(
            device.get("device_type", "")
        ).strip().lower()

        if device_type in STRONG_WIRED_TYPES:
            return True

        hostname = str(
            device.get("hostname", "")
        ).lower()

        display_name = str(
            device.get("display_name", "")
        ).lower()

        if (
            hostname.startswith("hdhr-")
            or "hd homerun" in display_name
            or "hdhomerun" in display_name
        ):
            return True

        if (
            device_type == "computer"
            and bool(
                device.get(
                    "agent_available"
                )
            )
        ):
            return True

        return False

    def collect(self, context):
        evidence = []

        devices = context.get(
            "devices",
            [],
        )

        infrastructure = context.get(
            "infrastructure",
            [],
        )

        # ----------------------------------------------------
        # Find primary router.
        # ----------------------------------------------------

        routers = [
            device
            for device in devices
            if device.get("device_type")
            == "router"
        ]

        primary_router = (
            routers[0]
            if routers
            else None
        )

        if not primary_router:
            return evidence

        router_ref = self._ref(
            primary_router
        )

        # ----------------------------------------------------
        # Single ISP gateway -> primary router.
        # ----------------------------------------------------

        isp_gateways = [
            item
            for item in infrastructure
            if item.get(
                "infrastructure_type"
            ) == "isp_gateway"
        ]

        if (
            len(isp_gateways) == 1
            and not self._has_explicit_parent(
                primary_router
            )
        ):
            gateway_ref = str(
                isp_gateways[0].get(
                    "ref",
                    "",
                )
            ).strip()

            if gateway_ref:
                evidence.append(
                    Evidence(
                        subject_ref=router_ref,
                        parent_ref=gateway_ref,
                        provider=self.name,
                        confidence=85,
                        transport="wired",
                        reason=(
                            "single_known_isp_gateway"
                        ),
                    )
                )

        # ----------------------------------------------------
        # Find manual infrastructure switches attached to the
        # primary router.
        # ----------------------------------------------------

        router_switches = [
            item
            for item in infrastructure
            if (
                item.get(
                    "infrastructure_type"
                ) == "switch"
                and item.get(
                    "parent_ref"
                ) == router_ref
            )
        ]

        distribution_switch = (
            router_switches[0]
            if len(router_switches) == 1
            else None
        )

        if not distribution_switch:
            return evidence

        distribution_ref = str(
            distribution_switch.get(
                "ref",
                "",
            )
        ).strip()

        if not distribution_ref:
            return evidence

        # ----------------------------------------------------
        # Unresolved discovered switches -> the single known
        # distribution switch.
        # ----------------------------------------------------

        for device in devices:
            if (
                device.get(
                    "device_type"
                ) != "switch"
            ):
                continue

            if self._has_explicit_parent(
                device
            ):
                continue

            subject_ref = self._ref(
                device
            )

            if not subject_ref:
                continue

            evidence.append(
                Evidence(
                    subject_ref=subject_ref,
                    parent_ref=(
                        distribution_ref
                    ),
                    provider=self.name,
                    confidence=70,
                    transport="wired",
                    reason=(
                        "single_distribution_switch"
                    ),
                )
            )

        # ----------------------------------------------------
        # Strong wired endpoints -> single distribution
        # switch.
        # ----------------------------------------------------

        for device in devices:
            if self._has_explicit_parent(
                device
            ):
                continue

            if not self._strongly_wired(
                device
            ):
                continue

            subject_ref = self._ref(
                device
            )

            if not subject_ref:
                continue

            evidence.append(
                Evidence(
                    subject_ref=subject_ref,
                    parent_ref=(
                        distribution_ref
                    ),
                    provider=self.name,
                    confidence=65,
                    transport="wired",
                    reason=(
                        "strong_wired_endpoint"
                    ),
                )
            )

        return evidence
