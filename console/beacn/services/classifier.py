"""Conservative device classification for BEACN inventory."""

from __future__ import annotations


def _normalise(value: object) -> str:
    """Return a lowercase, trimmed string for matching."""
    return str(value or "").strip().lower()


def classify_device(
    hostname: str = "",
    vendor: str = "",
    identity: dict | None = None,
) -> dict:
    """Classify a device using authoritative identity before heuristics."""
    identity = identity or {}

    managed_type = _normalise(identity.get("device_type"))
    managed_os = str(identity.get("os_name") or "").strip()

    if managed_type:
        return {
            "device_type": managed_type,
            "os_name": managed_os,
            "confidence": 100,
            "reason": "Managed agent identity",
        }

    hostname_value = _normalise(hostname)
    vendor_value = _normalise(vendor)
    evidence = f"{hostname_value} {vendor_value}"

    if (
        "ringdoorbell" in hostname_value
        or "ring doorbell" in hostname_value
    ):
        return {
            "device_type": "doorbell",
            "os_name": "",
            "confidence": 99,
            "reason": "Matched smart doorbell",
        }

    if (
        vendor_value == "ring"
        or hostname_value.startswith("ring-")
    ):
        return {
            "device_type": "camera",
            "os_name": "",
            "confidence": 92,
            "reason": "Matched Ring camera",
        }

    rules = (
        (
            "raspberry_pi",
            98,
            "Matched Raspberry Pi vendor",
            (
                "raspberry pi foundation",
                "raspberry pi (trading)",
                "raspberry pi trading",
                "raspberry pi ltd",
            ),
        ),
        (
            "nas",
            98,
            "Matched NAS vendor",
            (
                "synology",
                "qnap",
                "terramaster",
            ),
        ),
        (
            "media_tuner",
            98,
            "Matched network television tuner",
            (
                "silicondust",
                "hdhr-",
            ),
        ),
        (
            "router",
            98,
            "Matched router hostname",
            (
                "rt-ax",
                "router",
                "gateway",
            ),
        ),
        (
            "access_point",
            96,
            "Matched wireless access point",
            (
                "deco-",
                "unifi ap",
                "access point",
            ),
        ),
        (
            "camera",
            92,
            "Matched camera vendor or hostname",
            (
                "camera",
                "tapo cam",
                "kc105",
            ),
        ),
        (
            "game_console",
            98,
            "Matched console vendor",
            (
                "sony interactive entertainment",
                "playstation",
                "xbox",
                "nintendo",
            ),
        ),
        (
            "phone",
            95,
            "Matched phone hostname",
            (
                "iphone",
                "android",
                "pixel",
            ),
        ),
        (
            "speaker",
            93,
            "Matched smart speaker hostname",
            (
                "homepod",
                "kitchenpod",
                "speakerbox",
            ),
        ),
        (
            "ups",
            98,
            "Matched UPS vendor",
            (
                "apc by schneider electric",
                "american power conversion",
            ),
        ),
        (
            "appliance",
            96,
            "Matched appliance vendor",
            (
                "dyson",
            ),
        ),
        (
            "iot",
            90,
            "Matched IoT platform",
            (
                "espressif",
                "tuya",
                "switchbot",
                "esp_",
                "lwip",
            ),
        ),
    )

    for device_type, confidence, reason, patterns in rules:
        if any(pattern in evidence for pattern in patterns):
            return {
                "device_type": device_type,
                "os_name": "",
                "confidence": confidence,
                "reason": reason,
            }

    return {
        "device_type": "unknown",
        "os_name": "",
        "confidence": 0,
        "reason": "No classification rule matched",
    }
