"""Network health assessment for BEACN."""

from datetime import datetime

from beacn.common import db
from beacn.runtime import scan_state
from beacn.services.docker_monitor import docker_snapshot


def _greeting():
    """Return a greeting based on the console host's local time."""
    hour = datetime.now().astimezone().hour

    if hour < 12:
        return "Good morning"

    if hour < 18:
        return "Good afternoon"

    return "Good evening"


def _status_for_score(score):
    """Convert a numerical health score into a human status."""
    if score >= 90:
        return {
            "status": "Healthy",
            "summary": "Your network is healthy.",
        }

    if score >= 75:
        return {
            "status": "Attention",
            "summary": "Your network may need some attention.",
        }

    if score >= 50:
        return {
            "status": "Degraded",
            "summary": "Some parts of your network are degraded.",
        }

    return {
        "status": "Critical",
        "summary": "Your network needs immediate attention.",
    }


def _device_counts():
    """Read device and agent totals from the BEACN database."""
    with db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS devices_total,
                SUM(CASE WHEN is_online = 1 THEN 1 ELSE 0 END)
                    AS devices_online,
                SUM(CASE WHEN is_online = 0 THEN 1 ELSE 0 END)
                    AS devices_offline,

                SUM(
                    CASE
                        WHEN agent_version IS NOT NULL
                         AND TRIM(agent_version) <> ''
                        THEN 1
                        WHEN agent_last_seen IS NOT NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS agents_managed,

                SUM(
                    CASE
                        WHEN agent_available = 1
                         AND is_online = 1
                        THEN 1
                        ELSE 0
                    END
                ) AS agents_online
            FROM devices
            """
        ).fetchone()

    devices_total = int(row["devices_total"] or 0)
    devices_online = int(row["devices_online"] or 0)
    devices_offline = int(row["devices_offline"] or 0)

    agents_managed = int(row["agents_managed"] or 0)
    agents_online = int(row["agents_online"] or 0)
    agents_offline = max(0, agents_managed - agents_online)

    return {
        "devices_total": devices_total,
        "devices_online": devices_online,
        "devices_offline": devices_offline,
        "agents_managed": agents_managed,
        "agents_online": agents_online,
        "agents_offline": agents_offline,
    }


def _check(
    check_id,
    state,
    message,
    deduction=0,
    details=None,
):
    """Create a consistently shaped health-check result."""
    return {
        "id": check_id,
        "state": state,
        "message": message,
        "deduction": max(0, int(deduction)),
        "details": details or {},
    }


def _discovery_check():
    error = str(scan_state.get("last_error") or "").strip()
    running = bool(scan_state.get("running"))

    details = {
        "running": running,
        "started_at": scan_state.get("started_at"),
        "finished_at": scan_state.get("finished_at"),
        "duration_seconds": scan_state.get("duration_seconds"),
        "devices_found": scan_state.get("devices_found"),
    }

    if error:
        return _check(
            "discovery",
            "critical",
            f"Discovery reported an error: {error}",
            deduction=15,
            details=details,
        )

    if running:
        return _check(
            "discovery",
            "info",
            "Network discovery is currently running.",
            details=details,
        )

    duration = scan_state.get("duration_seconds")
    devices_found = scan_state.get("devices_found")

    if duration is not None and devices_found is not None:
        return _check(
            "discovery",
            "ok",
            (
                f"Discovery completed. "
                f"{devices_found} devices found in {duration:.1f} seconds."
            ),
            details=details,
        )

    return _check(
        "discovery",
        "ok",
        "Network discovery is healthy.",
        details=details,
    )

def _device_check(counts):
    offline = counts["devices_offline"]
    total = counts["devices_total"]

    if total == 0:
        return _check(
            "devices",
            "warning",
            "No discovered devices are currently available.",
            deduction=10,
            details=counts,
        )

    if offline:
        return _check(
            "devices",
            "info",
            (
                f"{offline} discovered device"
                f"{' is' if offline == 1 else 's are'} currently offline."
            ),
            details=counts,
        )

    return _check(
        "devices",
        "ok",
        f"All {total} discovered devices are online.",
        details=counts,
    )


def _agent_check(counts):
    managed = counts["agents_managed"]
    online = counts["agents_online"]
    offline = counts["agents_offline"]

    if managed == 0:
        return _check(
            "agents",
            "info",
            "No managed BEACN agents have been detected yet.",
            details=counts,
        )

    if offline:
        return _check(
            "agents",
            "warning",
            (
                f"{offline} managed BEACN agent"
                f"{' is' if offline == 1 else 's are'} offline."
            ),
            deduction=min(20, offline * 5),
            details=counts,
        )

    return _check(
        "agents",
        "ok",
        f"All {online} managed BEACN agents are online.",
        details=counts,
    )


def _docker_check():
    try:
        snapshot = docker_snapshot()
    except Exception as exc:
        return _check(
            "docker",
            "warning",
            f"Docker monitoring is unavailable: {exc}",
            deduction=5,
            details={
                "available": False,
                "containers_total": 0,
                "containers_running": 0,
                "containers_unhealthy": 0,
            },
        )

    engine = snapshot.get("engine") or {}

    total = int(engine.get("containers_total") or 0)
    running = int(engine.get("containers_running") or 0)
    unhealthy = int(engine.get("containers_unhealthy") or 0)

    details = {
        "available": True,
        "containers_total": total,
        "containers_running": running,
        "containers_stopped": int(
            engine.get("containers_stopped") or 0
        ),
        "containers_healthy": int(
            engine.get("containers_healthy") or 0
        ),
        "containers_unhealthy": unhealthy,
    }

    if unhealthy:
        return _check(
            "docker",
            "critical",
            (
                f"{unhealthy} Docker container"
                f"{' is' if unhealthy == 1 else 's are'} unhealthy."
            ),
            deduction=min(20, unhealthy * 5),
            details=details,
        )

    return _check(
        "docker",
        "ok",
        (
            f"Docker is healthy with {running} of "
            f"{total} containers running."
        ),
        details=details,
    )


def get_health_summary():
    """Calculate and return the current BEACN network-health summary."""
    counts = _device_counts()

    checks = [
        _discovery_check(),
        _device_check(counts),
        _agent_check(counts),
        _docker_check(),
    ]

    total_deduction = sum(
        int(check.get("deduction") or 0)
        for check in checks
    )

    score = max(0, min(100, 100 - total_deduction))
    status = _status_for_score(score)

    docker_details = next(
        (
            check["details"]
            for check in checks
            if check["id"] == "docker"
        ),
        {},
    )

    return {
        "greeting": _greeting(),
        "score": score,
        "status": status["status"],
        "summary": status["summary"],
        "checked_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "checks": checks,
        "counts": {
            **counts,
            "containers_total": int(
                docker_details.get("containers_total") or 0
            ),
            "containers_running": int(
                docker_details.get("containers_running") or 0
            ),
            "containers_unhealthy": int(
                docker_details.get("containers_unhealthy") or 0
            ),
        },
    }
