# REQ: SWR-010; RISK: RISK-004; SEC: SC-003; TEST: TC-009, TC-013
"""Tests for OTAService — OTA update management."""

import os
from unittest.mock import MagicMock, patch

import pytest

from monitor.services import privileged
from monitor.services.ota_service import MAX_BUNDLE_SIZE, OTAService


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


def _write_swu(path: str, version: str) -> None:
    manifest = (
        'software = { version = "' + version + '"; raspberrypi4-64 = {}; };\n'
    ).encode("utf-8")
    with open(path, "wb") as f:
        f.write(_newc_entry("sw-description", manifest))


@pytest.fixture
def data_dir(tmp_path):
    """Create temp data directory structure."""
    for d in ["ota/inbox", "ota/staging", "certs"]:
        (tmp_path / d).mkdir(parents=True)
    return str(tmp_path)


@pytest.fixture
def svc(data_dir):
    """Create OTAService with mock dependencies."""
    store = MagicMock()
    audit = MagicMock()
    public_key_path = os.path.join(data_dir, "certs", "swupdate-public.crt")
    return OTAService(
        store=store,
        audit=audit,
        data_dir=data_dir,
        public_key_path=public_key_path,
    )


class TestGetSetStatus:
    """Test status tracking."""

    def test_default_status_idle(self, svc):
        status = svc.get_status("server")
        assert status["state"] == "idle"
        assert status["error"] == ""

    def test_set_status(self, svc):
        svc.set_status("server", "staged", version="1.1.0")
        status = svc.get_status("server")
        assert status["state"] == "staged"
        assert status["version"] == "1.1.0"

    def test_set_status_preserves_other_fields(self, svc):
        svc.set_status("cam-001", "pending", version="2.0")
        svc.set_status("cam-001", "installing")
        status = svc.get_status("cam-001")
        assert status["state"] == "installing"
        assert status["version"] == "2.0"

    def test_independent_device_status(self, svc):
        svc.set_status("server", "installing")
        svc.set_status("cam-001", "pending")
        assert svc.get_status("server")["state"] == "installing"
        assert svc.get_status("cam-001")["state"] == "pending"

    def test_discards_stale_staged_server_bundle_from_disk(self, svc, data_dir):
        staged = os.path.join(data_dir, "ota", "staging", "old.swu")
        _write_swu(staged, "1.4.1-dev")

        status = svc.get_status("server", current_version="1.6.0")

        assert status["state"] == "idle"
        assert "Rejected older update" in status["error"]
        assert not os.path.exists(staged)

    def test_discards_current_staged_server_bundle_from_disk(self, svc, data_dir):
        staged = os.path.join(data_dir, "ota", "staging", "same.swu")
        _write_swu(staged, "1.6.0")

        status = svc.get_status("server", current_version="1.6.0")

        assert status["state"] == "idle"
        assert not os.path.exists(staged)


class TestCheckSpace:
    """Test disk space checking."""

    def test_has_space(self, svc):
        has_space, free, err = svc.check_space(0)
        assert has_space is True
        assert free > 0
        assert err == ""

    def test_returns_free_bytes(self, svc):
        _, free, _ = svc.check_space(0)
        assert isinstance(free, int)
        assert free > 0

    def test_check_space_with_required(self, svc):
        # Request an absurdly large amount
        has_space, _, _ = svc.check_space(10**18)
        assert has_space is False

    def test_check_space_reports_disk_usage_error(self, svc):
        with patch(
            "monitor.services.ota_service.shutil.disk_usage",
            side_effect=OSError("denied"),
        ):
            has_space, free, err = svc.check_space()

        assert has_space is False
        assert free == 0
        assert "denied" in err


