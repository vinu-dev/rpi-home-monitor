"""
WebRTC WHEP proxy — authenticated gateway to MediaMTX.

Proxies WebRTC WHEP requests to the local MediaMTX instance after
validating the user's session. Without this, the MediaMTX WHEP
endpoint (port 8889) would be accessible without authentication.

Endpoints:
  POST/PATCH/DELETE /webrtc/<path>  - proxy to MediaMTX WHEP
  OPTIONS           /webrtc/<path>  - CORS preflight (no auth)
"""

import urllib.error
import urllib.request

from flask import Blueprint, Response, request

from monitor.auth import login_required

webrtc_bp = Blueprint("webrtc", __name__)

MEDIAMTX_WHEP = "http://127.0.0.1:8889"


@webrtc_bp.route("/<path:path>", methods=["OPTIONS"])
def whep_preflight(path):
    """Handle CORS preflight — no auth needed (OPTIONS carries no cookies)."""
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


@webrtc_bp.route("/<path:path>", methods=["POST", "PATCH", "DELETE"])
@login_required
def whep_proxy(path):
    """Proxy authenticated WHEP requests to MediaMTX.

    Validates the session via @login_required before forwarding
    the request to the local MediaMTX WHEP endpoint.
    """
    target_url = f"{MEDIAMTX_WHEP}/{path}"

    # Forward the request body and content-type
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
            # Forward relevant response headers
            for header in ("Content-Type", "ETag", "Location", "Link"):
                value = upstream.headers.get(header)
                if value:
                    # Rewrite Location header to use our proxy path
                    if header == "Location" and value.startswith(
                        "http://127.0.0.1:8889/"
                    ):
                        value = value.replace("http://127.0.0.1:8889/", "/webrtc/")
                    resp.headers[header] = value
    except urllib.error.HTTPError as e:
        resp_data = e.read() if hasattr(e, "read") else b""
        resp = Response(resp_data, status=e.code)
        content_type = e.headers.get("Content-Type") if hasattr(e, "headers") else None
        if content_type:
            resp.headers["Content-Type"] = content_type
    except (urllib.error.URLError, OSError):
        resp = Response("MediaMTX not available", status=502)

    # Add CORS headers
    origin = request.headers.get("Origin", request.host_url.rstrip("/"))
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Expose-Headers"] = "ETag, Location, Link"
    return resp
