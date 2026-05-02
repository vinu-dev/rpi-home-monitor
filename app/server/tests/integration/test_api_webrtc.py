# REQ: SWR-031; RISK: RISK-017; SEC: SC-016; TEST: TC-028
"""Integration tests for the WebRTC WHEP proxy (monitor.api.webrtc).

The proxy sits in front of MediaMTX and gates all WebRTC access behind
session authentication.  These tests verify:

  1. Unauthenticated requests are rejected (401).
  2. Authenticated requests are proxied to MediaMTX.
  3. MediaMTX errors are forwarded faithfully (not swallowed as 500).
  4. CORS preflight (OPTIONS) is handled without authentication.
  5. Location header is rewritten from MediaMTX-internal URL to proxy path.
  6. 502 is returned when MediaMTX is unreachable.
"""

import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

# ===========================================================================
# Auth enforcement
# ===========================================================================


class TestAuthEnforcement:
    """WebRTC proxy must gate every method behind login_required."""

    def test_post_requires_auth(self, client):
        resp = client.post("/api/v1/webrtc/stream/whep")
        assert resp.status_code == 401

    def test_patch_requires_auth(self, client):
        resp = client.patch("/api/v1/webrtc/stream/whep")
        assert resp.status_code == 401

    def test_delete_requires_auth(self, client):
        resp = client.delete("/api/v1/webrtc/stream/whep")
        assert resp.status_code == 401

    def test_authenticated_post_reaches_proxy(self, logged_in_client):
        client = logged_in_client()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"type":"answer","sdp":"..."}'
        mock_resp.status = 201
        mock_resp.headers = {
            "Content-Type": "application/sdp",
            "ETag": None,
            "Location": None,
            "Link": None,
        }
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("monitor.api.webrtc.urllib.request.urlopen", return_value=mock_resp):
            resp = client.post(
                "/api/v1/webrtc/cam-001/whep",
                data=b"SDP offer",
                content_type="application/sdp",
            )
        assert resp.status_code == 201


# ===========================================================================
# CORS preflight — OPTIONS requires no auth
# ===========================================================================


class TestCORSPreflight:
    def test_options_does_not_require_auth(self, client):
        resp = client.options("/api/v1/webrtc/stream/whep")
        assert resp.status_code == 204

    def test_options_returns_correct_allow_methods(self, client):
        resp = client.options("/api/v1/webrtc/stream/whep")
        allow = resp.headers.get("Access-Control-Allow-Methods", "")
        for method in ("POST", "PATCH", "DELETE"):
            assert method in allow, f"{method} missing from CORS Allow-Methods"

    def test_options_returns_allow_headers(self, client):
        resp = client.options("/api/v1/webrtc/stream/whep")
        allow_headers = resp.headers.get("Access-Control-Allow-Headers", "")
        assert "Content-Type" in allow_headers

    def test_options_returns_expose_headers(self, client):
        resp = client.options("/api/v1/webrtc/stream/whep")
        expose = resp.headers.get("Access-Control-Expose-Headers", "")
        for h in ("ETag", "Location", "Link"):
            assert h in expose

    def test_options_max_age_set(self, client):
        resp = client.options("/api/v1/webrtc/stream/whep")
        assert resp.headers.get("Access-Control-Max-Age") == "86400"


# ===========================================================================
# Proxy forwarding and header rewriting
# ===========================================================================


