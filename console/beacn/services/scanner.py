import json
import threading
from uuid import uuid4

from beacn.common import (
    db,
    normalise_windows_name,
    utc_now,
)

from beacn.config import (
    IPERF_PORT,
    METRICS_INTERVAL_SECONDS,
    NETWORK_SUBNET,
    SCAN_TIMEOUT,
)

from beacn.runtime import (
    scan_lock,
    scan_state,
    db_write_lock,
)

from beacn.services.agent import (
    fetch_agent_status,
    tcp_open,
)

from beacn.services.commands import (
    run_command,
    valid_target,
)

from beacn.services.discovery import (
    parse_nmap_discovery,
    reverse_dns,
)

from beacn.services.telemetry import (
    prune_telemetry,
    save_telemetry,
    update_device_from_agent,
)

def parse_iperf_json(raw):
    try:
        payload = json.loads(raw)
        end = payload.get("end", {})
        summary = end.get("sum_received") or end.get("sum_sent") or {}

        return (
            float(summary.get("bits_per_second", 0)),
            int(summary.get("retransmits", 0))
            if summary.get("retransmits") is not None
            else None,
        )
    except Exception:
        return (None, None)

def scan_network():
    if not scan_lock.acquire(blocking=False):
        return

    scan_state["running"] = True
    scan_state["last_error"] = None

    try:
        result = run_command(
            ["nmap", "-sn", "-n", NETWORK_SUBNET],
            timeout=SCAN_TIMEOUT,
        )

        if not result["ok"] and not result["stdout"]:
            raise RuntimeError(result["stderr"] or "Network scan failed.")

        found = parse_nmap_discovery(result["stdout"])
        now = utc_now()

        with db_write_lock:
            with db() as conn:
                conn.execute("UPDATE devices SET is_online = 0")

                for device in found:
                    ip = device["ip"]

                    if not valid_target(ip):
                        continue

                    discovered_hostname = device["hostname"] or reverse_dns(ip)
                    agent = normalise_windows_name(fetch_agent_status(ip))

                    agent_available = int(agent is not None)
                    agent_hostname = ""
                    agent_version = ""
                    cpu_percent = None
                    memory_percent = None
                    uptime_seconds = None
                    agent_last_seen = None
                    agent_payload = None

                    if agent:
                        agent_hostname = str(
                            agent.get("device", {}).get("hostname", "")
                        ).strip()
                        agent_version = str(
                            agent.get("agent", {}).get("version", "")
                        ).strip()
                        performance = agent.get("performance", {})
                        cpu_percent = performance.get("cpu_percent")
                        memory_percent = performance.get("memory_percent")
                        uptime_seconds = agent.get("device", {}).get("uptime_seconds")
                        agent_last_seen = now
                        agent_payload = json.dumps(agent, separators=(",", ":"))
                        iperf_available = int(
                            bool(
                                agent.get("services", {})
                                .get("iperf3", {})
                                .get("running", False)
                            )
                        )
                    else:
                        iperf_available = int(tcp_open(ip, IPERF_PORT))

                    hostname = agent_hostname or discovered_hostname or ip
                    existing = conn.execute(
                        "SELECT id, first_seen FROM devices WHERE ip = ?",
                        (ip,),
                    ).fetchone()
                    device_id = existing["id"] if existing and existing["id"] else str(uuid4())
                    first_seen = existing["first_seen"] if existing else now

                    conn.execute("""
                        INSERT INTO devices (
                            id, ip, hostname, mac, vendor, is_online,
                            iperf_available, first_seen, last_seen,
                            agent_available, agent_version, agent_hostname,
                            cpu_percent, memory_percent, uptime_seconds,
                            agent_last_seen, agent_payload
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, 1, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?
                        )
                       ON CONFLICT(ip) DO UPDATE SET
                            hostname = excluded.hostname,
                            mac = CASE
                                WHEN excluded.mac <> '' THEN excluded.mac
                                ELSE devices.mac
                            END,
                            vendor = CASE
                                WHEN excluded.vendor <> '' THEN excluded.vendor
                                ELSE devices.vendor
                            END,
                            is_online = 1,
                            iperf_available = excluded.iperf_available,
                            last_seen = excluded.last_seen,
                            agent_available = excluded.agent_available,
                            agent_version = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.agent_version
                                ELSE devices.agent_version
                            END,
                            agent_hostname = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.agent_hostname
                                ELSE devices.agent_hostname
                            END,
                            cpu_percent = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.cpu_percent
                                ELSE devices.cpu_percent
                            END,
                            memory_percent = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.memory_percent
                                ELSE devices.memory_percent
                            END,
                            uptime_seconds = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.uptime_seconds
                                ELSE devices.uptime_seconds
                            END,
                            agent_last_seen = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.agent_last_seen
                                ELSE devices.agent_last_seen
                            END,
                            agent_payload = CASE
                                WHEN excluded.agent_available = 1
                                THEN excluded.agent_payload
                                ELSE devices.agent_payload
                            END
                    """, (
                        device_id,
                        ip,
                        hostname,
                        device["mac"],
                        device["vendor"],
                        iperf_available,
                        first_seen,
                        now,
                        agent_available,
                        agent_version,
                        agent_hostname,
                        cpu_percent,
                        memory_percent,
                        uptime_seconds,
                        agent_last_seen,
                        agent_payload,
                    ))

                    if agent:
                        save_telemetry(conn, ip, agent, now)

                prune_telemetry(conn)

    except Exception as exc:
        scan_state["last_error"] = str(exc)

    finally:
        scan_state["running"] = False
        scan_lock.release()



def collect_agent_metrics():
    while True:
        try:
            with db() as conn:
                rows = conn.execute(
                    "SELECT ip FROM devices WHERE agent_available = 1 AND is_online = 1"
                ).fetchall()

            for row in rows:
                payload = normalise_windows_name(fetch_agent_status(row["ip"]))

                if payload:
                    with db_write_lock:
                        with db() as conn:
                            update_device_from_agent(
                                conn,
                                row["ip"],
                                payload,
                                utc_now(),
                            )
                            prune_telemetry(conn)

            if str(scan_state.get("last_error") or "").startswith(
                "Metrics collector:"
            ):
                scan_state["last_error"] = None

        except Exception as exc:
            scan_state["last_error"] = f"Metrics collector: {exc}"

        threading.Event().wait(METRICS_INTERVAL_SECONDS)

