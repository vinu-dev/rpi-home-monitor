# REQ: SWR-024, SWR-029; RISK: RISK-012, RISK-014; SEC: SC-012, SC-014, SC-020; TEST: TC-023, TC-026
"""HTTP adapter for timestamp backfill control + status."""

from flask import Blueprint, current_app, jsonify

from monitor.auth import admin_required, csrf_protect

timestamp_backfill_bp = Blueprint("timestamp_backfill", __name__)


def _svc():
    return current_app.timestamp_backfill_service


@timestamp_backfill_bp.route("/status", methods=["GET"])
@admin_required
def get_status():
    return jsonify(_svc().get_status()), 200


@timestamp_backfill_bp.route("", methods=["POST"])
@admin_required
@csrf_protect
def start_backfill():
    payload, status = _svc().start()
    return jsonify(payload), status


@timestamp_backfill_bp.route("", methods=["DELETE"])
@admin_required
@csrf_protect
def cancel_backfill():
    payload, status = _svc().cancel()
    return jsonify(payload), status
