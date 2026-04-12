"""Tests for TailscaleService — Tailscale VPN management."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.tailscale_service import TailscaleService


@pytest.fixture
def svc():
    """Create TailscaleService with mock audit."""
    return TailscaleService(audit=MagicMock())


@pytest.fixture
def svc_with_store():
    """Create TailscaleService with mock store and audit."""
    store = MagicMock()
    return TailscaleService(store=store, audit=MagicMock())


# Sample tailscale status --json output (connected state)
SAMPLE_STATUS_CONNECTED = {
    "BackendState": "Running",
    "Self": {
        "HostName": "rpi-divinu",
        "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
        "ExitNode": False,
        "UserID": 12345,
    },
    "Peer": {
        "node1": {
            "HostName": "my-laptop",
            "TailscaleIPs": ["100.64.0.2"],
            "Online": True,
        },
        "node2": {
            "HostName": "my-phone",
            "TailscaleIPs": ["100.64.0.3"],
            "Online": False,
        },
    },
}

SAMPLE_STATUS_NEEDS_LOGIN = {
    "BackendState": "NeedsLogin",
    "Self": {
        "HostName": "rpi-divinu",
        "TailscaleIPs": [],
        "ExitNode": False,
        "UserID": 0,
    },
    "Peer": {},
}

SAMPLE_STATUS_STOPPED = {
    "BackendState": "Stopped",
    "Self": {
        "HostName": "rpi-divinu",
        "TailscaleIPs": [],
        "ExitNode": False,
        "UserID": 12345,
    },
    "Peer": {},
}


class TestGetStatus:
    """Test Tailscale status retrieval."""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connected_status(self, mock_run, svc):
        """Should return full status when connected."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # version check
            MagicMock(returncode=0, stdout="enabled\n", stderr=""),  # is-enabled
            MagicMock(
                returncode=0,
                stdout=json.dumps(SAMPLE_STATUS_CONNECTED),
                stderr="",
            ),
        ]
        status = svc.get_status()
        assert status["installed"] is True
        assert status["running"] is True
        assert status["state"] == "connected"
        assert status["hostname"] == "rpi-divinu"
        assert status["tailscale_ip"] == "100.64.0.1"
        assert len(status["peers"]) == 2
        assert status["authenticated"] is True
        assert status["daemon_enabled"] is True

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_needs_login_status(self, mock_run, svc):
        """Should report needs-login state."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="enabled\n", stderr=""),
            MagicMock(
                returncode=0,
                stdout=json.dumps(SAMPLE_STATUS_NEEDS_LOGIN),
                stderr="",
            ),
        ]
        status = svc.get_status()
        assert status["state"] == "needs-login"
        assert status["tailscale_ip"] == ""
        assert status["authenticated"] is False

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_stopped_with_auth_history(self, mock_run, svc):
        """Should detect prior authentication in stopped state."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="enabled\n", stderr=""),
            MagicMock(
                returncode=0,
                stdout=json.dumps(SAMPLE_STATUS_STOPPED),
                stderr="",
            ),
        ]
        status = svc.get_status()
        assert status["state"] == "stopped"
        assert status["authenticated"] is True

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_not_installed(self, mock_run, svc):
        """Should return unavailable when binary not found."""
        mock_run.side_effect = FileNotFoundError
        status = svc.get_status()
        assert status["installed"] is False
        assert status["state"] == "unavailable"

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_daemon_not_running(self, mock_run, svc):
        """Should return stopped when daemon isn't running."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # binary exists
            MagicMock(returncode=1, stdout="disabled\n", stderr=""),  # is-enabled
            MagicMock(
                returncode=1,
                stdout="",
                stderr="failed to connect to local tailscaled; it doesn't appear to be running",
            ),
        ]
        status = svc.get_status()
        assert status["installed"] is True
        assert status["running"] is False
        assert status["state"] == "stopped"
        assert status["daemon_enabled"] is False

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_no_peers(self, mock_run, svc):
        """Should return empty peers list when none connected."""
        status_data = {**SAMPLE_STATUS_CONNECTED, "Peer": {}}
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="enabled\n", stderr=""),
            MagicMock(returncode=0, stdout=json.dumps(status_data), stderr=""),
        ]
        status = svc.get_status()
        assert status["peers"] == []

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_timeout_on_version_check(self, mock_run, svc):
        """Should handle timeout gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired("tailscale", 5)
        status = svc.get_status()
        assert status["installed"] is False
        assert status["state"] == "unavailable"


