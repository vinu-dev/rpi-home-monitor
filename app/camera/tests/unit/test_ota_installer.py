# REQ: SWR-038, SWR-010; RISK: RISK-004; SEC: SC-003; TEST: TC-036, TC-013
"""Unit tests for camera-side OTA installer client."""

import io
import os

import pytest

from camera_streamer import ota_installer


@pytest.fixture
def spool(tmp_path, monkeypatch):
    spool_dir = tmp_path / "spool"
    staging = spool_dir / "staging"
    staging.mkdir(parents=True)
    monkeypatch.setattr(ota_installer, "SPOOL_DIR", str(spool_dir))
    monkeypatch.setattr(ota_installer, "STAGING_DIR", str(staging))
    monkeypatch.setattr(ota_installer, "TRIGGER_PATH", str(spool_dir / "trigger"))
    monkeypatch.setattr(
        ota_installer,
        "REBOOT_TRIGGER_PATH",
        str(spool_dir / "reboot-trigger"),
    )
    monkeypatch.setattr(ota_installer, "STATUS_PATH", str(spool_dir / "status.json"))
    return spool_dir


class TestReadWriteStatus:
    def test_reads_default_when_missing(self, spool):
        status = ota_installer.read_status()
        assert status["state"] == "idle"
        assert status["progress"] == 0
        assert status["error"] == ""

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


class TestTriggerReboot:
    """The /api/ota/reboot HTTP handler in status_server runs as
    User=camera; the legacy ``reboot`` binary fails for that user
    ("Failed to unlink reboot parameter file: Read-only file system")
    so the camera-streamer drops a trigger file and the root-side
    ``camera-ota-reboot.path`` -> ``camera-ota-reboot.service``
    performs ``systemctl reboot``. Tests below cover the user-side
    half of that contract: write the trigger atomically, return a
    sane (ok, msg) tuple."""

    def test_writes_trigger_atomically(self, spool):
        ok, msg = ota_installer.trigger_reboot()
        assert ok is True
        assert "trigger" in msg.lower()
        assert os.path.isfile(ota_installer.REBOOT_TRIGGER_PATH)
        # Trigger contents are a unix timestamp on a single line —
        # gives the operator a way to tell stale triggers apart.
        with open(ota_installer.REBOOT_TRIGGER_PATH) as f:
            content = f.read().strip()
        assert content.isdigit()
        # Atomic-write contract: no leftover temp file in the spool.
        leftovers = [
            n for n in os.listdir(str(spool)) if n.startswith(".reboot-trigger.")
        ]
        assert leftovers == []

    def test_independent_of_install_trigger(self, spool):
        # Writing the reboot trigger must NOT also fire the install
        # trigger (they're separate files; separate path units).
        ota_installer.trigger_reboot()
        assert os.path.isfile(ota_installer.REBOOT_TRIGGER_PATH)
        assert not os.path.exists(ota_installer.TRIGGER_PATH)

    def test_creates_spool_dir_if_missing(self, tmp_path, monkeypatch):
        # Fresh spool that doesn't yet exist (e.g. the tmpfiles.d rule
        # hasn't fired yet on a freshly-flashed dev rebuild). Helper
        # should create it idempotently rather than failing.
        spool_dir = tmp_path / "fresh-spool"
        monkeypatch.setattr(ota_installer, "SPOOL_DIR", str(spool_dir))
        monkeypatch.setattr(
            ota_installer,
            "REBOOT_TRIGGER_PATH",
            str(spool_dir / "reboot-trigger"),
        )
        ok, _ = ota_installer.trigger_reboot()
        assert ok is True
        assert os.path.isdir(str(spool_dir))


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