class TestStorageAndLed:
    def test_ensure_storage_repairs_with_privileged_helper(self, svc):
        with (
            patch.object(
                svc,
                "_create_storage_dirs",
                side_effect=["permission denied", ""],
            ),
            patch.object(svc, "_probe_storage_writable", side_effect=["", ""]),
            patch(
                "monitor.services.ota_service.privileged.should_use_helper",
                return_value=True,
            ),
            patch(
                "monitor.services.ota_service.privileged.request",
                return_value={},
            ) as request,
        ):
            ok, err = svc.ensure_storage()

        assert ok is True
        assert err == ""
        request.assert_called_once_with("ota.repair_storage", timeout=20)

    def test_ensure_storage_reports_failed_privileged_repair(self, svc):
        with (
            patch.object(svc, "_create_storage_dirs", return_value="root-owned"),
            patch.object(svc, "_probe_storage_writable", return_value="probe denied"),
            patch(
                "monitor.services.ota_service.privileged.should_use_helper",
                return_value=True,
            ),
            patch(
                "monitor.services.ota_service.privileged.request",
                side_effect=privileged.PrivilegedHelperError("helper down"),
            ),
        ):
            ok, err = svc.ensure_storage()

        assert ok is False
        assert "not writable" in err

    def test_probe_directory_writable_cleans_failed_probe(self, svc, tmp_path):
        probe = tmp_path / ".write-test"
        probe.write_text("left behind", encoding="utf-8")

        with (
            patch(
                "monitor.services.ota_service.uuid.uuid4",
                return_value=MagicMock(hex="fixed"),
            ),
            patch("monitor.services.ota_service.os.getpid", return_value=123),
            patch("monitor.services.ota_service.os.open", side_effect=OSError("full")),
            patch("monitor.services.ota_service.os.path.exists", return_value=True),
            patch("monitor.services.ota_service.os.unlink") as unlink,
        ):
            err = svc._probe_directory_writable(str(tmp_path))

        assert "full" in err
        unlink.assert_called_once_with(
            os.path.join(str(tmp_path), ".write-test-123-fixed")
        )

    def test_status_led_uses_helper_when_available(self, svc):
        with (
            patch(
                "monitor.services.ota_service.privileged.should_use_helper",
                return_value=True,
            ),
            patch(
                "monitor.services.ota_service.privileged.request",
                return_value={},
            ) as request,
        ):
            svc._set_status_led("healthy")

        request.assert_called_once_with(
            "led.set",
            {"state": "healthy", "role": "server", "force": True},
            timeout=10,
        )

    def test_status_led_falls_back_to_local_controller(self, svc):
        with (
            patch(
                "monitor.services.ota_service.privileged.should_use_helper",
                return_value=False,
            ),
            patch("monitor.services.ota_service.status_led.set_state") as set_state,
        ):
            svc._set_status_led("healthy")

        set_state.assert_called_once_with("healthy", role="server", force=True)

    def test_prepare_reboot_activation_marks_next_boot(self, svc):
        with (
            patch.object(svc, "_mark_activation") as mark,
            patch.object(svc, "_set_status_led") as set_led,
        ):
            svc.prepare_reboot_activation("1.6.2")

        mark.assert_called_once_with("1.6.2")
        set_led.assert_called_once_with("ota-rebooting")


