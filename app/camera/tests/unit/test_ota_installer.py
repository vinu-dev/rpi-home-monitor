# REQ: SWR-038, SWR-010; RISK: RISK-004; SEC: SC-003; TEST: TC-036, TC-013
"""Unit tests for camera-side OTA installer client."""

import io
import os

import pytest

from camera_streamer import ota_installer


def _newc_entry(name: str, data: bytes) -> bytes:
    name_bytes = name.encode("utf-8") + b"\0"
    fields = [
        "070701",
        f"{1:08x}",
        f"{0o100644:08x}",
        f"{0:08x}",
        f"{0:08x}",
        f"{1:08x}",
        f"{0:08x}",
        f"{len(data):08x}",
        f"{0:08x}",
        f"{0:08x}",
        f"{0:08x}",
        f"{0:08x}",
        f"{len(name_bytes):08x}",
        f"{0:08x}",
    ]
    out = "".join(fields).encode("ascii") + name_bytes
    out += b"\0" * ((4 - len(out) % 4) % 4)
    out += data
    out += b"\0" * ((4 - len(out) % 4) % 4)
    return out


def _swu_bytes(version: str) -> bytes:
    manifest = (
        'software = { version = "' + version + '"; home-monitor-camera = {}; };\n'
    ).encode("utf-8")
    return _newc_entry("sw-description", manifest)


@pytest.fixture
def spool(tmp_path, monkeypatch):
    spool_dir = tmp_path / "spool"
    staging = spool_dir / "staging"
    staging.mkdir(parents=True)
    monkeypatch.setattr(ota_installer, "SPOOL_DIR", str(spool_dir))
    monkeypatch.setattr(ota_installer, "STAGING_DIR", str(staging))
    monkeypatch.setattr(ota_installer, "TRIGGER_PATH", str(spool_dir / "trigger"))
    monkeypatch.setattr(ota_installer, "STATUS_PATH", str(spool_dir / "status.json"))
    return spool_dir


class TestReadWriteStatus:
    def test_reads_default_when_missing(self, spool):
        status = ota_installer.read_status()
        assert status["state"] == "idle"
        assert status["progress"] == 0
        assert status["error"] == ""
        assert "verification" in status

    def test_roundtrip(self, spool):
        ota_installer.write_status("installing", progress=55, error="")
        status = ota_installer.read_status()
        assert status["state"] == "installing"
        assert status["progress"] == 55

    def test_read_tolerates_corrupt_file(self, spool):
        with open(ota_installer.STATUS_PATH, "w") as f:
            f.write("{not valid json")
        status = ota_installer.read_status()
        assert status["state"] == "idle"


class TestVerificationPosture:
    def test_warns_when_camera_uses_dev_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ota_installer, "PUBKEY_SYSTEM", str(tmp_path / "missing"))
        monkeypatch.setattr(ota_installer, "PUBKEY_DATA", str(tmp_path / "missing2"))
        monkeypatch.setattr(
            ota_installer, "SWUPDATE_ENFORCE_MARKER", str(tmp_path / "enforce")
        )
        monkeypatch.setattr(ota_installer.shutil, "which", lambda _: None)

        posture = ota_installer.verification_posture()

        assert posture["mode"] == "dev-fallback"
        assert posture["allows_unsigned_fallback"] is True
        assert posture["install_blocked"] is False

    def test_enforcement_blocks_when_verifier_missing(self, tmp_path, monkeypatch):
        marker = tmp_path / "enforce"
        marker.write_text("1")
        monkeypatch.setattr(ota_installer, "PUBKEY_SYSTEM", str(tmp_path / "missing"))
        monkeypatch.setattr(ota_installer, "PUBKEY_DATA", str(tmp_path / "missing2"))
        monkeypatch.setattr(ota_installer, "SWUPDATE_ENFORCE_MARKER", str(marker))
        monkeypatch.setattr(ota_installer.shutil, "which", lambda _: None)

        posture = ota_installer.verification_posture()

        assert posture["mode"] == "blocked"
        assert posture["install_blocked"] is True
        assert posture["allows_unsigned_fallback"] is False


