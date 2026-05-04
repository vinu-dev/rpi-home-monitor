# REQ: SWR-058, SWR-059, SWR-060, SWR-061; RISK: RISK-023, RISK-024, RISK-025; SEC: SC-022, SC-023, SC-024; TEST: TC-050, TC-051, TC-052, TC-053
"""Share-link API and public viewer routes."""

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)

from monitor.auth import admin_required, csrf_protect

share_api_bp = Blueprint("share_api", __name__)
share_public_bp = Blueprint("share_public", __name__)


def _svc():
    return current_app.share_link_service


def _remote_ip() -> str:
    return request.remote_addr or ""


def _remote_ua() -> str:
    return request.headers.get("User-Agent", "")


def _public_error(message: str, status_code: int):
    return (
        render_template(
            "shared_link_error.html",
            message=message,
            status_code=status_code,
        ),
        status_code,
    )


def _public_rate_limit_error(status_code: int = 429):
    return _public_error("Too many requests. Try again later.", status_code)


def _enforce_public_rate_limit():
    allowed, _warn = _svc().check_public_rate_limit(_remote_ip())
    if not allowed:
        return _public_rate_limit_error()
    return None


def _record_public_failure(token: str, error: str, reason: str) -> None:
    if error == _svc().public_resource_failure_message():
        return
    _svc().record_failed_public_attempt(_remote_ip(), token, reason)


@share_api_bp.route("/links", methods=["POST"])
@admin_required
@csrf_protect
def create_share_link():
    data = request.get_json(silent=True) or {}
    result, error, status = _svc().create_share_link(
        resource_type=data.get("resource_type", ""),
        resource_id=data.get("resource_id", ""),
        owner_id=session.get("user_id", ""),
        owner_username=session.get("username", ""),
        ttl=data.get("ttl", ""),
        pin_ip=bool(data.get("pin_ip")),
        pin_ua=bool(data.get("pin_ua")),
        note=data.get("note", ""),
        requesting_ip=_remote_ip(),
        base_url=request.url_root.rstrip("/"),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"link": result}), status


@share_api_bp.route("/links", methods=["GET"])
@admin_required
def list_share_links():
    result, error, status = _svc().list_share_links(
        resource_type=request.args.get("resource_type", ""),
        resource_id=request.args.get("resource_id", ""),
        base_url=request.url_root.rstrip("/"),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@share_api_bp.route("/links/<token>", methods=["DELETE"])
@admin_required
@csrf_protect
def revoke_share_link(token: str):
    result, error, status = _svc().revoke_share_link(
        token,
        requesting_user=session.get("username", ""),
        requesting_ip=_remote_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@share_public_bp.route("/share/clip/<token>", methods=["GET"])
def shared_clip_page(token: str):
    rate_limit_response = _enforce_public_rate_limit()
    if rate_limit_response is not None:
        return rate_limit_response

    result, error, status = _svc().get_shared_clip_page(
        token, _remote_ip(), _remote_ua()
    )
    if error:
        _record_public_failure(token, error, "clip-page")
        if error == _svc().public_resource_failure_message():
            return _public_error(error, status)
        return _public_error(error, status)
    return (
        render_template(
            "shared_clip_viewer.html",
            device_name=result["device_name"],
            resource_name=result["resource_name"],
            video_url=result["video_url"],
            share_link=result["share_link"],
            hide_nav=True,
            public_page=True,
        ),
        status,
    )


@share_public_bp.route("/share/clip/<token>/video.mp4", methods=["GET"])
def shared_clip_video(token: str):
    rate_limit_response = _enforce_public_rate_limit()
    if rate_limit_response is not None:
        return jsonify({"error": "Too many requests. Try again later."}), 429

    result, error, status = _svc().get_shared_clip_asset(
        token, _remote_ip(), _remote_ua()
    )
    if error:
        _record_public_failure(token, error, "clip-asset")
        return jsonify({"error": error}), status
    return send_file(result["path"], mimetype="video/mp4")


@share_public_bp.route("/share/camera/<token>", methods=["GET"])
def shared_camera_page(token: str):
    rate_limit_response = _enforce_public_rate_limit()
    if rate_limit_response is not None:
        return rate_limit_response

    result, error, status = _svc().get_shared_camera_page(
        token, _remote_ip(), _remote_ua()
    )
    if error:
        _record_public_failure(token, error, "camera-page")
        return _public_error(error, status)
    return (
        render_template(
            "shared_camera_viewer.html",
            device_name=result["device_name"],
            resource_name=result["resource_name"],
            hls_url=result["hls_url"],
            share_link=result["share_link"],
            hide_nav=True,
            public_page=True,
        ),
        status,
    )


@share_public_bp.route("/share/camera/<token>/<path:filename>", methods=["GET"])
def shared_camera_file(token: str, filename: str):
    rate_limit_response = _enforce_public_rate_limit()
    if rate_limit_response is not None:
        return jsonify({"error": "Too many requests. Try again later."}), 429

    result, error, status = _svc().get_shared_camera_file(
        token,
        _remote_ip(),
        _remote_ua(),
        filename,
    )
    if error:
        _record_public_failure(token, error, "camera-asset")
        return jsonify({"error": error}), status
    return send_file(result["path"], mimetype=result["mimetype"])