class TestConnect:
    """Test Tailscale connect."""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_already_authenticated(self, mock_run, svc):
        """Should return no auth URL when already connected."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # binary exists
            MagicMock(returncode=0, stdout="", stderr=""),  # tailscale up
        ]
        auth_url, err = svc.connect()
        assert auth_url is None
        assert err == ""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_returns_auth_url(self, mock_run, svc):
        """Should extract auth URL from tailscale up output."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(
                returncode=0,
                stdout="To authenticate, visit:\n\n\thttps://login.tailscale.com/a/abc123def\n",
                stderr="",
            ),
        ]
        auth_url, err = svc.connect()
        assert auth_url == "https://login.tailscale.com/a/abc123def"
        assert err == ""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_auth_url_in_stderr(self, mock_run, svc):
        """Should also check stderr for auth URL."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(
                returncode=0,
                stdout="",
                stderr="To authenticate, visit:\n\n\thttps://login.tailscale.com/a/xyz789\n",
            ),
        ]
        auth_url, err = svc.connect()
        assert auth_url == "https://login.tailscale.com/a/xyz789"

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_with_flags(self, mock_run, svc):
        """Should pass accept-routes, ssh, and authkey flags."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        svc.connect(accept_routes=True, ssh=True, authkey="tskey-auth-test")
        # Second call is tailscale up
        call_args = mock_run.call_args_list[1][0][0]
        assert "--accept-routes" in call_args
        assert "--ssh" in call_args
        assert "--authkey=tskey-auth-test" in call_args

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_without_flags(self, mock_run, svc):
        """Should not include flags when not set."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        svc.connect()
        call_args = mock_run.call_args_list[1][0][0]
        assert call_args == ["tailscale", "up", "--timeout=5s"]

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_not_installed(self, mock_run, svc):
        """Should return error when binary not found."""
        mock_run.side_effect = FileNotFoundError
        auth_url, err = svc.connect()
        assert auth_url is None
        assert "not installed" in err

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_timeout(self, mock_run, svc):
        """Should handle timeout."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            subprocess.TimeoutExpired("tailscale", 30),
        ]
        auth_url, err = svc.connect()
        assert auth_url is None
        assert "timed out" in err.lower()

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_logs_audit_on_auth_needed(self, mock_run, svc):
        """Should log audit event when auth URL is generated."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(
                returncode=0,
                stdout="https://login.tailscale.com/a/test123\n",
                stderr="",
            ),
        ]
        svc.connect()
        calls = [str(c) for c in svc._audit.log_event.call_args_list]
        assert any("TAILSCALE_AUTH_NEEDED" in c for c in calls)

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_connect_logs_audit_on_success(self, mock_run, svc):
        """Should log audit when connected without auth."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        svc.connect()
        calls = [str(c) for c in svc._audit.log_event.call_args_list]
        assert any("TAILSCALE_CONNECTED" in c for c in calls)


class TestDisconnect:
    """Test Tailscale disconnect."""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_disconnect_success(self, mock_run, svc):
        """Should disconnect successfully."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # binary exists
            MagicMock(returncode=0, stdout="", stderr=""),  # tailscale down
        ]
        ok, err = svc.disconnect()
        assert ok is True
        assert err == ""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_disconnect_failure(self, mock_run, svc):
        """Should return error on failure."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1, stdout="", stderr="not connected"),
        ]
        ok, err = svc.disconnect()
        assert ok is False
        assert "not connected" in err

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_disconnect_not_installed(self, mock_run, svc):
        """Should return error when binary not found."""
        mock_run.side_effect = FileNotFoundError
        ok, err = svc.disconnect()
        assert ok is False
        assert "not installed" in err

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_disconnect_logs_audit(self, mock_run, svc):
        """Should log audit on disconnect."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        svc.disconnect()
        calls = [str(c) for c in svc._audit.log_event.call_args_list]
        assert any("TAILSCALE_DISCONNECTED" in c for c in calls)


class TestEnable:
    """Test enabling tailscaled daemon."""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_enable_success(self, mock_run, svc):
        """Should enable and start daemon."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, err = svc.enable()
        assert ok is True
        assert err == ""
        cmd = mock_run.call_args[0][0]
        assert cmd == ["systemctl", "enable", "--now", "tailscaled"]

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_enable_failure(self, mock_run, svc):
        """Should return error on failure."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="permission denied"
        )
        ok, err = svc.enable()
        assert ok is False
        assert "permission denied" in err

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_enable_logs_audit(self, mock_run, svc):
        """Should log audit event."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        svc.enable()
        calls = [str(c) for c in svc._audit.log_event.call_args_list]
        assert any("TAILSCALE_ENABLED" in c for c in calls)