class TestIsBusy:
    def test_idle_is_not_busy(self, spool):
        assert ota_installer.is_busy() is False

    def test_trigger_file_is_busy(self, spool):
        open(ota_installer.TRIGGER_PATH, "w").close()
        assert ota_installer.is_busy() is True

    def test_installing_state_is_busy(self, spool):
        ota_installer.write_status("installing", progress=50)
        assert ota_installer.is_busy() is True

    def test_installed_state_is_not_busy(self, spool):
        ota_installer.write_status("installed", progress=100)
        assert ota_installer.is_busy() is False


class TestStageBundle:
    def test_streams_and_renames(self, spool):
        data = b"swupdate-bundle-contents" * 100
        src = io.BytesIO(data)
        ok, path = ota_installer.stage_bundle(src, len(data))
        assert ok is True
        assert path == os.path.join(str(spool / "staging"), "update.swu")
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read() == data
        # No leftover .partial file
        assert not os.path.isfile(path + ".partial")

    def test_rejects_incomplete(self, spool):
        src = io.BytesIO(b"short")
        ok, msg = ota_installer.stage_bundle(src, 1000)
        assert ok is False
        assert "incomplete" in msg.lower()

    def test_invokes_progress_cb(self, spool):
        data = b"x" * 200
        src = io.BytesIO(data)
        calls = []
        ota_installer.stage_bundle(
            src, len(data), progress_cb=lambda s, t: calls.append((s, t))
        )
        assert calls, "progress_cb should fire"
        assert calls[-1] == (len(data), len(data))

    def test_rejects_older_bundle(self, spool, monkeypatch):
        monkeypatch.setattr(
            "camera_streamer.ota_installer.release_version", lambda: "1.6.0"
        )
        data = _swu_bytes("1.4.1-dev")

        ok, msg = ota_installer.stage_bundle(io.BytesIO(data), len(data))

        assert ok is False
        assert "Rejected older update" in msg
        assert not os.path.exists(ota_installer.bundle_path())
        assert ota_installer.read_status()["state"] == "error"

    def test_allows_newer_bundle(self, spool, monkeypatch):
        monkeypatch.setattr(
            "camera_streamer.ota_installer.release_version", lambda: "1.6.0"
        )
        data = _swu_bytes("1.7.0")

        ok, path = ota_installer.stage_bundle(io.BytesIO(data), len(data))

        assert ok is True
        assert path == ota_installer.bundle_path()
        status = ota_installer.read_status()
        assert status["target_version"] == "1.7.0"
        assert status["update_relation"] == "upgrade"


class TestTriggerInstall:
    def test_writes_trigger_and_status(self, spool):
        # Pre-stage a bundle so trigger_install() doesn't bail.
        bundle = ota_installer.bundle_path()
        with open(bundle, "wb") as f:
            f.write(b"x")
        ok, msg = ota_installer.trigger_install()
        assert ok is True
        assert os.path.isfile(ota_installer.TRIGGER_PATH)
        with open(ota_installer.TRIGGER_PATH) as f:
            assert bundle in f.read()
        assert ota_installer.read_status()["state"] == "verifying"

    def test_missing_bundle_returns_error(self, spool):
        ok, msg = ota_installer.trigger_install("/no/such/file.swu")
        assert ok is False
        assert "missing" in msg.lower()


class TestWaitForCompletion:
    def test_returns_installed(self, spool, monkeypatch):
        ota_installer.write_status("installed", progress=100)
        monkeypatch.setattr(ota_installer.time, "sleep", lambda s: None)
        status = ota_installer.wait_for_completion(timeout=1)
        assert status["state"] == "installed"

    def test_returns_error(self, spool, monkeypatch):
        ota_installer.write_status("error", progress=30, error="bad sig")
        monkeypatch.setattr(ota_installer.time, "sleep", lambda s: None)
        status = ota_installer.wait_for_completion(timeout=1)
        assert status["state"] == "error"
        assert status["error"] == "bad sig"

    def test_start_timeout_bails(self, spool, monkeypatch):
        # Status never transitions out of idle → installer didn't fire.
        monkeypatch.setattr(ota_installer, "TRIGGER_START_TIMEOUT", 0)
        monkeypatch.setattr(ota_installer.time, "sleep", lambda s: None)
        status = ota_installer.wait_for_completion(timeout=2, poll_interval=0)
        assert status["state"] == "error"
        assert "installer did not start" in status["error"].lower()
