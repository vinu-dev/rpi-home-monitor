"""Tests for CertService — certificate monitoring and renewal."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.cert_service import (
    EXPIRY_WARNING_DAYS,
    CertService,
)


@pytest.fixture
def certs_dir(tmp_path):
    """Create a temp certs directory with fake cert files."""
    d = tmp_path / "certs"
    d.mkdir()
    (d / "server.crt").write_text("FAKE SERVER CERT")
    (d / "server.key").write_text("FAKE SERVER KEY")
    (d / "ca.crt").write_text("FAKE CA CERT")
    (d / "ca.key").write_text("FAKE CA KEY")
    return str(d)


@pytest.fixture
def svc(certs_dir):
    """Create a CertService with a mock audit logger."""
    audit = MagicMock()
    return CertService(certs_dir=certs_dir, audit=audit)


class TestCertPaths:
    """Test certificate path properties."""

    def test_server_cert_path(self, svc, certs_dir):
        assert svc.server_cert_path == os.path.join(certs_dir, "server.crt")

    def test_server_key_path(self, svc, certs_dir):
        assert svc.server_key_path == os.path.join(certs_dir, "server.key")

    def test_ca_cert_path(self, svc, certs_dir):
        assert svc.ca_cert_path == os.path.join(certs_dir, "ca.crt")

    def test_ca_key_path(self, svc, certs_dir):
        assert svc.ca_key_path == os.path.join(certs_dir, "ca.key")


class TestCheckExpiry:
    """Test certificate expiry checking."""

    @patch("monitor.services.cert_service.subprocess.run")
    def test_check_expiry_success(self, mock_run, svc):
        """Should parse openssl output and return expiry info."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="notAfter=May 15 12:00:00 2031 GMT",
            stderr="",
        )
        expiry, days, err = svc.check_expiry()
        assert err == ""
        assert expiry is not None
        assert expiry.year == 2031
        assert expiry.month == 5
        assert days > 0

    @patch("monitor.services.cert_service.subprocess.run")
    def test_check_expiry_stores_date(self, mock_run, svc):
        """Should store expiry date for property access."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="notAfter=Dec 31 23:59:59 2030 GMT",
            stderr="",
        )
        svc.check_expiry()
        assert svc.expiry_date is not None
        assert svc.expiry_date.year == 2030

    def test_check_expiry_missing_cert(self, tmp_path):
        """Should return error if cert file doesn't exist."""
        svc = CertService(certs_dir=str(tmp_path / "nonexistent"))
        expiry, days, err = svc.check_expiry()
        assert expiry is None
        assert "not found" in err

    @patch("monitor.services.cert_service.subprocess.run")
    def test_check_expiry_openssl_error(self, mock_run, svc):
        """Should return error on openssl failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="unable to load certificate",
        )
        expiry, days, err = svc.check_expiry()
        assert expiry is None
        assert "openssl error" in err

    @patch("monitor.services.cert_service.subprocess.run")
    def test_check_expiry_timeout(self, mock_run, svc):
        """Should handle openssl timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("openssl", 10)
        expiry, days, err = svc.check_expiry()
        assert expiry is None
        assert "timed out" in err


class TestDaysUntilExpiry:
    """Test days_until_expiry property."""

    def test_none_when_unknown(self, svc):
        assert svc.days_until_expiry is None

    def test_returns_days(self, svc):
        svc._expiry_date = datetime.now(UTC) + timedelta(days=100)
        days = svc.days_until_expiry
        assert 99 <= days <= 100

    def test_zero_when_expired(self, svc):
        svc._expiry_date = datetime.now(UTC) - timedelta(days=10)
        assert svc.days_until_expiry == 0


class TestNeedsRenewal:
    """Test needs_renewal property."""

    def test_false_when_unknown(self, svc):
        assert svc.needs_renewal is False

    def test_false_when_far_from_expiry(self, svc):
        svc._expiry_date = datetime.now(UTC) + timedelta(days=365)
        assert svc.needs_renewal is False

    def test_true_when_within_warning(self, svc):
        svc._expiry_date = datetime.now(UTC) + timedelta(days=EXPIRY_WARNING_DAYS - 1)
        assert svc.needs_renewal is True

    def test_true_when_expired(self, svc):
        svc._expiry_date = datetime.now(UTC) - timedelta(days=1)
        assert svc.needs_renewal is True


