# REQ: SWR-058, SWR-059, SWR-060, SWR-061; RISK: RISK-023, RISK-024, RISK-025; SEC: SC-022, SC-023, SC-024; TEST: TC-050, TC-051, TC-052, TC-053
"""Unit tests for ShareLinkService."""

from pathlib import Path

from monitor.models import Camera
from monitor.services.share_link_service import ShareLinkService


def _seed_clip(
    app, camera_id="cam-001", clip_date="2026-05-04", filename="12-00-00.mp4"
):
    cam = Camera(
        id=camera_id,
        name="Front Door",
        status="online",
        ip="192.168.1.40",
        recording_mode="continuous",
    )
    app.store.save_camera(cam)
    clip_dir = Path(app.config["RECORDINGS_DIR"]) / camera_id / clip_date
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip_path = clip_dir / filename
    clip_path.write_bytes(b"fake mp4 bytes")
    return ShareLinkService.build_clip_resource_id(camera_id, clip_date, filename)


def _seed_live(app, camera_id="cam-001"):
    cam = Camera(
        id=camera_id,
        name="Driveway",
        status="online",
        ip="192.168.1.60",
        recording_mode="continuous",
    )
    app.store.save_camera(cam)
    live_dir = Path(app.config["LIVE_DIR"]) / camera_id
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / "stream.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (live_dir / "seg000.ts").write_bytes(b"segment")


class TestShareLinkService:
    def test_create_clip_share_persists_and_lists(self, app):
        resource_id = _seed_clip(app)

        result, error, status = app.share_link_service.create_share_link(
            resource_type="clip",
            resource_id=resource_id,
            owner_id="user-admin",
            owner_username="admin",
            ttl="24h",
            pin_ip=True,
            pin_ua=True,
            note="insurance",
            base_url="https://device.local",
        )

        assert error is None
        assert status == 201
        assert result["share_url"].startswith(
            "https://device.local/share/clip/sharelink_"
        )
        assert result["resource_type"] == "clip"
        assert result["resource_id"] == resource_id
        assert result["pin_ip"] is True
        assert result["pin_ua"] is True

        listed, error, status = app.share_link_service.list_share_links(
            "clip", resource_id, base_url="https://device.local"
        )
        assert error is None
        assert status == 200
        assert listed["resource_name"] == "Front Door · 2026-05-04 · 12-00-00.mp4"
        assert len(listed["links"]) == 1
        assert listed["links"][0]["note"] == "insurance"

    def test_first_use_pinning_binds_then_rejects_other_ip(self, app):
        resource_id = _seed_clip(app)
        created, error, _status = app.share_link_service.create_share_link(
            resource_type="clip",
            resource_id=resource_id,
            owner_id="user-admin",
            owner_username="admin",
            ttl="24h",
            pin_ip=True,
            pin_ua=True,
        )
        assert error is None
        token = created["token"]

        result, error, status = app.share_link_service.get_shared_clip_page(
            token,
            visitor_ip="192.168.1.45",
            visitor_ua=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0 Safari/537.36"
            ),
        )
        assert error is None
        assert status == 200
        assert result["share_link"].pinned_ip == "192.168.1.45"
        assert result["share_link"].pinned_ua == "windows:chrome"

        _result, error, status = app.share_link_service.get_shared_clip_page(
            token,
            visitor_ip="10.0.0.20",
            visitor_ua=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
            ),
        )
        assert status == 404
        assert error == app.share_link_service.public_link_failure_message()

    def test_expired_link_is_rejected(self, app):
        resource_id = _seed_clip(app)
        created, error, _status = app.share_link_service.create_share_link(
            resource_type="clip",
            resource_id=resource_id,
            owner_id="user-admin",
            owner_username="admin",
            ttl="1h",
        )
        assert error is None
        link = app.store.get_share_link(created["token"])
        link.expires_at = "2026-01-01T00:00:00Z"
        app.store.save_share_link(link)

        _result, error, status = app.share_link_service.get_shared_clip_page(
            link.token,
            visitor_ip="192.168.1.45",
            visitor_ua="Mozilla/5.0 Chrome/136.0",
        )
        assert status == 404
        assert error == app.share_link_service.public_link_failure_message()

    def test_shared_camera_file_resolves_hls_segment(self, app):
        _seed_live(app, "cam-live")
        created, error, _status = app.share_link_service.create_share_link(
            resource_type="camera",
            resource_id="cam-live",
            owner_id="user-admin",
            owner_username="admin",
            ttl="24h",
        )
        assert error is None

        result, error, status = app.share_link_service.get_shared_camera_file(
            created["token"],
            visitor_ip="192.168.1.45",
            visitor_ua="Mozilla/5.0 Chrome/136.0",
            filename="seg000.ts",
        )
        assert error is None
        assert status == 200
        assert result["mimetype"] == "video/mp2t"
        assert result["path"].name == "seg000.ts"
