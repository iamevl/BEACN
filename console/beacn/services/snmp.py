from __future__ import annotations

import asyncio
import os
from typing import Any

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    USM_AUTH_HMAC96_SHA,
    USM_PRIV_CFB128_AES,
    get_cmd,
)


SNMP_PORT = 161
SNMP_TIMEOUT_SECONDS = 1.5
SNMP_RETRIES = 0

SYSTEM_OIDS = {
    "description": "1.3.6.1.2.1.1.1.0",
    "object_id": "1.3.6.1.2.1.1.2.0",
    "uptime_ticks": "1.3.6.1.2.1.1.3.0",
    "name": "1.3.6.1.2.1.1.5.0",
}


def _snmp_version(
    explicit: str | None = None,
) -> str:
    value = (
        str(explicit or "").strip()
        or os.environ.get(
            "BEACN_SNMP_VERSION",
            "3",
        ).strip()
    ).lower()

    aliases = {
        "3": "3",
        "v3": "3",
        "2": "2c",
        "2c": "2c",
        "v2": "2c",
        "v2c": "2c",
    }

    return aliases.get(value, value)


def _v3_credentials(
    *,
    username: str | None = None,
    auth_password: str | None = None,
    priv_password: str | None = None,
) -> UsmUserData:

    username = (
        str(username or "").strip()
        or os.environ.get(
            "BEACN_SNMP_USERNAME",
            "",
        ).strip()
    )

    auth_password = (
        str(auth_password or "")
        or os.environ.get(
            "BEACN_SNMP_AUTH_PASSWORD",
            "",
        )
    )

    priv_password = (
        str(priv_password or "")
        or os.environ.get(
            "BEACN_SNMP_PRIV_PASSWORD",
            "",
        )
    )

    if not username:
        raise ValueError(
            "SNMPv3 username is not configured."
        )

    if not auth_password:
        raise ValueError(
            "SNMPv3 authentication password "
            "is not configured."
        )

    if not priv_password:
        raise ValueError(
            "SNMPv3 privacy password "
            "is not configured."
        )

    return UsmUserData(
        username,
        authKey=auth_password,
        privKey=priv_password,
        authProtocol=USM_AUTH_HMAC96_SHA,
        privProtocol=USM_PRIV_CFB128_AES,
    )


def _community(
    explicit: str | None = None,
) -> str:
    """
    Community is deliberately not stored in device inventory.

    Later BEACN releases can introduce encrypted SNMP credentials.
    For the foundation release we accept either an explicit value
    or BEACN_SNMP_COMMUNITY from the environment.
    """

    return (
        str(explicit or "").strip()
        or os.environ.get(
            "BEACN_SNMP_COMMUNITY",
            "public",
        ).strip()
        or "public"
    )


def _normalise(value: Any) -> Any:
    if value is None:
        return None

    text = value.prettyPrint()

    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            pass

    return text


async def _probe_async(
    target: str,
    *,
    version: str | None = None,
    community: str | None = None,
    username: str | None = None,
    auth_password: str | None = None,
    priv_password: str | None = None,
    port: int = SNMP_PORT,
    timeout: float = SNMP_TIMEOUT_SECONDS,
    retries: int = SNMP_RETRIES,
) -> dict[str, Any]:

    target = str(target or "").strip()

    if not target:
        return {
            "available": False,
            "target": target,
            "error": "No target supplied.",
        }

    engine = SnmpEngine()

    try:
        transport = await UdpTransportTarget.create(
            (target, int(port)),
            timeout=float(timeout),
            retries=int(retries),
        )

        objects = [
            ObjectType(
                ObjectIdentity(oid)
            )
            for oid in SYSTEM_OIDS.values()
        ]

        selected_version = _snmp_version(
            version
        )

        if selected_version == "3":
            auth_data = _v3_credentials(
                username=username,
                auth_password=auth_password,
                priv_password=priv_password,
            )

        elif selected_version == "2c":
            auth_data = CommunityData(
                _community(community),
                mpModel=1,
            )

        else:
            return {
                "available": False,
                "target": target,
                "port": int(port),
                "version": selected_version,
                "error": (
                    "Unsupported SNMP version. "
                    "Use 3 or 2c."
                ),
            }

        (
            error_indication,
            error_status,
            error_index,
            var_binds,
        ) = await get_cmd(
            engine,
            auth_data,
            transport,
            ContextData(),
            *objects,
        )

        if error_indication:
            return {
                "available": False,
                "target": target,
                "port": int(port),
                "version": selected_version,
                "error": str(error_indication),
            }

        if error_status:
            failed_index = int(error_index or 0)

            failed_oid = None

            if (
                failed_index > 0
                and failed_index <= len(objects)
            ):
                failed_oid = list(
                    SYSTEM_OIDS.values()
                )[failed_index - 1]

            return {
                "available": False,
                "target": target,
                "port": int(port),
                "version": selected_version,
                "error": (
                    error_status.prettyPrint()
                ),
                "failed_oid": failed_oid,
            }

        values: dict[str, Any] = {}

        keys = list(SYSTEM_OIDS)

        for key, var_bind in zip(
            keys,
            var_binds,
        ):
            _, value = var_bind

            values[key] = _normalise(value)

        uptime_ticks = values.get(
            "uptime_ticks"
        )

        uptime_seconds = None

        if isinstance(uptime_ticks, int):
            uptime_seconds = (
                uptime_ticks / 100.0
            )

        return {
            "available": True,
            "target": target,
            "port": int(port),
            "version": selected_version,
            "system": {
                "name": values.get("name"),
                "description":
                    values.get("description"),
                "object_id":
                    values.get("object_id"),
                "uptime_ticks":
                    uptime_ticks,
                "uptime_seconds":
                    uptime_seconds,
            },
        }

    except Exception as exc:
        return {
            "available": False,
            "target": target,
            "port": int(port),
            "version": selected_version,
            "error": (
                f"{type(exc).__name__}: {exc}"
            ),
        }

    finally:
        try:
            engine.close_dispatcher()
        except Exception:
            pass


def probe_snmp(
    target: str,
    *,
    version: str | None = None,
    community: str | None = None,
    username: str | None = None,
    auth_password: str | None = None,
    priv_password: str | None = None,
    port: int = SNMP_PORT,
    timeout: float = SNMP_TIMEOUT_SECONDS,
    retries: int = SNMP_RETRIES,
) -> dict[str, Any]:
    """
    Perform a read-only SNMPv2c system probe.

    This function does not update the BEACN database.
    """

    return asyncio.run(
        _probe_async(
            target,
            version=version,
            community=community,
            username=username,
            auth_password=auth_password,
            priv_password=priv_password,
            port=port,
            timeout=timeout,
            retries=retries,
        )
    )


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description=(
            "Probe an SNMPv2c device "
            "using standard system OIDs."
        )
    )

    parser.add_argument(
        "target",
        help="Device IP address or hostname",
    )

    parser.add_argument(
        "--version",
        default=None,
        choices=("3", "2c"),
        help=(
            "SNMP version. Defaults to "
            "BEACN_SNMP_VERSION or 3."
        ),
    )

    parser.add_argument(
        "--community",
        default=None,
        help=(
            "SNMP community. Defaults to "
            "BEACN_SNMP_COMMUNITY or public."
        ),
    )

    parser.add_argument(
        "--port",
        type=int,
        default=SNMP_PORT,
    )

    args = parser.parse_args()

    print(
        json.dumps(
            probe_snmp(
                args.target,
                version=args.version,
                community=args.community,
                port=args.port,
            ),
            indent=2,
        )
    )