class TestDisable:
    """Test disabling tailscaled daemon."""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_disable_success(self, mock_run, svc):
        """Should disconnect then disable daemon."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # binary exists
            MagicMock(returncode=0, stdout="", stderr=""),  # tailscale down
            MagicMock(returncode=0, stdout="", stderr=""),  # systemctl disable
        ]
        ok, err = svc.disable()
        assert ok is True
        assert err == ""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_disable_graceful_even_if_down_fails(self, mock_run, svc):
        """Should still disable daemon even if tailscale down fails."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # binary exists
            MagicMock(returncode=1, stdout="", stderr="not connected"),  # down fails
            MagicMock(returncode=0, stdout="", stderr=""),  # disable succeeds
        ]
        ok, err = svc.disable()
        assert ok is True

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_disable_logs_audit(self, mock_run, svc):
        """Should log audit event."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        svc.disable()
        calls = [str(c) for c in svc._audit.log_event.call_args_list]
        assert any("TAILSCALE_DISABLED" in c for c in calls)


class TestApplyConfig:
    """Test config-driven Tailscale management."""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_disable_when_not_enabled(self, mock_run, svc_with_store):
        """Should disable daemon when tailscale_enabled is False."""
        settings = MagicMock()
        settings.tailscale_enabled = False
        svc_with_store._store.get_settings.return_value = settings

        # disable calls: binary_exists, tailscale down, systemctl disable
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        auth_url, err = svc_with_store.apply_config()
        assert auth_url is None
        assert err == ""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_enable_and_connect_with_flags(self, mock_run, svc_with_store):
        """Should enable daemon and connect with saved flags."""
        settings = MagicMock()
        settings.tailscale_enabled = True
        settings.tailscale_auto_connect = True
        settings.tailscale_accept_routes = True
        settings.tailscale_ssh = True
        settings.tailscale_auth_key = "tskey-auth-test"
        svc_with_store._store.get_settings.return_value = settings

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # enable
            MagicMock(returncode=0),  # binary exists
            MagicMock(returncode=0, stdout="", stderr=""),  # tailscale up
        ]

        auth_url, err = svc_with_store.apply_config()
        assert err == ""
        # Verify tailscale up was called with flags
        up_call = mock_run.call_args_list[2][0][0]
        assert "--accept-routes" in up_call
        assert "--ssh" in up_call
        assert "--authkey=tskey-auth-test" in up_call

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_enable_without_auto_connect(self, mock_run, svc_with_store):
        """Should enable daemon but not connect when auto_connect is off."""
        settings = MagicMock()
        settings.tailscale_enabled = True
        settings.tailscale_auto_connect = False
        svc_with_store._store.get_settings.return_value = settings

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        auth_url, err = svc_with_store.apply_config()
        assert auth_url is None
        assert err == ""
        # Should have called systemctl enable but NOT tailscale up
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["systemctl", "enable", "--now", "tailscaled"] in cmds
        assert ["tailscale", "up"] not in cmds

    def test_no_store_returns_error(self):
        """Should return error when no store configured."""
        svc = TailscaleService(store=None)
        auth_url, err = svc.apply_config()
        assert "No store" in err


class TestExtractAuthUrl:
    """Test auth URL extraction from CLI output."""

    def test_standard_format(self):
        text = "To authenticate, visit:\n\n\thttps://login.tailscale.com/a/abc123\n"
        assert (
            TailscaleService._extract_auth_url(text)
            == "https://login.tailscale.com/a/abc123"
        )

    def test_no_url(self):
        assert TailscaleService._extract_auth_url("some other output") is None

    def test_empty(self):
        assert TailscaleService._extract_auth_url("") is None

    def test_url_with_long_code(self):
        text = "https://login.tailscale.com/a/16cc78fe01ed46abcdef"
        assert TailscaleService._extract_auth_url(text) == text


class TestAuditResilience:
    """Test that audit failures don't crash the service."""

    @patch("monitor.services.tailscale_service.subprocess.run")
    def test_audit_error_ignored(self, mock_run):
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("audit broken")
        svc = TailscaleService(audit=audit)

        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        # Should not raise despite audit failure
        ok, err = svc.disconnect()
        assert ok is True

    def test_no_audit_logger(self):
        """Should work fine without audit logger."""
        svc = TailscaleService(audit=None)
        svc._log_audit("TEST", "test")  # Should not raise
