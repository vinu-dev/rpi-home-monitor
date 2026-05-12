# REQ: SWR-058, SWR-059, SWR-060, SWR-061; RISK: RISK-023, RISK-024, RISK-025; SEC: SC-022, SC-023, SC-024; TEST: TC-050, TC-051, TC-052, TC-053
"""Integration tests for share-link APIs and public routes."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monitor.auth import _login_attempts
from monitor.models import Camera
from monitor.services.share_link_service import ShareLinkService


def _seed_clip(
    app, camera_id="cam-001", clip_date="2026-05-04", filename="12-00-00.mp4"
):
    app.store.save_camera(
        Camera(
            id=camera_id,
            name="Front Door",
            status="online",
            ip="192.168.1.50",
            recording_mode="continuous",
        )
    )
    clip_dir = Path(app.config["RECORDINGS_DIR"]) / camera_id / clip_date
    clip_dir.mkdir(parents=True, exist_ok=True)
    (clip_dir / filename).write_bytes(b"fake mp4 bytes")
    return ShareLinkService.build_clip_resource_id(camera_id, clip_date, filename)


def _seed_live(app, camera_id="cam-002"):
    app.store.save_camera(
        Camera(
            id=camera_id,
            name="Driveway",
            status="online",
            ip="192.168.1.60",
            recording_mode="continuous",
        )
    )


def _mock_whep_upstream(status=201, body=b"SDP answer", headers=None):
    response_headers = {
        "Content-Type": "application/sdp",
        "ETag": None,
        "Location": None,
        "Link": None,
    }
    if headers:
        response_headers.update(headers)
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.headers = response_headers
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.fixture(autouse=True)
def clear_share_rate_limits():
    _login_attempts.clear()
    yield
    _login_attempts.clear()


class TestShareApi:
    def test_create_list_and_revoke_clip_link(self, app, logged_in_client):
        client = logged_in_client()
        resource_id = _seed_clip(app)

        created = client.post(
            "/api/v1/share/links",
            json={
                "resource_type": "clip",
                "resource_id": resource_id,
                "ttl": "24h",
                "pin_ip": True,
                "pin_ua": True,
                "note": "insurance",
            },
        )
        assert created.status_code == 201
        created_body = created.get_json()["link"]
        assert created_body["resource_type"] == "clip"
        assert created_body["share_url"].endswith(
            "/share/clip/" + created_body["token"]
        )

        listed = client.get(
            "/api/v1/share/links",
            query_string={"resource_type": "clip", "resource_id": resource_id},
        )
        assert listed.status_code == 200
        links = listed.get_json()["links"]
        assert len(links) == 1
        assert links[0]["note"] == "insurance"
        assert links[0]["token"] == created_body["token_id"]
        assert links[0]["share_url"] == ""
        assert links[0]["share_url_available"] is False

        revoked = client.delete("/api/v1/share/links/" + created_body["token"])
        assert revoked.status_code == 200
        assert revoked.get_json()["message"] == "Share link revoked"

        events = app.audit.get_events(limit=10, event_type="SHARE_LINK_REVOKED")
        assert any(created_body["token_id"][-6:] in event["detail"] for event in events)

    def test_share_routes_require_admin(self, app, logged_in_client):
        client = logged_in_client("viewer")
        resource_id = _seed_clip(app)

        response = client.post(
            "/api/v1/share/links",
            json={
                "resource_type": "clip",
                "resource_id": resource_id,
                "ttl": "24h",
            },
        )
        assert response.status_code == 403


class TestPublicShareRoutes:
    def test_public_clip_page_and_asset_render_without_auth(self, app, client):
        resource_id = _seed_clip(app)
        created, error, _status = app.share_link_service.create_share_link(
            resource_type="clip",
            resource_id=resource_id,
            owner_id="user-admin",
            owner_username="admin",
            ttl="24h",
        )
        assert error is None

        page = client.get("/share/clip/" + created["token"])
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "Shared clip" in body
        assert "/share/clip/" + created["token"] + "/video.mp4" in body

        asset = client.get("/share/clip/" + created["token"] + "/video.mp4")
        assert asset.status_code == 200
        assert asset.mimetype == "video/mp4"

    def test_public_camera_page_and_whep_proxy_render_without_auth(self, app, client):
        _seed_live(app)
        created, error, _status = app.share_link_service.create_share_link(
            resource_type="camera",
            resource_id="cam-002",
            owner_id="user-admin",
            owner_username="admin",
            ttl="24h",
        )
        assert error is None

        page = client.get("/share/camera/" + created["token"])
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "Shared live camera" in body
        assert "/share/camera/" + created["token"] + "/whep" in body

        upstream = _mock_whep_upstream(
            headers={"Location": "http://127.0.0.1:8889/cam-002/whep/session/abc"}
        )
        with patch("monitor.api.webrtc.urllib.request.urlopen", return_value=upstream):
            whep = client.post(
                "/share/camera/" + created["token"] + "/whep",
                data=b"SDP offer",
                content_type="application/sdp",
            )
        assert whep.status_code == 201
        assert whep.mimetype == "application/sdp"
        assert whep.headers["Location"] == (
            "/share/camera/" + created["token"] + "/whep/session/abc"
        )

    def test_public_camera_whep_invalid_token_does_not_proxy(self, app, client):
        _seed_live(app)

        with patch("monitor.api.webrtc.urllib.request.urlopen") as urlopen:
            response = client.post(
                "/share/camera/sharelink_missing/whep",
                data=b"SDP offer",
                content_type="application/sdp",
            )

        assert response.status_code == 404
        urlopen.assert_not_called()

    def test_public_camera_whep_rejects_path_escape_before_proxy(self, app, client):
        _seed_live(app)
        created, error, _status = app.share_link_service.create_share_link(
            resource_type="camera",
            resource_id="cam-002",
            owner_id="user-admin",
            owner_username="admin",
            ttl="24h",
        )
        assert error is None

        with patch("monitor.api.webrtc.urllib.request.urlopen") as urlopen:
            response = client.patch(
                "/share/camera/" + created["token"] + "/whep/%2E%2E/other/session",
                data=b"ICE fragment",
                content_type="application/trickle-ice-sdpfrag",
            )

        assert response.status_code == 404
        urlopen.assert_not_called()

    def test_public_camera_hls_file_remains_available_when_present(self, app, client):
        _seed_live(app)
        live_dir = Path(app.config["LIVE_DIR"]) / "cam-002"
        live_dir.mkdir(parents=True, exist_ok=True)
        (live_dir / "stream.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
        created, error, _status = app.share_link_service.create_share_link(
            resource_type="camera",
            resource_id="cam-002",
            owner_id="user-admin",
            owner_username="admin",
            ttl="24h",
        )
        assert error is None

        playlist = client.get("/share/camera/" + created["token"] + "/stream.m3u8")
        assert playlist.status_code == 200
        assert playlist.mimetype == "application/vnd.apple.mpegurl"

    def test_invalid_public_token_uses_generic_404(self, app, client):
        response = client.get("/share/clip/sharelink_missing")
        assert response.status_code == 404
        body = response.get_data(as_text=True)
        assert "Link unavailable" in body
        assert "Contact the person who shared it" in body

    def test_repeated_invalid_token_attempts_hit_shared_rate_limit(self, app, client):
        client.environ_base["REMOTE_ADDR"] = "198.51.100.8"

        for _ in range(10):
            response = client.get("/share/clip/sharelink_missing")
            assert response.status_code == 404

        blocked = client.get("/share/clip/sharelink_missing")
        assert blocked.status_code == 429
