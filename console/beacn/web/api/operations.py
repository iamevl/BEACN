"""Operational API routes."""

import threading

from flask import Blueprint, jsonify, request

from beacn.common import db, utc_now
from beacn.config import IPERF_PORT
from beacn.runtime import db_write_lock, scan_state
from beacn.services.agent import tcp_open
from beacn.services.commands import (
    normalize_target,
    run_command,
    valid_target,
)
from beacn.services.scanner import parse_iperf_json, scan_network


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


@operations_blueprint.post("/api/iperf")
def iperf():
    body = request.json or {}
    target = body.get("target", "")
    reverse = bool(body.get("reverse", False))

    if not valid_target(target):
        return jsonify({
            "ok": False,
            "error": "Target is outside the configured subnet.",
        }), 400

    safe_target = normalize_target(target)

    if not tcp_open(safe_target, IPERF_PORT, timeout=1):
        return jsonify({
            "ok": False,
            "error": f"No iperf3 server detected on {safe_target}:{IPERF_PORT}.",
        }), 400

    args = [
        "iperf3", "-c", safe_target,
        "-p", str(IPERF_PORT),
        "-J", "-t", "10",
    ]
    direction = "reverse" if reverse else "forward"

    if reverse:
        args.append("-R")

    result = run_command(args, timeout=25)
    bits_per_second, retransmits = parse_iperf_json(result["stdout"])
    raw_output = result["stdout"] or result["stderr"]

    with db_write_lock:
        with db() as conn:
            conn.execute("""
                INSERT INTO iperf_results
                    (target_ip, direction, bits_per_second,
                     retransmits, raw_output, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                safe_target,
                direction,
                bits_per_second,
                retransmits,
                raw_output,
                utc_now(),
            ))

    result["bits_per_second"] = bits_per_second
    result["retransmits"] = retransmits
    result["direction"] = direction
    return jsonify(result)


@operations_blueprint.get("/api/results")
def results():
    target = request.args.get("target", "")

    with db() as conn:
        if target and valid_target(target):
            rows = conn.execute("""
                SELECT id, target_ip, direction, bits_per_second,
                       retransmits, created_at
                FROM iperf_results
                WHERE target_ip = ?
                ORDER BY id DESC LIMIT 50
            """, (target,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, target_ip, direction, bits_per_second,
                       retransmits, created_at
                FROM iperf_results
                ORDER BY id DESC LIMIT 50
            """).fetchall()

    return jsonify({"results": [dict(row) for row in rows]})
