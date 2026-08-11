"""Monitoring API routes."""

from flask import Blueprint, current_app, jsonify

from beacn.common import utc_now
from beacn.services.commands import valid_target

try:
    from docker.errors import DockerException
except ImportError:
    DockerException = Exception


def unavailable_docker_payload(error, source="agent"):
    return {
        "available": False,
        "source": source,
        "error": "Docker telemetry is currently unavailable.",
        "engine": {
            "containers_total": 0,
            "containers_running": 0,
            "containers_stopped": 0,
            "containers_healthy": 0,
            "containers_unhealthy": 0,
        },
        "containers": [],
        "collected_at": utc_now(),
    }


def create_monitoring_blueprint(
    *,
    get_health_summary,
    docker_snapshot,
    fetch_agent_json,
    db,
):
    blueprint = Blueprint("monitoring", __name__)

    @blueprint.get("/api/health")
    def api_health():
        return jsonify(get_health_summary())

    @blueprint.get("/api/docker")
    def docker_overview():
        """Legacy local Docker endpoint retained for compatibility."""
        try:
            payload = docker_snapshot()
            payload["source"] = "dashboard-host"
            return jsonify(payload)
        except (DockerException, RuntimeError, OSError) as exc:
            current_app.logger.exception(
                "Failed to collect local Docker telemetry: %s",
                exc,
            )
            return jsonify(unavailable_docker_payload(
                exc,
                "dashboard-host",
            ))

    @blueprint.get("/api/docker/<target>")
    def docker_for_device(target):
        if not valid_target(target):
            return jsonify(unavailable_docker_payload(
                "Invalid target."
            )), 400

        with db() as conn:
            row = conn.execute(
                """
                SELECT agent_available, agent_hostname
                FROM devices
                WHERE ip = ?
                """,
                (target,),
            ).fetchone()

        if not row:
            return jsonify(unavailable_docker_payload(
                "Device not found."
            )), 404

        if not row["agent_available"]:
            return jsonify(unavailable_docker_payload(
                "The selected device does not have a BEACN Agent."
            ))

        payload = fetch_agent_json(target, "/docker")
        if payload is None:
            return jsonify(unavailable_docker_payload(
                f"The agent on {target} did not return Docker telemetry."
            ))

        payload.setdefault("source", "agent")
        payload["target_ip"] = target
        payload["target_hostname"] = row["agent_hostname"] or target
        return jsonify(payload)

    return blueprint