class TestRenewServerCert:
    """Test server certificate renewal."""

    @patch("monitor.services.cert_service.subprocess.run")
    def test_renew_success(self, mock_run, svc):
        """Should generate new key, CSR, and sign with CA."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, err = svc.renew_server_cert()
        assert ok is True
        assert err == ""
        # Should call openssl 3 times: ecparam, req, x509
        assert mock_run.call_count >= 3

    @patch("monitor.services.cert_service.subprocess.run")
    def test_renew_logs_audit(self, mock_run, svc):
        """Should log CERT_RENEWED audit event."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="notAfter=May 15 12:00:00 2031 GMT",
            stderr="",
        )
        svc.renew_server_cert()
        svc._audit.log_event.assert_called()
        call_kwargs = svc._audit.log_event.call_args
        assert "CERT_RENEWED" in str(call_kwargs)

    def test_renew_fails_without_ca_key(self, tmp_path):
        """Should fail if CA key doesn't exist."""
        d = tmp_path / "certs"
        d.mkdir()
        (d / "server.crt").write_text("CERT")
        (d / "ca.crt").write_text("CA")
        svc = CertService(certs_dir=str(d))
        ok, err = svc.renew_server_cert()
        assert ok is False
        assert "CA key not found" in err

    def test_renew_fails_without_ca_cert(self, tmp_path):
        """Should fail if CA cert doesn't exist."""
        d = tmp_path / "certs"
        d.mkdir()
        (d / "server.crt").write_text("CERT")
        (d / "ca.key").write_text("KEY")
        svc = CertService(certs_dir=str(d))
        ok, err = svc.renew_server_cert()
        assert ok is False
        assert "CA cert not found" in err

    @patch("monitor.services.cert_service.subprocess.run")
    def test_renew_key_gen_failure(self, mock_run, svc):
        """Should return error if key generation fails."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="key gen error"
        )
        ok, err = svc.renew_server_cert()
        assert ok is False
        assert "Key generation failed" in err


class TestGetCertStatus:
    """Test dashboard status reporting."""

    @patch("monitor.services.cert_service.subprocess.run")
    def test_status_ok(self, mock_run, svc):
        """Should return ok status when cert is valid."""
        future = datetime.now(UTC) + timedelta(days=365)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"notAfter={future.strftime('%b %d %H:%M:%S %Y')} GMT",
            stderr="",
        )
        status = svc.get_cert_status()
        assert status["status"] == "ok"
        assert status["needs_renewal"] is False
        assert status["days_remaining"] > 300

    @patch("monitor.services.cert_service.subprocess.run")
    def test_status_warning(self, mock_run, svc):
        """Should return warning when cert expires soon."""
        soon = datetime.now(UTC) + timedelta(days=15)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"notAfter={soon.strftime('%b %d %H:%M:%S %Y')} GMT",
            stderr="",
        )
        status = svc.get_cert_status()
        assert status["status"] == "warning"
        assert status["needs_renewal"] is True

    def test_status_error(self, tmp_path):
        """Should return error if cert can't be read."""
        svc = CertService(certs_dir=str(tmp_path / "nonexistent"))
        status = svc.get_cert_status()
        assert status["status"] == "error"
        assert status["error"] is not None


class TestDoCheck:
    """Test the _do_check method for background monitoring."""

    @patch("monitor.services.cert_service.subprocess.run")
    def test_logs_warning_near_expiry(self, mock_run, svc):
        """Should log warning and audit when cert expires soon."""
        soon = datetime.now(UTC) + timedelta(days=15)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"notAfter={soon.strftime('%b %d %H:%M:%S %Y')} GMT",
            stderr="",
        )
        svc._do_check()
        svc._audit.log_event.assert_called()
        assert svc._warning_logged is True

    @patch("monitor.services.cert_service.subprocess.run")
    def test_warning_logged_only_once(self, mock_run, svc):
        """Should only log expiry warning once."""
        soon = datetime.now(UTC) + timedelta(days=15)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"notAfter={soon.strftime('%b %d %H:%M:%S %Y')} GMT",
            stderr="",
        )
        svc._do_check()
        svc._do_check()
        # Should only log audit once despite two checks
        assert svc._audit.log_event.call_count == 1

    @patch("monitor.services.cert_service.CertService.renew_server_cert")
    @patch("monitor.services.cert_service.subprocess.run")
    def test_auto_renews_expired_cert(self, mock_run, mock_renew, svc):
        """Should auto-renew when cert is expired."""
        past = datetime.now(UTC) - timedelta(days=5)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"notAfter={past.strftime('%b %d %H:%M:%S %Y')} GMT",
            stderr="",
        )
        mock_renew.return_value = (True, "")
        svc._do_check()
        mock_renew.assert_called_once()


class TestStartStop:
    """Test background thread lifecycle."""

    def test_start_creates_thread(self, svc):
        svc.start()
        assert svc._thread is not None
        assert svc._thread.is_alive()
        svc.stop()

    def test_stop_terminates_thread(self, svc):
        svc.start()
        svc.stop()
        assert not svc._running


class TestAuditFailureResilience:
    """Test that audit failures don't crash the service."""

    @patch("monitor.services.cert_service.subprocess.run")
    def test_audit_error_ignored(self, mock_run, certs_dir):
        """Should not crash if audit logger raises."""
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("audit broken")
        svc = CertService(certs_dir=certs_dir, audit=audit)

        soon = datetime.now(UTC) + timedelta(days=15)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"notAfter={soon.strftime('%b %d %H:%M:%S %Y')} GMT",
            stderr="",
        )
        # Should not raise
        svc._do_check()