class TestStageBundle:
    """Test bundle staging."""

    def test_stage_success(self, svc, data_dir):
        """Should move file to staging directory."""
        src = os.path.join(data_dir, "ota", "inbox", "update.swu")
        with open(src, "wb") as f:
            f.write(b"x" * 1024)

        path, err = svc.stage_bundle(src, "update.swu", user="admin", ip="1.2.3.4")
        assert err == ""
        assert path is not None
        assert os.path.isfile(path)
        assert not os.path.isfile(src)  # moved, not copied

    def test_rejects_non_swu(self, svc, data_dir):
        src = os.path.join(data_dir, "bad.zip")
        with open(src, "w") as f:
            f.write("data")
        _, err = svc.stage_bundle(src, "bad.zip")
        assert "swu" in err.lower()

    def test_rejects_empty_file(self, svc, data_dir):
        src = os.path.join(data_dir, "empty.swu")
        open(src, "w").close()
        _, err = svc.stage_bundle(src, "empty.swu")
        assert "empty" in err.lower()

    def test_rejects_missing_file(self, svc):
        _, err = svc.stage_bundle("/nonexistent/file.swu", "file.swu")
        assert "Cannot read" in err

    def test_rejects_oversized(self, svc, data_dir):
        """Should reject files over MAX_BUNDLE_SIZE."""
        src = os.path.join(data_dir, "big.swu")
        with open(src, "wb") as f:
            f.write(b"x" * 100)

        with patch("os.path.getsize", return_value=MAX_BUNDLE_SIZE + 1):
            _, err = svc.stage_bundle(src, "big.swu")
        assert "too large" in err.lower()

    def test_logs_audit(self, svc, data_dir):
        src = os.path.join(data_dir, "ota", "inbox", "update.swu")
        with open(src, "wb") as f:
            f.write(b"x" * 100)
        svc.stage_bundle(src, "update.swu", user="admin", ip="1.2.3.4")
        svc._audit.log_event.assert_called()
        assert "OTA_STAGED" in str(svc._audit.log_event.call_args)

    def test_sets_status_staged(self, svc, data_dir):
        src = os.path.join(data_dir, "ota", "inbox", "update.swu")
        with open(src, "wb") as f:
            f.write(b"x" * 100)
        svc.stage_bundle(src, "update.swu")
        assert svc.get_status("server")["state"] == "staged"

    def test_rejects_older_version_when_current_is_known(self, svc, data_dir):
        src = os.path.join(data_dir, "ota", "inbox", "old.swu")
        _write_swu(src, "1.4.1-dev")

        path, err = svc.stage_bundle(src, "old.swu", current_version="1.6.0")

        assert path is None
        assert "Rejected older update" in err
        assert not os.path.exists(os.path.join(data_dir, "ota", "staging", "old.swu"))

    def test_allows_newer_version_when_current_is_known(self, svc, data_dir):
        src = os.path.join(data_dir, "ota", "inbox", "new.swu")
        _write_swu(src, "1.7.0")

        path, err = svc.stage_bundle(src, "new.swu", current_version="1.6.0")

        assert err == ""
        assert path is not None
        status = svc.get_status("server", current_version="1.6.0")
        assert status["update_relation"] == "upgrade"


