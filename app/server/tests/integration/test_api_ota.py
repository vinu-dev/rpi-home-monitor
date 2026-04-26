"""Tests for the OTA update API."""

import io

from monitor.models import Camera


def _add_camera(app, camera_id="cam-001", status="online"):
    """Helper: add camera."""
    app.store.save_camera(
        Camera(id=camera_id, name="Test", status=status, ip="192.168.1.50")
    )


class TestOTAStatus:
    """Test GET /api/v1/ota/status."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/ota/status").status_code == 401

    def test_returns_status(self, logged_in_client):
        client = logged_in_client()
        response = client.get("/api/v1/ota/status")
        assert response.status_code == 200
        data = response.get_json()
        assert "server" in data
        # Per docs/architecture/versioning.md §C, current_version reads
        # /etc/os-release VERSION_ID via release_version(). On a CI
        # runner with no os-release present (or with the host's own
        # os-release that doesn't carry our VERSION_ID), the helper
        # returns "" — that's the documented fail-safe. The test only
        # cares that the field exists and is a string; the live value
        # comes from the device at runtime.
        assert "current_version" in data["server"]
        assert isinstance(data["server"]["current_version"], str)
        assert "cameras" in data

    def test_includes_camera_status(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        data = client.get("/api/v1/ota/status").get_json()
        assert len(data["cameras"]) == 1
        assert data["cameras"][0]["id"] == "cam-001"

    def test_excludes_pending_cameras(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "pending")
        data = client.get("/api/v1/ota/status").get_json()
        assert len(data["cameras"]) == 0


class TestServerUpload:
    """Test POST /api/v1/ota/server/upload."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.post("/api/v1/ota/server/upload")
        assert response.status_code == 403

    def test_requires_file(self, logged_in_client):
        client = logged_in_client()
        response = client.post("/api/v1/ota/server/upload")
        assert response.status_code == 400

    def test_rejects_non_swu(self, logged_in_client):
        client = logged_in_client()
        data = {"file": (io.BytesIO(b"data"), "update.zip")}
        response = client.post(
            "/api/v1/ota/server/upload", data=data, content_type="multipart/form-data"
        )
        assert response.status_code == 400
        assert "swu" in response.get_json()["error"].lower()

    def test_uploads_swu(self, logged_in_client):
        client = logged_in_client()
        data = {"file": (io.BytesIO(b"fake-swu-content"), "update.swu")}
        response = client.post(
            "/api/v1/ota/server/upload", data=data, content_type="multipart/form-data"
        )
        assert response.status_code == 200
        assert "staged" in response.get_json()["message"].lower()

    def test_upload_sets_status(self, app, logged_in_client):
        client = logged_in_client()
        data = {"file": (io.BytesIO(b"fake-swu-content"), "update.swu")}
        client.post(
            "/api/v1/ota/server/upload", data=data, content_type="multipart/form-data"
        )
        status = app.ota_service.get_status("server")
        assert status["state"] == "staged"