def _mock_upstream(status=201, body=b"", headers=None):
    """Build a mock urllib response context manager."""
    h = {
        "Content-Type": "application/sdp",
        "ETag": None,
        "Location": None,
        "Link": None,
    }
    if headers:
        h.update(headers)
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.headers = h
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestProxyForwarding:
    def test_forwards_post_body_to_mediamtx(self, logged_in_client):
        client = logged_in_client()
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            return _mock_upstream()

        with patch(
            "monitor.api.webrtc.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            client.post(
                "/api/v1/webrtc/stream/whep",
                data=b"SDP_OFFER",
                content_type="application/sdp",
            )

        assert len(captured) == 1
        assert captured[0].data == b"SDP_OFFER"

    def test_forwards_correct_target_url(self, logged_in_client):
        client = logged_in_client()
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            return _mock_upstream()

        with patch(
            "monitor.api.webrtc.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            client.post(
                "/api/v1/webrtc/my-camera/whep",
                data=b"",
                content_type="application/sdp",
            )

        assert "my-camera/whep" in captured[0].full_url

    def test_location_header_rewritten(self, logged_in_client):
        client = logged_in_client()
        upstream = _mock_upstream(
            status=201,
            headers={"Location": "http://127.0.0.1:8889/my-camera/whep/session/abc"},
        )
        with patch("monitor.api.webrtc.urllib.request.urlopen", return_value=upstream):
            resp = client.post(
                "/api/v1/webrtc/my-camera/whep",
                data=b"",
                content_type="application/sdp",
            )

        location = resp.headers.get("Location", "")
        assert "127.0.0.1:8889" not in location, (
            "Internal MediaMTX URL leaked in Location header"
        )
        assert location.startswith("/webrtc/"), f"Location not rewritten: {location}"

    def test_etag_forwarded(self, logged_in_client):
        client = logged_in_client()
        upstream = _mock_upstream(headers={"ETag": '"abc123"'})
        with patch("monitor.api.webrtc.urllib.request.urlopen", return_value=upstream):
            resp = client.post(
                "/api/v1/webrtc/stream/whep", data=b"", content_type="application/sdp"
            )
        assert resp.headers.get("ETag") == '"abc123"'

    def test_patch_method_forwarded(self, logged_in_client):
        client = logged_in_client()
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            return _mock_upstream(status=200)

        with patch(
            "monitor.api.webrtc.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            client.patch(
                "/api/v1/webrtc/stream/whep/session/1",
                data=b"ice",
                content_type="application/trickle-ice-sdpfrag",
            )

        assert captured[0].method == "PATCH"

    def test_delete_method_forwarded(self, logged_in_client):
        client = logged_in_client()
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            return _mock_upstream(status=200)

        with patch(
            "monitor.api.webrtc.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            client.delete("/api/v1/webrtc/stream/whep/session/1")

        assert captured[0].method == "DELETE"


# ===========================================================================
# MediaMTX error forwarding
# ===========================================================================


class TestMediaMTXErrorForwarding:
    def test_404_from_mediamtx_forwarded(self, logged_in_client):
        client = logged_in_client()
        err = urllib.error.HTTPError(
            url="http://127.0.0.1:8889/x",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        with patch("monitor.api.webrtc.urllib.request.urlopen", side_effect=err):
            resp = client.post(
                "/api/v1/webrtc/unknown/whep", data=b"", content_type="application/sdp"
            )
        assert resp.status_code == 404

    def test_422_from_mediamtx_forwarded(self, logged_in_client):
        client = logged_in_client()
        err = urllib.error.HTTPError(
            url="http://127.0.0.1:8889/x",
            code=422,
            msg="Unprocessable",
            hdrs=None,
            fp=None,
        )
        with patch("monitor.api.webrtc.urllib.request.urlopen", side_effect=err):
            resp = client.post(
                "/api/v1/webrtc/stream/whep",
                data=b"bad sdp",
                content_type="application/sdp",
            )
        assert resp.status_code == 422

    def test_mediamtx_unreachable_returns_502(self, logged_in_client):
        client = logged_in_client()
        with patch(
            "monitor.api.webrtc.urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            resp = client.post(
                "/api/v1/webrtc/stream/whep", data=b"", content_type="application/sdp"
            )
        assert resp.status_code == 502

    def test_oserror_returns_502(self, logged_in_client):
        client = logged_in_client()
        with patch(
            "monitor.api.webrtc.urllib.request.urlopen",
            side_effect=OSError("connection reset"),
        ):
            resp = client.post(
                "/api/v1/webrtc/stream/whep", data=b"", content_type="application/sdp"
            )
        assert resp.status_code == 502

    def test_http_error_with_content_type_header_forwarded(self, logged_in_client):
        """HTTPError that carries a Content-Type header must forward it (line 82)."""
        client = logged_in_client()
        hdrs = MagicMock()
        hdrs.get = MagicMock(return_value="application/json")
        err = urllib.error.HTTPError(
            url="http://127.0.0.1:8889/x",
            code=400,
            msg="Bad Request",
            hdrs=hdrs,
            fp=None,
        )
        with patch("monitor.api.webrtc.urllib.request.urlopen", side_effect=err):
            resp = client.post(
                "/api/v1/webrtc/stream/whep",
                data=b"bad",
                content_type="application/sdp",
            )
        assert resp.status_code == 400
        assert resp.content_type.startswith("application/json")


# ===========================================================================
# CORS on authenticated proxy responses
# ===========================================================================


class TestCORSOnProxyResponses:
    def test_access_control_allow_origin_present(self, logged_in_client):
        client = logged_in_client()
        with patch(
            "monitor.api.webrtc.urllib.request.urlopen", return_value=_mock_upstream()
        ):
            resp = client.post(
                "/api/v1/webrtc/stream/whep", data=b"", content_type="application/sdp"
            )
        assert "Access-Control-Allow-Origin" in resp.headers

    def test_expose_headers_present_on_proxy_response(self, logged_in_client):
        client = logged_in_client()
        with patch(
            "monitor.api.webrtc.urllib.request.urlopen", return_value=_mock_upstream()
        ):
            resp = client.post(
                "/api/v1/webrtc/stream/whep", data=b"", content_type="application/sdp"
            )
        expose = resp.headers.get("Access-Control-Expose-Headers", "")
        assert "ETag" in expose
        assert "Location" in expose