class TestVerifyBundle:
    """Test bundle signature verification."""

    def test_missing_bundle(self, svc):
        valid, err = svc.verify_bundle("/nonexistent/file.swu")
        assert valid is False
        assert "not found" in err

    def test_no_public_key_skips_verification(self, svc, data_dir):
        """Should skip verification when no public key exists."""
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        valid, err = svc.verify_bundle(bundle)
        assert valid is True
        assert err == ""

    def test_verification_posture_warns_on_dev_fallback(self, monkeypatch, svc):
        monkeypatch.setattr("monitor.services.ota_service.shutil.which", lambda _: None)

        posture = svc.get_verification_posture()

        assert posture["mode"] == "dev-fallback"
        assert posture["allows_unsigned_fallback"] is True
        assert posture["install_blocked"] is False
        assert "development fallback" in posture["warning"]

    def test_enforced_missing_public_key_fails_closed(self, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        marker = os.path.join(data_dir, "swupdate-enforce")
        with open(marker, "w") as f:
            f.write("1")
        svc = OTAService(
            store=MagicMock(),
            audit=MagicMock(),
            data_dir=data_dir,
            public_key_path=os.path.join(data_dir, "certs", "swupdate-public.crt"),
            enforce_marker_path=marker,
        )

        valid, err = svc.verify_bundle(bundle)

        assert valid is False
        assert "verification certificate is missing" in err

    @patch("monitor.services.ota_service.subprocess.run")
    def test_verify_success(self, mock_run, svc, data_dir):
        """Should return True when swupdate verification passes."""
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        valid, err = svc.verify_bundle(bundle)
        assert valid is True

    @patch("monitor.services.ota_service.subprocess.run")
    def test_verify_failure(self, mock_run, svc, data_dir):
        """Should return False when signature is invalid."""
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="bad signature"
        )
        valid, err = svc.verify_bundle(bundle)
        assert valid is False
        assert "bad signature" in err
        assert mock_run.call_args.kwargs["env"]["TMPDIR"] == os.path.join(
            data_dir, "ota", "tmp"
        )

    @patch("monitor.services.ota_service.subprocess.run")
    def test_swupdate_not_found(self, mock_run, svc, data_dir):
        """Should skip verification when swupdate not installed."""
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        mock_run.side_effect = FileNotFoundError
        valid, err = svc.verify_bundle(bundle)
        assert valid is True  # dev mode fallback

    @patch("monitor.services.ota_service.subprocess.run")
    def test_enforced_swupdate_not_found_fails_closed(self, mock_run, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")
        marker = os.path.join(data_dir, "swupdate-enforce")
        with open(marker, "w") as f:
            f.write("1")
        svc = OTAService(
            store=MagicMock(),
            audit=MagicMock(),
            data_dir=data_dir,
            public_key_path=key,
            enforce_marker_path=marker,
        )

        mock_run.side_effect = FileNotFoundError
        valid, err = svc.verify_bundle(bundle)

        assert valid is False
        assert "swupdate is not installed" in err

    def test_verify_uses_privileged_helper_when_available(self, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        with (
            patch(
                "monitor.services.ota_service.privileged.should_use_helper",
                return_value=True,
            ),
            patch(
                "monitor.services.ota_service.privileged.request",
                return_value={"returncode": 0, "stderr": ""},
            ) as request,
        ):
            valid, err = svc.verify_bundle(bundle)

        assert valid is True
        assert err == ""
        request.assert_called_once_with(
            "ota.verify",
            {"bundle_path": bundle, "public_key_path": key},
            timeout=60,
        )

    def test_verify_helper_failure_returns_stderr(self, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        with (
            patch(
                "monitor.services.ota_service.privileged.should_use_helper",
                return_value=True,
            ),
            patch(
                "monitor.services.ota_service.privileged.request",
                return_value={"returncode": 1, "stderr": "bad signature"},
            ),
        ):
            valid, err = svc.verify_bundle(bundle)

        assert valid is False
        assert err == "bad signature"


class TestInstallBundle:
    """Test bundle installation via swupdate."""

    def test_missing_bundle(self, svc):
        ok, err = svc.install_bundle("/nonexistent.swu")
        assert ok is False
        assert "not found" in err

    # Install now shells out via subprocess.Popen + a ticker thread
    # (so the UI can see a rising progress bar while swupdate writes).
    # Tests patch Popen and return a MagicMock whose communicate()
    # yields (stdout, stderr) and whose returncode matches each case.
    def _popen_mock(self, returncode=0, stderr=""):
        proc = MagicMock()
        proc.communicate.return_value = ("", stderr)
        proc.returncode = returncode
        return proc

    @patch("monitor.services.ota_service.subprocess.Popen")
    def test_install_success(self, mock_popen, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        mock_popen.return_value = self._popen_mock(returncode=0)
        ok, err = svc.install_bundle(bundle, user="admin", ip="1.2.3.4")
        assert ok is True
        assert svc.get_status("server")["state"] == "installed"
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == [
            "swupdate",
            "-i",
            bundle,
            "-k",
            key,
        ]
        assert mock_popen.call_args.kwargs["env"]["TMPDIR"] == os.path.join(
            data_dir, "ota", "tmp"
        )

    @patch("monitor.services.ota_service.subprocess.Popen")
    def test_install_failure(self, mock_popen, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        mock_popen.return_value = self._popen_mock(returncode=1, stderr="write failed")
        ok, err = svc.install_bundle(bundle)
        assert ok is False
        assert svc.get_status("server")["state"] == "error"
        assert mock_popen.call_args[0][0] == [
            "swupdate",
            "-i",
            bundle,
            "-k",
            key,
        ]
        assert mock_popen.call_args.kwargs["env"]["TMPDIR"] == os.path.join(
            data_dir, "ota", "tmp"
        )

    @patch("monitor.services.ota_service.subprocess.Popen")
    def test_install_without_key_uses_plain_command(self, mock_popen, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_popen.return_value = self._popen_mock(returncode=0)
        ok, err = svc.install_bundle(bundle)
        assert ok is True
        assert err == ""
        assert mock_popen.call_args[0][0] == ["swupdate", "-i", bundle]
        assert mock_popen.call_args.kwargs["env"]["TMPDIR"] == os.path.join(
            data_dir, "ota", "tmp"
        )

    @patch("monitor.services.ota_service.subprocess.Popen")
    def test_install_swupdate_not_found(self, mock_popen, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_popen.side_effect = FileNotFoundError
        ok, err = svc.install_bundle(bundle)
        assert ok is False
        assert "not installed" in err

    @patch("monitor.services.ota_service.subprocess.Popen")
    def test_install_timeout(self, mock_popen, svc, data_dir):
        import subprocess

        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        # First communicate() raises TimeoutExpired; after Popen.kill
        # the code calls communicate() again to drain, which returns
        # ("", "") on our mock.
        proc = MagicMock()
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired("swupdate", 600),
            ("", ""),
        ]
        proc.returncode = -9
        mock_popen.return_value = proc

        ok, err = svc.install_bundle(bundle)
        assert ok is False
        assert "timed out" in err

    @patch("monitor.services.ota_service.subprocess.Popen")
    def test_install_logs_audit(self, mock_popen, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_popen.return_value = self._popen_mock(returncode=0)
        svc.install_bundle(bundle, user="admin", ip="1.2.3.4")
        calls = [str(c) for c in svc._audit.log_event.call_args_list]
        assert any("OTA_INSTALL_START" in c for c in calls)
        assert any("OTA_INSTALL_COMPLETE" in c for c in calls)

    def test_install_uses_privileged_helper(self, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.crt")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        with (
            patch(
                "monitor.services.ota_service.privileged.should_use_helper",
                return_value=True,
            ),
            patch(
                "monitor.services.ota_service.privileged.request",
                return_value={"returncode": 0, "stderr": ""},
            ) as request,
        ):
            ok, err = svc.install_bundle(bundle)

        assert ok is True
        assert err == ""
        request.assert_any_call(
            "ota.install",
            {"bundle_path": bundle, "public_key_path": key},
            timeout=600,
        )

    def test_install_privileged_helper_error_sets_error_status(self, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        with (
            patch(
                "monitor.services.ota_service.privileged.should_use_helper",
                return_value=True,
            ),
            patch(
                "monitor.services.ota_service.privileged.request",
                side_effect=privileged.PrivilegedHelperError("helper denied"),
            ),
        ):
            ok, err = svc.install_bundle(bundle)

        assert ok is False
        assert err == "helper denied"
        assert svc.get_status("server")["state"] == "error"


class TestCleanStaging:
    """Test staging directory cleanup."""

    def test_clean_removes_files(self, svc, data_dir):
        staging = os.path.join(data_dir, "ota", "staging")
        with open(os.path.join(staging, "old.swu"), "w") as f:
            f.write("old")
        svc.clean_staging()
        assert os.path.isdir(staging)
        assert len(os.listdir(staging)) == 0

    def test_clean_handles_missing_dir(self, svc, data_dir):
        """Should not fail if staging dir doesn't exist."""
        import shutil

        staging = os.path.join(data_dir, "ota", "staging")
        shutil.rmtree(staging)
        svc.clean_staging()  # Should not raise


class TestScanUsb:
    """Test USB .swu bundle scanning."""

    @patch("monitor.services.usb.detect_devices")
    def test_scan_finds_bundles(self, mock_detect, svc, data_dir):
        """Should find .swu files on mounted USB devices."""
        usb_mount = os.path.join(data_dir, "usb_mount")
        os.makedirs(usb_mount)
        swu_path = os.path.join(usb_mount, "update-1.2.swu")
        with open(swu_path, "wb") as f:
            f.write(b"x" * 256)

        mock_detect.return_value = [{"path": "/dev/sda1", "mountpoint": usb_mount}]

        bundles = svc.scan_usb()
        assert len(bundles) == 1
        assert bundles[0]["filename"] == "update-1.2.swu"
        assert bundles[0]["size"] == 256
        assert bundles[0]["device"] == "/dev/sda1"

    @patch("monitor.services.usb.detect_devices")
    def test_scan_searches_subdirs(self, mock_detect, svc, data_dir):
        """Should search updates/ and ota/ subdirectories."""
        usb_mount = os.path.join(data_dir, "usb_mount2")
        updates_dir = os.path.join(usb_mount, "updates")
        os.makedirs(updates_dir)
        swu_path = os.path.join(updates_dir, "camera-2.0.swu")
        with open(swu_path, "wb") as f:
            f.write(b"x" * 128)

        mock_detect.return_value = [{"path": "/dev/sda1", "mountpoint": usb_mount}]

        bundles = svc.scan_usb()
        assert len(bundles) == 1
        assert bundles[0]["filename"] == "camera-2.0.swu"

    @patch("monitor.services.usb.detect_devices")
    def test_scan_ignores_non_swu(self, mock_detect, svc, data_dir):
        """Should skip non-.swu files."""
        usb_mount = os.path.join(data_dir, "usb_mount3")
        os.makedirs(usb_mount)
        with open(os.path.join(usb_mount, "readme.txt"), "w") as f:
            f.write("not an update")
        with open(os.path.join(usb_mount, "image.img"), "wb") as f:
            f.write(b"x" * 64)

        mock_detect.return_value = [{"path": "/dev/sda1", "mountpoint": usb_mount}]

        bundles = svc.scan_usb()
        assert len(bundles) == 0

    @patch("monitor.services.usb.detect_devices")
    def test_scan_skips_unmounted(self, mock_detect, svc):
        """Should skip USB devices that aren't mounted."""
        mock_detect.return_value = [{"path": "/dev/sda1", "mountpoint": ""}]
        bundles = svc.scan_usb()
        assert len(bundles) == 0

    @patch("monitor.services.usb.detect_devices")
    def test_scan_no_usb(self, mock_detect, svc):
        """Should return empty list when no USB devices found."""
        mock_detect.return_value = []
        bundles = svc.scan_usb()
        assert len(bundles) == 0

    @patch("monitor.services.usb.detect_devices")
    def test_scan_handles_detection_error(self, mock_detect, svc):
        """Should return empty list on detection failure."""
        mock_detect.side_effect = RuntimeError("lsblk broken")
        bundles = svc.scan_usb()
        assert len(bundles) == 0


class TestImportFromUsb:
    """Test USB .swu bundle import."""

    def test_import_success(self, svc, data_dir):
        """Should copy .swu from USB and stage it."""
        usb_file = os.path.join(data_dir, "usb", "update.swu")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x" * 512)

        staged, err = svc.import_from_usb(usb_file, user="admin", ip="1.2.3.4")
        assert err == ""
        assert staged is not None
        assert os.path.isfile(staged)
        # Original on USB should still exist (copy, not move)
        assert os.path.isfile(usb_file)

    def test_import_preserves_original(self, svc, data_dir):
        """Should preserve the original file on USB."""
        usb_file = os.path.join(data_dir, "usb2", "firmware.swu")
        os.makedirs(os.path.dirname(usb_file))
        content = b"firmware content"
        with open(usb_file, "wb") as f:
            f.write(content)

        svc.import_from_usb(usb_file)
        with open(usb_file, "rb") as f:
            assert f.read() == content

    def test_import_rejects_non_swu(self, svc, data_dir):
        """Should reject non-.swu files."""
        usb_file = os.path.join(data_dir, "usb3", "image.img")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x" * 64)
        _, err = svc.import_from_usb(usb_file)
        assert "swu" in err.lower()

    def test_import_missing_file(self, svc):
        """Should return error for missing file."""
        _, err = svc.import_from_usb("/nonexistent/update.swu")
        assert "not found" in err.lower()

    def test_import_empty_file(self, svc, data_dir):
        """Should reject empty files."""
        usb_file = os.path.join(data_dir, "usb4", "empty.swu")
        os.makedirs(os.path.dirname(usb_file))
        open(usb_file, "w").close()
        _, err = svc.import_from_usb(usb_file)
        assert "empty" in err.lower()

    def test_import_oversized(self, svc, data_dir):
        """Should reject files over MAX_BUNDLE_SIZE."""
        usb_file = os.path.join(data_dir, "usb5", "big.swu")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x" * 100)

        with patch("os.path.getsize", return_value=MAX_BUNDLE_SIZE + 1):
            _, err = svc.import_from_usb(usb_file)
        assert "too large" in err.lower()

    def test_import_sets_staged_status(self, svc, data_dir):
        """Should set status to staged after import."""
        usb_file = os.path.join(data_dir, "usb6", "update.swu")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x" * 128)
        svc.import_from_usb(usb_file)
        assert svc.get_status("server")["state"] == "staged"

    def test_import_logs_audit(self, svc, data_dir):
        """Should log USB import audit event."""
        usb_file = os.path.join(data_dir, "usb7", "update.swu")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x" * 128)
        svc.import_from_usb(usb_file, user="admin", ip="1.2.3.4")
        calls = [str(c) for c in svc._audit.log_event.call_args_list]
        assert any("OTA_USB_IMPORT" in c for c in calls)

    def test_import_reports_unreadable_size(self, svc, data_dir):
        usb_file = os.path.join(data_dir, "usb8", "update.swu")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x")

        with patch(
            "monitor.services.ota_service.os.path.getsize", side_effect=OSError("gone")
        ):
            staged, err = svc.import_from_usb(usb_file)

        assert staged is None
        assert "Cannot read file" in err

    def test_import_reports_insufficient_space(self, svc, data_dir):
        usb_file = os.path.join(data_dir, "usb9", "update.swu")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x")

        with patch.object(svc, "check_space", return_value=(False, 12, "full")):
            staged, err = svc.import_from_usb(usb_file)

        assert staged is None
        assert "Insufficient disk space" in err

    def test_import_reports_copy_failure(self, svc, data_dir):
        usb_file = os.path.join(data_dir, "usb10", "update.swu")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x")

        with patch(
            "monitor.services.ota_service.shutil.copy2", side_effect=OSError("denied")
        ):
            staged, err = svc.import_from_usb(usb_file)

        assert staged is None
        assert "Failed to copy from USB" in err

    def test_import_removes_inbox_copy_when_stage_fails(self, svc, data_dir):
        usb_file = os.path.join(data_dir, "usb11", "update.swu")
        os.makedirs(os.path.dirname(usb_file))
        with open(usb_file, "wb") as f:
            f.write(b"x")

        with (
            patch.object(svc, "stage_bundle", return_value=(None, "bad version")),
            patch("monitor.services.ota_service.os.unlink") as unlink,
        ):
            staged, err = svc.import_from_usb(usb_file)

        assert staged is None
        assert err == "bad version"
        unlink.assert_called_once()


class TestAuditResilience:
    """Test that audit failures don't crash the service."""

    def test_audit_error_ignored(self, data_dir):
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("audit broken")
        svc = OTAService(store=MagicMock(), audit=audit, data_dir=data_dir)

        src = os.path.join(data_dir, "ota", "inbox", "update.swu")
        with open(src, "wb") as f:
            f.write(b"x" * 100)
        # Should not raise despite audit failure
        svc.stage_bundle(src, "update.swu", user="admin")
