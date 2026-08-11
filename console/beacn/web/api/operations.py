"""Operational API routes."""

import threading

from flask import Blueprint, jsonify, request

from beacn.runtime import scan_state
from beacn.services.commands import (
    normalize_target,
    run_command,
    valid_target,
)
from beacn.services.scanner import scan_network


operations_blueprint = Blueprint("operations", __name__)


@operations_blueprint.post("/api/scan")
def trigger_scan():
    if scan_state["running"]:
        return jsonify({"ok": True, "message": "A scan is already running."})

    threading.Thread(target=scan_network, daemon=True).start()
    return jsonify({"ok": True, "message": "Network scan started."})


@operations_blueprint.post("/api/ping")
def ping():
    target = (request.json or {}).get("target", "")

    if not valid_target(target):
        return jsonify({
            "ok": False,
            "error": "Target is outside the configured subnet.",
        }), 400

    safe_target = normalize_target(target)

    return jsonify(run_command(
        ["ping", "-c", "4", "-W", "2", safe_target],
        timeout=12,
    ))


@operations_blueprint.post("/api/ports")
def ports():
    target = (request.json or {}).get("target", "")

    if not valid_target(target):
        return jsonify({
            "ok": False,
            "error": "Target is outside the configured subnet.",
        }), 400

    safe_target = normalize_target(target)

    return jsonify(run_command(
        ["nmap", "-Pn", "-T4", "--top-ports", "100", safe_target],
        timeout=45,
    ))
