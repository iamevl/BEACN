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
    walk_cmd,
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


# Standard IF-MIB / IF-MIB-compatible numeric OIDs.
#
# We intentionally use numeric OIDs so the probe does not depend
# on external MIB files being installed in the container.
INTERFACE_OIDS = {
    "description": "1.3.6.1.2.1.2.2.1.2",
    "type": "1.3.6.1.2.1.2.2.1.3",
    "mtu": "1.3.6.1.2.1.2.2.1.4",
    "speed_bps": "1.3.6.1.2.1.2.2.1.5",
    "physical_address": "1.3.6.1.2.1.2.2.1.6",
    "admin_status": "1.3.6.1.2.1.2.2.1.7",
    "oper_status": "1.3.6.1.2.1.2.2.1.8",

    # IF-MIB ifXTable
    "name": "1.3.6.1.2.1.31.1.1.1.1",
    "high_speed_mbps": "1.3.6.1.2.1.31.1.1.1.15",
    "alias": "1.3.6.1.2.1.31.1.1.1.18",
}


INTERFACE_STATUS = {
    1: "up",
    2: "down",
    3: "testing",
    4: "unknown",
    5: "dormant",
    6: "notPresent",
    7: "lowerLayerDown",
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


def _auth_data(
    *,
    version: str | None = None,
    community: str | None = None,
    username: str | None = None,
    auth_password: str | None = None,
    priv_password: str | None = None,
):
    selected_version = _snmp_version(version)

    if selected_version == "3":
        return (
            selected_version,
            _v3_credentials(
                username=username,
                auth_password=auth_password,
                priv_password=priv_password,
            ),
        )

    if selected_version == "2c":
        return (
            selected_version,
            CommunityData(
                _community(community),
                mpModel=1,
            ),
        )

    raise ValueError(
        "Unsupported SNMP version. Use 3 or 2c."
    )


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

        try:
            selected_version, auth_data = _auth_data(
                version=version,
                community=community,
                username=username,
                auth_password=auth_password,
                priv_password=priv_password,
            )
        except ValueError as exc:
            return {
                "available": False,
                "target": target,
                "port": int(port),
                "version": _snmp_version(version),
                "error": str(exc),
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


def _interface_index_from_oid(
    oid: Any,
    base_oid: str,
) -> int | None:
    """
    Extract the interface index by comparing numeric OID tuples.

    PySNMP may pretty-print an ObjectName using a resolved MIB
    label rather than the original numeric OID, so string-prefix
    matching is unreliable.
    """

    try:
        base_parts = tuple(
            int(part)
            for part in base_oid.strip(".").split(".")
        )

        if hasattr(oid, "asTuple"):
            oid_parts = tuple(
                int(part)
                for part in oid.asTuple()
            )
        else:
            raw = str(
                oid.prettyPrint()
                if hasattr(oid, "prettyPrint")
                else oid
            ).strip(".")

            oid_parts = tuple(
                int(part)
                for part in raw.split(".")
            )

    except (TypeError, ValueError, AttributeError):
        return None

    if len(oid_parts) <= len(base_parts):
        return None

    if oid_parts[:len(base_parts)] != base_parts:
        return None

    try:
        return int(
            oid_parts[len(base_parts)]
        )
    except (TypeError, ValueError, IndexError):
        return None


def _format_mac(value: Any) -> str | None:
    if value is None:
        return None

    try:
        raw = bytes(value.asOctets())
    except Exception:
        raw = b""

    if raw:
        return ":".join(
            f"{byte:02x}"
            for byte in raw
        )

    text = value.prettyPrint().strip()

    if not text:
        return None

    return text


async def _walk_column(
    engine: SnmpEngine,
    auth_data: Any,
    transport: Any,
    oid: str,
) -> tuple[dict[int, Any], str | None]:

    values: dict[int, Any] = {}

    iterator = walk_cmd(
        engine,
        auth_data,
        transport,
        ContextData(),
        ObjectType(
            ObjectIdentity(oid)
        ),
        lexicographicMode=False,
    )

    async for (
        error_indication,
        error_status,
        error_index,
        var_binds,
    ) in iterator:

        if error_indication:
            return (
                values,
                str(error_indication),
            )

        if error_status:
            return (
                values,
                error_status.prettyPrint(),
            )

        for var_bind in var_binds:
            object_name, value = var_bind

            index = _interface_index_from_oid(
                object_name,
                oid,
            )

            if index is None:
                continue

            values[index] = value

    return values, None


async def _interfaces_async(
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
        selected_version, auth_data = _auth_data(
            version=version,
            community=community,
            username=username,
            auth_password=auth_password,
            priv_password=priv_password,
        )

        transport = await UdpTransportTarget.create(
            (target, int(port)),
            timeout=float(timeout),
            retries=int(retries),
        )

        columns: dict[str, dict[int, Any]] = {}
        errors: dict[str, str] = {}

        for field, oid in INTERFACE_OIDS.items():
            values, error = await _walk_column(
                engine,
                auth_data,
                transport,
                oid,
            )

            columns[field] = values

            if error:
                errors[field] = error

        indexes: set[int] = set()

        for values in columns.values():
            indexes.update(values.keys())

        interfaces: list[dict[str, Any]] = []

        for index in sorted(indexes):
            description_value = (
                columns["description"].get(index)
            )

            name_value = (
                columns["name"].get(index)
            )

            alias_value = (
                columns["alias"].get(index)
            )

            type_value = (
                columns["type"].get(index)
            )

            mtu_value = (
                columns["mtu"].get(index)
            )

            speed_value = (
                columns["speed_bps"].get(index)
            )

            high_speed_value = (
                columns["high_speed_mbps"].get(
                    index
                )
            )

            admin_value = (
                columns["admin_status"].get(index)
            )

            oper_value = (
                columns["oper_status"].get(index)
            )

            physical_value = (
                columns["physical_address"].get(
                    index
                )
            )

            def as_int(value):
                if value is None:
                    return None

                try:
                    return int(value)
                except (TypeError, ValueError):
                    try:
                        return int(
                            value.prettyPrint()
                        )
                    except Exception:
                        return None

            def as_text(value):
                if value is None:
                    return None

                text = (
                    value.prettyPrint().strip()
                )

                return text or None

            admin_number = as_int(admin_value)
            oper_number = as_int(oper_value)

            speed_bps = as_int(speed_value)
            high_speed_mbps = as_int(
                high_speed_value
            )

            # ifSpeed is limited to Gauge32. Prefer ifHighSpeed
            # where the agent exposes it.
            resolved_speed_bps = speed_bps

            if high_speed_mbps:
                resolved_speed_bps = (
                    high_speed_mbps
                    * 1_000_000
                )

            interfaces.append({
                "index": index,

                "name": (
                    as_text(name_value)
                    or as_text(
                        description_value
                    )
                    or f"if{index}"
                ),

                "description":
                    as_text(description_value),

                "alias":
                    as_text(alias_value),

                "type":
                    as_int(type_value),

                "mtu":
                    as_int(mtu_value),

                "speed_bps":
                    resolved_speed_bps,

                "high_speed_mbps":
                    high_speed_mbps,

                "mac":
                    _format_mac(
                        physical_value
                    ),

                "admin_status": {
                    "code": admin_number,
                    "state":
                        INTERFACE_STATUS.get(
                            admin_number,
                            "unknown",
                        ),
                },

                "oper_status": {
                    "code": oper_number,
                    "state":
                        INTERFACE_STATUS.get(
                            oper_number,
                            "unknown",
                        ),
                },
            })

        return {
            "available": True,
            "target": target,
            "port": int(port),
            "version": selected_version,
            "count": len(interfaces),
            "interfaces": interfaces,
            "walk_errors": errors,
        }

    except Exception as exc:
        return {
            "available": False,
            "target": target,
            "port": int(port),
            "version": _snmp_version(version),
            "error": (
                f"{type(exc).__name__}: {exc}"
            ),
        }

    finally:
        try:
            engine.close_dispatcher()
        except Exception:
            pass


def get_snmp_interfaces(
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
    Walk standard interface tables and return a normalized,
    read-only interface inventory.
    """

    return asyncio.run(
        _interfaces_async(
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

    parser.add_argument(
        "--interfaces",
        action="store_true",
        help=(
            "Walk standard interface tables "
            "instead of the system probe."
        ),
    )

    args = parser.parse_args()

    if args.interfaces:
        result = get_snmp_interfaces(
            args.target,
            version=args.version,
            community=args.community,
            port=args.port,
        )
    else:
        result = probe_snmp(
            args.target,
            version=args.version,
            community=args.community,
            port=args.port,
        )

    print(
        json.dumps(
            result,
            indent=2,
        )
    )
