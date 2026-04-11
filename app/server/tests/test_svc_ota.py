"""Tests for OTAService — OTA update management."""

import os
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.ota_service import MAX_BUNDLE_SIZE, OTAService


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
    return OTAService(store=store, audit=audit, data_dir=data_dir)


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
        svc._audit.log.assert_called()
        assert "OTA_STAGED" in str(svc._audit.log.call_args)

    def test_sets_status_staged(self, svc, data_dir):
        src = os.path.join(data_dir, "ota", "inbox", "update.swu")
        with open(src, "wb") as f:
            f.write(b"x" * 100)
        svc.stage_bundle(src, "update.swu")
        assert svc.get_status("server")["state"] == "staged"


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

    @patch("monitor.services.ota_service.subprocess.run")
    def test_verify_success(self, mock_run, svc, data_dir):
        """Should return True when swupdate verification passes."""
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.pem")
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
        key = os.path.join(data_dir, "certs", "swupdate-public.pem")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="bad signature"
        )
        valid, err = svc.verify_bundle(bundle)
        assert valid is False
        assert "bad signature" in err

    @patch("monitor.services.ota_service.subprocess.run")
    def test_swupdate_not_found(self, mock_run, svc, data_dir):
        """Should skip verification when swupdate not installed."""
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key = os.path.join(data_dir, "certs", "swupdate-public.pem")
        with open(key, "w") as f:
            f.write("PUBLIC KEY")

        mock_run.side_effect = FileNotFoundError
        valid, err = svc.verify_bundle(bundle)
        assert valid is True  # dev mode fallback


class TestInstallBundle:
    """Test bundle installation via swupdate."""

    def test_missing_bundle(self, svc):
        ok, err = svc.install_bundle("/nonexistent.swu")
        assert ok is False
        assert "not found" in err

    @patch("monitor.services.ota_service.subprocess.run")
    def test_install_success(self, mock_run, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, err = svc.install_bundle(bundle, user="admin", ip="1.2.3.4")
        assert ok is True
        assert svc.get_status("server")["state"] == "installed"

    @patch("monitor.services.ota_service.subprocess.run")
    def test_install_failure(self, mock_run, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="write failed"
        )
        ok, err = svc.install_bundle(bundle)
        assert ok is False
        assert svc.get_status("server")["state"] == "error"

    @patch("monitor.services.ota_service.subprocess.run")
    def test_install_swupdate_not_found(self, mock_run, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.side_effect = FileNotFoundError
        ok, err = svc.install_bundle(bundle)
        assert ok is False
        assert "not installed" in err

    @patch("monitor.services.ota_service.subprocess.run")
    def test_install_timeout(self, mock_run, svc, data_dir):
        import subprocess

        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.side_effect = subprocess.TimeoutExpired("swupdate", 600)
        ok, err = svc.install_bundle(bundle)
        assert ok is False
        assert "timed out" in err

    @patch("monitor.services.ota_service.subprocess.run")
    def test_install_logs_audit(self, mock_run, svc, data_dir):
        bundle = os.path.join(data_dir, "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        svc.install_bundle(bundle, user="admin", ip="1.2.3.4")
        calls = [str(c) for c in svc._audit.log.call_args_list]
        assert any("OTA_INSTALL_START" in c for c in calls)
        assert any("OTA_INSTALL_COMPLETE" in c for c in calls)


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


class TestAuditResilience:
    """Test that audit failures don't crash the service."""

    def test_audit_error_ignored(self, data_dir):
        audit = MagicMock()
        audit.log.side_effect = RuntimeError("audit broken")
        svc = OTAService(store=MagicMock(), audit=audit, data_dir=data_dir)

        src = os.path.join(data_dir, "ota", "inbox", "update.swu")
        with open(src, "wb") as f:
            f.write(b"x" * 100)
        # Should not raise despite audit failure
        svc.stage_bundle(src, "update.swu", user="admin")