class TestCameraPush:
    """Test POST /api/v1/ota/camera/<id>/push."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.post("/api/v1/ota/camera/cam-001/push").status_code == 403

    def test_camera_not_found(self, logged_in_client):
        client = logged_in_client()
        response = client.post("/api/v1/ota/camera/cam-xxx/push")
        assert response.status_code == 404

    def test_camera_must_be_online(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "offline")
        response = client.post("/api/v1/ota/camera/cam-001/push")
        assert response.status_code == 400

    def test_push_refuses_without_staged_bundle(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.post("/api/v1/ota/camera/cam-001/push")
        assert response.status_code == 409
        assert "upload" in response.get_json()["error"].lower()

    def test_pushes_update(self, monkeypatch, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")

        # Upload a bundle first (creates camera inbox).
        up = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"fake-swu-content"), "cam.swu")},
            content_type="multipart/form-data",
        )
        assert up.status_code == 200

        # Stub the background push so the test doesn't hit network.
        calls = []

        def _fake_push(ip, path, progress_cb=None, status_cb=None):
            calls.append((ip, path))
            if progress_cb:
                progress_cb(1, 1)
            if status_cb:
                status_cb("installed", 100)
            return True, "Installed"

        monkeypatch.setattr(app.camera_ota_client, "push_bundle", _fake_push)

        response = client.post("/api/v1/ota/camera/cam-001/push")
        assert response.status_code == 202

        # The push runs in a background thread â€” wait until it finished.
        import time

        for _ in range(40):
            if app.ota_service.get_status("cam-001")["state"] in {
                "installed",
                "error",
            }:
                break
            time.sleep(0.05)

        assert calls and calls[0][0] == "192.168.1.50"
        assert app.ota_service.get_status("cam-001")["state"] == "installed"

    def test_push_logs_audit(self, monkeypatch, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"x"), "cam.swu")},
            content_type="multipart/form-data",
        )
        monkeypatch.setattr(
            app.camera_ota_client,
            "push_bundle",
            lambda ip, path, progress_cb=None: (True, "ok"),
        )
        client.post("/api/v1/ota/camera/cam-001/push")
        events = app.audit.get_events(event_type="OTA_CAMERA_PUSH")
        assert len(events) >= 1


class TestCameraUpload:
    """Test POST /api/v1/ota/camera/<id>/upload."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        resp = client.post("/api/v1/ota/camera/cam-001/upload")
        assert resp.status_code == 403

    def test_camera_not_found(self, logged_in_client):
        client = logged_in_client()
        resp = client.post(
            "/api/v1/ota/camera/missing/upload",
            data={"file": (io.BytesIO(b"x"), "x.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 404

    def test_rejects_non_swu(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"x"), "x.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_uploads_and_stages(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"payload"), "v2.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["filename"] == "v2.swu"
        assert data["size"] == len(b"payload")

        # Status should now show it as staged + filename surfaces to status.
        status = client.get("/api/v1/ota/status").get_json()
        cam_entries = [c for c in status["cameras"] if c["id"] == "cam-001"]
        assert cam_entries and cam_entries[0]["staged_filename"] == "v2.swu"

    def test_upload_replaces_previous(self, app, logged_in_client):
        """Only one bundle per camera at a time (prevents stale pushes)."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"old"), "old.swu")},
            content_type="multipart/form-data",
        )
        client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"new-body"), "new.swu")},
            content_type="multipart/form-data",
        )
        status = client.get("/api/v1/ota/status").get_json()
        cam = next(c for c in status["cameras"] if c["id"] == "cam-001")
        assert cam["staged_filename"] == "new.swu"


class TestCameraLiveStatus:
    """Test GET /api/v1/ota/camera/<id>/live-status."""

    def test_camera_not_found(self, logged_in_client):
        client = logged_in_client()
        resp = client.get("/api/v1/ota/camera/missing/live-status")
        assert resp.status_code == 404

    def test_returns_unreachable_on_error(self, monkeypatch, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        monkeypatch.setattr(
            app.camera_ota_client,
            "get_status",
            lambda ip: (None, "timeout"),
        )
        resp = client.get("/api/v1/ota/camera/cam-001/live-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reachable"] is False
        assert data["error"] == "timeout"

    def test_proxies_camera_status(self, monkeypatch, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        monkeypatch.setattr(
            app.camera_ota_client,
            "get_status",
            lambda ip: ({"state": "installing", "progress": 70, "error": ""}, ""),
        )
        resp = client.get("/api/v1/ota/camera/cam-001/live-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reachable"] is True
        assert data["state"] == "installing"
        assert data["progress"] == 70


# ---------------------------------------------------------------------------
# New coverage tests — paths not exercised by the tests above
# ---------------------------------------------------------------------------


class TestServerUploadEdgeCases:
    """Cover the remaining branches of POST /api/v1/ota/server/upload."""

    def test_returns_400_when_no_file_key(self, logged_in_client):
        """POST with an empty multipart body (no 'file' part) → 400."""
        client = logged_in_client()
        resp = client.post(
            "/api/v1/ota/server/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "file" in resp.get_json()["error"].lower()

    def test_returns_400_when_filename_empty(self, logged_in_client):
        """POST with a file part whose filename is an empty string → 400."""
        client = logged_in_client()
        # Sending a file stream with an empty filename triggers the
        # `if not file.filename` branch in upload_server_image.
        resp = client.post(
            "/api/v1/ota/server/upload",
            data={"file": (io.BytesIO(b"data"), "")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "filename" in resp.get_json()["error"].lower()

    def test_returns_400_when_stage_bundle_fails(
        self, monkeypatch, app, logged_in_client
    ):
        """If ota.stage_bundle() returns an error string → 400."""
        client = logged_in_client()
        monkeypatch.setattr(
            app.ota_service,
            "stage_bundle",
            lambda *a, **kw: (None, "disk full"),
        )
        resp = client.post(
            "/api/v1/ota/server/upload",
            data={"file": (io.BytesIO(b"content"), "update.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "disk full" in resp.get_json()["error"]

    def test_returns_400_when_verify_bundle_fails(
        self, monkeypatch, app, logged_in_client
    ):
        """If ota.verify_bundle() returns (False, error) → 400."""
        client = logged_in_client()
        # stage_bundle must succeed so verify_bundle is reached.
        import os
        import tempfile

        def _fake_stage(source_path, filename, user="", ip=""):
            fd, p = tempfile.mkstemp(suffix=".swu")
            os.write(fd, b"fake")
            os.close(fd)
            return p, ""

        monkeypatch.setattr(app.ota_service, "stage_bundle", _fake_stage)
        monkeypatch.setattr(
            app.ota_service,
            "verify_bundle",
            lambda path: (False, "bad signature"),
        )
        resp = client.post(
            "/api/v1/ota/server/upload",
            data={"file": (io.BytesIO(b"content"), "update.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "bad signature" in resp.get_json()["error"]

    def test_returns_200_on_success(self, monkeypatch, app, logged_in_client):
        """Happy path: stage succeeds, verify succeeds → 200 with staged message."""
        client = logged_in_client()
        import os
        import tempfile

        def _fake_stage(source_path, filename, user="", ip=""):
            fd, p = tempfile.mkstemp(suffix=".swu")
            os.write(fd, b"fake")
            os.close(fd)
            return p, ""

        monkeypatch.setattr(app.ota_service, "stage_bundle", _fake_stage)
        monkeypatch.setattr(app.ota_service, "verify_bundle", lambda path: (True, ""))
        resp = client.post(
            "/api/v1/ota/server/upload",
            data={"file": (io.BytesIO(b"content"), "update.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "staged" in data["message"].lower()
        assert data["filename"] == "update.swu"


class TestServerInstall:
    """Cover POST /api/v1/ota/server/install."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.post("/api/v1/ota/server/install").status_code == 403

    def test_returns_404_when_no_staging_dir(self, app, logged_in_client):
        """Staging directory does not exist → 404."""
        client = logged_in_client()
        # Ensure the staging directory does not exist.
        import shutil

        staging = app.ota_service.staging_dir
        if __import__("os").path.isdir(staging):
            shutil.rmtree(staging)
        resp = client.post("/api/v1/ota/server/install")
        assert resp.status_code == 404
        assert "staged" in resp.get_json()["error"].lower()

    def test_returns_404_when_staging_dir_has_no_swu(self, app, logged_in_client):
        """Staging directory exists but contains no .swu files → 404."""
        client = logged_in_client()
        import os

        staging = app.ota_service.staging_dir
        os.makedirs(staging, exist_ok=True)
        # Put a non-.swu file in staging so the directory exists but is empty
        # of bundles.
        open(os.path.join(staging, "readme.txt"), "w").close()
        resp = client.post("/api/v1/ota/server/install")
        assert resp.status_code == 404
        assert "staged" in resp.get_json()["error"].lower()

    def test_returns_500_when_install_bundle_fails(
        self, monkeypatch, app, logged_in_client
    ):
        """ota.install_bundle() returns (False, error) → 500."""
        client = logged_in_client()
        import os

        staging = app.ota_service.staging_dir
        os.makedirs(staging, exist_ok=True)
        bundle = os.path.join(staging, "update.swu")
        with open(bundle, "wb") as fh:
            fh.write(b"fake-bundle")
        monkeypatch.setattr(
            app.ota_service,
            "install_bundle",
            lambda path, user="", ip="": (False, "swupdate exploded"),
        )
        resp = client.post("/api/v1/ota/server/install")
        assert resp.status_code == 500
        assert "swupdate exploded" in resp.get_json()["error"]

    def test_returns_200_on_success(self, monkeypatch, app, logged_in_client):
        """install_bundle() succeeds → 200 with reboot message."""
        client = logged_in_client()
        import os

        staging = app.ota_service.staging_dir
        os.makedirs(staging, exist_ok=True)
        bundle = os.path.join(staging, "update.swu")
        with open(bundle, "wb") as fh:
            fh.write(b"fake-bundle")
        monkeypatch.setattr(
            app.ota_service,
            "install_bundle",
            lambda path, user="", ip="": (True, ""),
        )
        resp = client.post("/api/v1/ota/server/install")
        assert resp.status_code == 200
        assert "reboot" in resp.get_json()["message"].lower()


class TestCameraUploadEdgeCases:
    """Cover the remaining branches of POST /api/v1/ota/camera/<id>/upload."""

    def test_returns_400_when_no_file_key(self, app, logged_in_client):
        """Multipart body with no 'file' part → 400."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "file" in resp.get_json()["error"].lower()

    def test_returns_400_when_filename_empty(self, app, logged_in_client):
        """File part with empty filename → 400."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"data"), "")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "filename" in resp.get_json()["error"].lower()

    def test_returns_409_when_update_already_in_progress(self, app, logged_in_client):
        """While state is 'uploading', a new upload attempt → 409."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        # Manually set the in-flight state.
        app.ota_service.set_status("cam-001", "uploading", progress=10, error="")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"payload"), "v2.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 409
        assert "in progress" in resp.get_json()["error"].lower()

    def test_returns_409_when_installing(self, app, logged_in_client):
        """While state is 'installing', a new upload attempt → 409."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        app.ota_service.set_status("cam-001", "installing", progress=50, error="")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"payload"), "v2.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 409

    def test_returns_400_when_uploaded_file_is_empty(self, app, logged_in_client):
        """Uploading a zero-byte .swu file → 400."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b""), "empty.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "empty" in resp.get_json()["error"].lower()

    def test_returns_200_and_sets_staged_status(self, app, logged_in_client):
        """Successful camera upload → 200, ota_service state becomes 'staged'."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"real-content"), "fw-1.2.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["camera_id"] == "cam-001"
        assert data["filename"] == "fw-1.2.swu"
        assert data["size"] == len(b"real-content")
        assert app.ota_service.get_status("cam-001")["state"] == "staged"


class TestCameraPushEdgeCases:
    """Cover the remaining branches of POST /api/v1/ota/camera/<id>/push."""

    def test_returns_400_when_camera_has_no_ip(self, app, logged_in_client):
        """Camera with an empty IP string → 400 (re-pair required)."""
        client = logged_in_client()
        app.store.save_camera(
            Camera(id="cam-noip", name="No IP", status="online", ip="")
        )
        resp = client.post("/api/v1/ota/camera/cam-noip/push")
        assert resp.status_code == 400
        assert "ip" in resp.get_json()["error"].lower()

    def test_returns_409_when_update_already_in_progress(self, app, logged_in_client):
        """State already 'uploading' at push time → 409."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        # Stage a bundle so the "no bundle" 409 is not triggered first.
        client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"payload"), "v1.swu")},
            content_type="multipart/form-data",
        )
        # Now simulate an in-flight transfer.
        app.ota_service.set_status("cam-001", "uploading", progress=20, error="")
        resp = client.post("/api/v1/ota/camera/cam-001/push")
        assert resp.status_code == 409
        assert "in progress" in resp.get_json()["error"].lower()


class TestCameraLiveStatusEdgeCases:
    """Cover the remaining branches of GET /api/v1/ota/camera/<id>/live-status."""

    def test_returns_400_when_camera_has_no_ip(self, app, logged_in_client):
        """Camera exists but has no IP → 400."""
        client = logged_in_client()
        app.store.save_camera(
            Camera(id="cam-noip", name="No IP", status="online", ip="")
        )
        resp = client.get("/api/v1/ota/camera/cam-noip/live-status")
        assert resp.status_code == 400
        assert "ip" in resp.get_json()["error"].lower()

    def test_returns_200_reachable_false_on_client_error(
        self, monkeypatch, app, logged_in_client
    ):
        """camera_ota_client.get_status returns (None, error) → 200, reachable=False."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        monkeypatch.setattr(
            app.camera_ota_client,
            "get_status",
            lambda ip: (None, "connection refused"),
        )
        resp = client.get("/api/v1/ota/camera/cam-001/live-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reachable"] is False
        assert data["error"] == "connection refused"

    def test_returns_200_reachable_true_on_success(
        self, monkeypatch, app, logged_in_client
    ):
        """camera_ota_client.get_status returns valid payload → 200, reachable=True."""
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        monkeypatch.setattr(
            app.camera_ota_client,
            "get_status",
            lambda ip: ({"state": "idle", "progress": 0, "error": ""}, ""),
        )
        resp = client.get("/api/v1/ota/camera/cam-001/live-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reachable"] is True
        assert data["state"] == "idle"


class TestUSBOperations:
    """Cover GET /api/v1/ota/usb/scan and POST /api/v1/ota/usb/import."""

    # --- scan ---

    def test_scan_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.get("/api/v1/ota/usb/scan").status_code == 403

    def test_scan_returns_bundles_list(self, monkeypatch, app, logged_in_client):
        """scan_usb() result is surfaced verbatim under the 'bundles' key."""
        client = logged_in_client()
        fake_bundles = [
            {
                "filename": "v1.2.swu",
                "path": "/mnt/usb/v1.2.swu",
                "size": 1024,
                "size_human": "1.0 KB",
                "device": "/dev/sda1",
            }
        ]
        monkeypatch.setattr(app.ota_service, "scan_usb", lambda: fake_bundles)
        resp = client.get("/api/v1/ota/usb/scan")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "bundles" in data
        assert data["bundles"] == fake_bundles

    def test_scan_returns_empty_list_when_no_usb(
        self, monkeypatch, app, logged_in_client
    ):
        """Empty USB scan → {"bundles": []}."""
        client = logged_in_client()
        monkeypatch.setattr(app.ota_service, "scan_usb", lambda: [])
        resp = client.get("/api/v1/ota/usb/scan")
        assert resp.status_code == 200
        assert resp.get_json() == {"bundles": []}

    # --- import ---

    def test_import_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.post("/api/v1/ota/usb/import", json={}).status_code == 403

    def test_import_returns_400_when_no_path(self, logged_in_client):
        """JSON body with no 'path' key → 400."""
        client = logged_in_client()
        resp = client.post("/api/v1/ota/usb/import", json={})
        assert resp.status_code == 400
        assert "path" in resp.get_json()["error"].lower()

    def test_import_returns_400_when_path_empty_string(self, logged_in_client):
        """JSON body with path='' → 400."""
        client = logged_in_client()
        resp = client.post("/api/v1/ota/usb/import", json={"path": ""})
        assert resp.status_code == 400

    def test_import_returns_400_when_import_fails(
        self, monkeypatch, app, logged_in_client
    ):
        """ota.import_from_usb() returns error → 400."""
        client = logged_in_client()
        monkeypatch.setattr(
            app.ota_service,
            "import_from_usb",
            lambda path, user="", ip="": (None, "file not found on USB"),
        )
        resp = client.post(
            "/api/v1/ota/usb/import", json={"path": "/mnt/usb/update.swu"}
        )
        assert resp.status_code == 400
        assert "file not found on USB" in resp.get_json()["error"]

    def test_import_returns_400_when_verify_fails(
        self, monkeypatch, app, logged_in_client
    ):
        """import_from_usb() succeeds but verify_bundle() fails → 400."""
        client = logged_in_client()
        import os
        import tempfile

        def _fake_import(path, user="", ip=""):
            fd, p = tempfile.mkstemp(suffix=".swu")
            os.write(fd, b"fake")
            os.close(fd)
            return p, ""

        monkeypatch.setattr(app.ota_service, "import_from_usb", _fake_import)
        monkeypatch.setattr(
            app.ota_service,
            "verify_bundle",
            lambda path: (False, "invalid signature"),
        )
        resp = client.post(
            "/api/v1/ota/usb/import", json={"path": "/mnt/usb/update.swu"}
        )
        assert resp.status_code == 400
        assert "invalid signature" in resp.get_json()["error"]

    def test_import_returns_200_on_success(self, monkeypatch, app, logged_in_client):
        """Full happy path: import + verify succeed → 200 with staged_path."""
        client = logged_in_client()
        import os
        import tempfile

        def _fake_import(path, user="", ip=""):
            fd, p = tempfile.mkstemp(suffix=".swu")
            os.write(fd, b"fake")
            os.close(fd)
            return p, ""

        monkeypatch.setattr(app.ota_service, "import_from_usb", _fake_import)
        monkeypatch.setattr(app.ota_service, "verify_bundle", lambda path: (True, ""))
        resp = client.post(
            "/api/v1/ota/usb/import", json={"path": "/mnt/usb/update.swu"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "staged" in data["message"].lower()
        assert "staged_path" in data
