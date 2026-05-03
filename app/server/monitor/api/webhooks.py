# REQ: SWR-056, SWR-057; RISK: RISK-017, RISK-020, RISK-021; SEC: SC-012, SC-020, SC-021; TEST: TC-023, TC-041, TC-042, TC-048, TC-049
"""Webhook management API."""

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect

webhooks_bp = Blueprint("webhooks", __name__)


@webhooks_bp.route("", methods=["GET"])
@admin_required
def list_webhooks():
    """Return configured webhook destinations."""
    return jsonify(
        {"destinations": current_app.webhook_delivery_service.list_destinations()}
    )


@webhooks_bp.route("", methods=["POST"])
@admin_required
@csrf_protect
def create_webhook():
    """Create a webhook destination."""
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "JSON body required"}), 400
    destination, error, status = (
        current_app.webhook_delivery_service.create_destination(
            payload,
            requesting_user=session.get("username", ""),
            requesting_ip=request.remote_addr or "",
        )
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"destination": destination}), status


@webhooks_bp.route("/<destination_id>", methods=["PUT"])
@admin_required
@csrf_protect
def update_webhook(destination_id):
    """Update a webhook destination."""
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "JSON body required"}), 400
    destination, error, status = (
        current_app.webhook_delivery_service.update_destination(
            destination_id,
            payload,
            requesting_user=session.get("username", ""),
            requesting_ip=request.remote_addr or "",
        )
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"destination": destination}), status


@webhooks_bp.route("/<destination_id>", methods=["DELETE"])
@admin_required
@csrf_protect
def delete_webhook(destination_id):
    """Delete a webhook destination."""
    message, status = current_app.webhook_delivery_service.delete_destination(
        destination_id,
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if status != 200:
        return jsonify({"error": message}), status
    return jsonify({"message": message}), status


@webhooks_bp.route("/<destination_id>/enabled", methods=["PATCH"])
@admin_required
@csrf_protect
def toggle_webhook(destination_id):
    """Enable or disable a webhook destination."""
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "JSON body required"}), 400
    destination, error, status = current_app.webhook_delivery_service.set_enabled(
        destination_id,
        payload.get("enabled"),
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"destination": destination}), status


@webhooks_bp.route("/<destination_id>/test", methods=["POST"])
@admin_required
@csrf_protect
def send_test(destination_id):
    """Fire a synthetic test payload at a destination."""
    result, error, status = current_app.webhook_delivery_service.send_test(
        destination_id,
        requesting_user=session.get("username", ""),
        requesting_ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    body = {"delivery": result}
    if status >= 400:
        body["error"] = result.get("error") or "Test delivery failed"
    return jsonify(body), status


@webhooks_bp.route("/deliveries", methods=["GET"])
@admin_required
def list_deliveries():
    """Return recent webhook delivery attempts."""
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        limit = 20
    deliveries = current_app.webhook_delivery_service.list_recent_deliveries(
        limit=limit
    )
    return jsonify({"deliveries": deliveries, "count": len(deliveries)})
