import json
from datetime import datetime, timezone

from beacn.runtime import database


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    """Compatibility connection helper for legacy routes during v0.10."""
    return database.connect()


def normalise_windows_name(agent_payload):
    if not agent_payload:
        return agent_payload

    operating_system = agent_payload.get("operating_system", {})
    product_name = str(operating_system.get("product_name", ""))
    build_text = str(operating_system.get("build", ""))

    try:
        build_number = int(build_text.split(".", 1)[0])
    except (TypeError, ValueError):
        build_number = 0

    if build_number >= 22000 and product_name.startswith("Windows 10"):
        operating_system["product_name"] = product_name.replace(
            "Windows 10",
            "Windows 11",
            1,
        )

    return agent_payload
