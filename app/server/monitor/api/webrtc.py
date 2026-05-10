"""
WebRTC WHEP proxy - authenticated gateway to MediaMTX.

Proxies WebRTC WHEP requests to the local MediaMTX instance after
validating the user's session. Without this, the MediaMTX WHEP
endpoint (port 8889) would be accessible without authentication.

Endpoints:
  POST/PATCH/DELETE /webrtc/<path>  - proxy to MediaMTX WHEP
  OPTIONS           /webrtc/<path>  - CORS preflight (no auth)
"""

import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, Response, request

from monitor.auth import login_required

webrtc_bp = Blueprint("webrtc", __name__)

# REQ: SWR-031; RISK: RISK-017; SEC: SC-016; TEST: TC-028

MEDIAMTX_WHEP = "http://127.0.0.1:8889"


def whep_preflight_response():
    """Build a CORS preflight response for WHEP endpoints."""
    origin = request.headers.get("Origin", request.host_url.rstrip("/"))
    resp = Response("", status=204)
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "POST, PATCH, OPTIONS, DELETE"
    resp.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, If-Match"
    )
    resp.headers["Access-Control-Expose-Headers"] = "ETag, Location, Link"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


def proxy_whep_request(
    path: str,
    *,
    public_base_path: str = "",
    target_base_path: str = "",
) -> Response:
    """Proxy the current WHEP request to MediaMTX and rewrite public headers."""
    target_url = f"{MEDIAMTX_WHEP}/{urllib.parse.quote(path, safe='/')}"

    headers = {}
    for header in ("Content-Type", "If-Match"):
        value = request.headers.get(header)
        if value:
            headers[header] = value

    try:
        req = urllib.request.Request(
            target_url,
            data=request.get_data(),
            headers=headers,
            method=request.method,
        )
        with urllib.request.urlopen(req, timeout=10) as upstream:
            resp_data = upstream.read()
            resp = Response(resp_data, status=upstream.status)
            for header in ("Content-Type", "ETag", "Location", "Link"):
                value = upstream.headers.get(header)
                if value:
                    if header == "Location":
                        value = _rewrite_mediamtx_location(
                            value,
                            public_base_path=public_base_path,
                            target_base_path=target_base_path,
                        )
                    resp.headers[header] = value
    except urllib.error.HTTPError as e:
        resp_data = e.read() if hasattr(e, "read") else b""
        resp = Response(resp_data, status=e.code)
        content_type = e.headers.get("Content-Type") if e.headers is not None else None
        if content_type:
            resp.headers["Content-Type"] = content_type
    except (urllib.error.URLError, OSError):
        resp = Response("MediaMTX not available", status=502)

    origin = request.headers.get("Origin", request.host_url.rstrip("/"))
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Expose-Headers"] = "ETag, Location, Link"
    return resp


def _rewrite_mediamtx_location(
    value: str,
    *,
    public_base_path: str = "",
    target_base_path: str = "",
) -> str:
    mediamtx_prefix = f"{MEDIAMTX_WHEP}/"
    if not value.startswith(mediamtx_prefix):
        return value
    internal_path = value[len(mediamtx_prefix) :]
    if public_base_path and target_base_path:
        target_base = target_base_path.strip("/")
        if internal_path == target_base:
            return public_base_path.rstrip("/")
        if internal_path.startswith(target_base + "/"):
            suffix = internal_path[len(target_base) :].lstrip("/")
            return public_base_path.rstrip("/") + "/" + suffix
    return "/webrtc/" + internal_path


@webrtc_bp.route("/<path:path>", methods=["OPTIONS"])
def whep_preflight(path):
    """Handle CORS preflight; no auth needed because OPTIONS carries no cookies."""
    return whep_preflight_response()


@webrtc_bp.route("/<path:path>", methods=["POST", "PATCH", "DELETE"])
@login_required
def whep_proxy(path):
    """Proxy authenticated WHEP requests to MediaMTX."""
    return proxy_whep_request(path)
