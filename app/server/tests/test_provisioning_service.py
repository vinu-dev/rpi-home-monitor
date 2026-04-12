"""
Tests for ProvisioningService — first-boot setup wizard logic.

Unit tests target the service directly (no Flask app).
All subprocess calls are mocked. Uses tmp_path for data_dir.
"""

import os
import subprocess
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

SUBPROCESS_PATCH = "monitor.services.provisioning_service.subprocess"
TIMER_PATCH = "monitor.services.provisioning_service.threading.Timer"


def _fix(mock_sub):
    """Preserve real exception classes on mocked subprocess module.

    When the entire subprocess module is replaced by a MagicMock,
    ``except subprocess.TimeoutExpired`` in production code fails
    because a MagicMock is not a valid exception type.  Reassigning
    the real classes on the mock fixes this.
    """
    mock_sub.TimeoutExpired = subprocess.TimeoutExpired
    mock_sub.SubprocessError = subprocess.SubprocessError


@dataclass
class FakeUser:
    """Minimal user stand-in for store mocking."""

    id: str = "u1"
    username: str = "admin"
    password_hash: str = "oldhash"
    role: str = "admin"


@pytest.fixture()
def store():
    """Mock store with get_user_by_username and save_user."""
    s = MagicMock()
    s.get_user_by_username.return_value = FakeUser()
    return s


@pytest.fixture()
def svc(store, tmp_path):
    """ProvisioningService wired to mock store and tmp_path."""
    from monitor.services.provisioning_service import ProvisioningService

    return ProvisioningService(store=store, data_dir=str(tmp_path))


# ── is_setup_complete ────────────────────────────────────────────────


class TestIsSetupComplete:
    def test_false_when_no_stamp(self, svc):
        assert svc.is_setup_complete() is False

    def test_true_when_stamp_exists(self, svc, tmp_path):
        (tmp_path / ".setup-done").write_text("done")
        assert svc.is_setup_complete() is True

    def test_setup_done_path_uses_data_dir(self, svc, tmp_path):
        assert svc.setup_done_path == os.path.join(str(tmp_path), ".setup-done")


# ── is_hotspot_active ────────────────────────────────────────────────


class TestIsHotspotActive:
    @patch(SUBPROCESS_PATCH)
    def test_active_when_script_returns_zero(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=0)
        assert svc.is_hotspot_active() is True
        mock_sub.run.assert_called_once()
        args = mock_sub.run.call_args
        assert args[0][0][1] == "status"

    @patch(SUBPROCESS_PATCH)
    def test_inactive_when_script_returns_nonzero(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=1)
        assert svc.is_hotspot_active() is False

    @patch(SUBPROCESS_PATCH)
    def test_inactive_on_timeout(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=10)
        assert svc.is_hotspot_active() is False

    @patch(SUBPROCESS_PATCH)
    def test_inactive_on_file_not_found(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = FileNotFoundError("no script")
        assert svc.is_hotspot_active() is False

    @patch(SUBPROCESS_PATCH)
    def test_inactive_on_os_error(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = OSError("boom")
        assert svc.is_hotspot_active() is False


# ── get_status ───────────────────────────────────────────────────────


class TestGetStatus:
    @patch(SUBPROCESS_PATCH)
    def test_returns_both_fields(self, mock_sub, svc, tmp_path):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=0)
        status = svc.get_status()
        assert status["setup_complete"] is False
        assert status["hotspot_active"] is True

    @patch(SUBPROCESS_PATCH)
    def test_setup_complete_reflected(self, mock_sub, svc, tmp_path):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=1)
        (tmp_path / ".setup-done").write_text("done")
        status = svc.get_status()
        assert status["setup_complete"] is True
        assert status["hotspot_active"] is False


# ── scan_wifi ────────────────────────────────────────────────────────


class TestScanWifi:
    def test_blocked_after_setup_complete(self, svc, tmp_path):
        (tmp_path / ".setup-done").write_text("done")
        networks, err, code = svc.scan_wifi()
        assert code == 403
        assert networks == []
        assert "already completed" in err

    @patch(SUBPROCESS_PATCH)
    def test_success_parses_networks(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout="HomeWiFi:90:WPA2\nGuest:50:WPA1\nHomeWiFi:70:WPA2\n",
            stderr="",
        )
        networks, err, code = svc.scan_wifi()
        assert code == 200
        assert err == ""
        # Deduplication: HomeWiFi appears once with strongest signal
        assert len(networks) == 2
        assert networks[0]["ssid"] == "HomeWiFi"
        assert networks[0]["signal"] == 90
        assert networks[1]["ssid"] == "Guest"

    @patch(SUBPROCESS_PATCH)
    def test_success_empty_scan(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        networks, err, code = svc.scan_wifi()
        assert code == 200
        assert networks == []

    @patch(SUBPROCESS_PATCH)
    def test_skips_blank_ssid_lines(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout=":80:WPA2\nValid:60:WPA2\n",
            stderr="",
        )
        networks, _, code = svc.scan_wifi()
        assert code == 200
        assert len(networks) == 1
        assert networks[0]["ssid"] == "Valid"

    @patch(SUBPROCESS_PATCH)
    def test_handles_bad_signal_value(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout="Net:bad:WPA2\n",
            stderr="",
        )
        networks, _, code = svc.scan_wifi()
        assert code == 200
        assert networks[0]["signal"] == 0

    @patch(SUBPROCESS_PATCH)
    def test_skips_short_lines(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout="OnlyOneField\nValid:80:WPA2\n",
            stderr="",
        )
        networks, _, code = svc.scan_wifi()
        assert code == 200
        assert len(networks) == 1

    @patch(SUBPROCESS_PATCH)
    def test_timeout_returns_504(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=30)
        networks, err, code = svc.scan_wifi()
        assert code == 504
        assert "timed out" in err
        assert networks == []

    @patch(SUBPROCESS_PATCH)
    def test_file_not_found_returns_500(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = FileNotFoundError("no nmcli")
        networks, err, code = svc.scan_wifi()
        assert code == 500
        assert "failed" in err.lower()

    @patch(SUBPROCESS_PATCH)
    def test_os_error_returns_500(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = OSError("permission denied")
        networks, err, code = svc.scan_wifi()
        assert code == 500

    @patch(SUBPROCESS_PATCH)
    def test_nonzero_returncode_returns_500(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=1, stdout="", stderr="device not ready"
        )
        networks, err, code = svc.scan_wifi()
        assert code == 500
        assert "device not ready" in err


# ── save_wifi_credentials ────────────────────────────────────────────


class TestSaveWifiCredentials:
    def test_blocked_after_setup_complete(self, svc, tmp_path):
        (tmp_path / ".setup-done").write_text("done")
        msg, code = svc.save_wifi_credentials("Net", "pass1234")
        assert code == 403

    def test_missing_ssid(self, svc):
        msg, code = svc.save_wifi_credentials("", "pass1234")
        assert code == 400
        assert "SSID" in msg

    def test_whitespace_only_ssid(self, svc):
        msg, code = svc.save_wifi_credentials("   ", "pass1234")
        assert code == 400

    def test_missing_password(self, svc):
        msg, code = svc.save_wifi_credentials("Net", "")
        assert code == 400
        assert "Password" in msg

    def test_whitespace_only_password(self, svc):
        msg, code = svc.save_wifi_credentials("Net", "   ")
        assert code == 400

    def test_success_stores_credentials(self, svc):
        msg, code = svc.save_wifi_credentials("MyWiFi", "secret123")
        assert code == 200
        assert "MyWiFi" in msg
        assert svc._pending_wifi["ssid"] == "MyWiFi"
        assert svc._pending_wifi["password"] == "secret123"

    def test_strips_whitespace(self, svc):
        svc.save_wifi_credentials("  MyWiFi  ", "  secret  ")
        assert svc._pending_wifi["ssid"] == "MyWiFi"
        assert svc._pending_wifi["password"] == "secret"

    def test_overwrite_previous_credentials(self, svc):
        svc.save_wifi_credentials("First", "pass1111")
        svc.save_wifi_credentials("Second", "pass2222")
        assert svc._pending_wifi["ssid"] == "Second"
        assert svc._pending_wifi["password"] == "pass2222"


# ── set_admin_password ───────────────────────────────────────────────


class TestSetAdminPassword:
    def test_blocked_after_setup_complete(self, svc, tmp_path):
        (tmp_path / ".setup-done").write_text("done")
        msg, code = svc.set_admin_password("longenough12")
        assert code == 403

    def test_too_short_password(self, svc):
        msg, code = svc.set_admin_password("short")
        assert code == 400
        assert "12 characters" in msg

    def test_exactly_12_chars_accepted(self, svc, store):
        msg, code = svc.set_admin_password("a" * 12)
        assert code == 200
        store.save_user.assert_called_once()

    def test_admin_not_found(self, svc, store):
        store.get_user_by_username.return_value = None
        msg, code = svc.set_admin_password("longenough12")
        assert code == 500
        assert "not found" in msg

    def test_password_hashed_and_saved(self, svc, store):
        with patch("monitor.auth.hash_password", return_value="newhash") as mh:
            msg, code = svc.set_admin_password("securepassword")
        assert code == 200
        mh.assert_called_once_with("securepassword")
        saved_user = store.save_user.call_args[0][0]
        assert saved_user.password_hash == "newhash"


# ── _connect_wifi ────────────────────────────────────────────────────


class TestConnectWifi:
    @patch(SUBPROCESS_PATCH)
    def test_success(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=0)
        ok, err = svc._connect_wifi("Net", "pass")
        assert ok is True
        assert err == ""

    @patch(SUBPROCESS_PATCH)
    def test_calls_nmcli_with_correct_args(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=0)
        svc._connect_wifi("MySSID", "MyPass")
        args = mock_sub.run.call_args[0][0]
        assert "nmcli" in args
        assert "MySSID" in args
        assert "MyPass" in args
        assert "wlan0" in args

    @patch(SUBPROCESS_PATCH)
    def test_timeout(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=30)
        ok, err = svc._connect_wifi("Net", "pass")
        assert ok is False
        assert "timed out" in err

    @patch(SUBPROCESS_PATCH)
    def test_file_not_found(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = FileNotFoundError("no nmcli")
        ok, err = svc._connect_wifi("Net", "pass")
        assert ok is False
        assert "failed" in err.lower()

    @patch(SUBPROCESS_PATCH)
    def test_os_error(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = OSError("nope")
        ok, err = svc._connect_wifi("Net", "pass")
        assert ok is False

    @patch(SUBPROCESS_PATCH)
    def test_wrong_password_message(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=1, stderr="Secrets were required but not provided"
        )
        ok, err = svc._connect_wifi("Net", "wrong")
        assert ok is False
        assert "Incorrect" in err

    @patch(SUBPROCESS_PATCH)
    def test_no_suitable_network_message(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=1, stderr="No suitable network found"
        )
        ok, err = svc._connect_wifi("Net", "pass")
        assert ok is False
        assert "Incorrect" in err

    @patch(SUBPROCESS_PATCH)
    def test_generic_failure(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=1, stderr="Some other error")
        ok, err = svc._connect_wifi("Net", "pass")
        assert ok is False
        assert "Some other error" in err


# ── _get_wifi_ip ─────────────────────────────────────────────────────


class TestGetWifiIp:
    @patch(SUBPROCESS_PATCH)
    def test_parses_ip_with_cidr(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout="IP4.ADDRESS[1]:192.168.1.50/24\n",
        )
        assert svc._get_wifi_ip() == "192.168.1.50"

    @patch(SUBPROCESS_PATCH)
    def test_parses_ip_without_cidr(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout="IP4.ADDRESS[1]:10.0.0.5\n",
        )
        assert svc._get_wifi_ip() == "10.0.0.5"

    @patch(SUBPROCESS_PATCH)
    def test_returns_empty_on_no_output(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="")
        assert svc._get_wifi_ip() == ""

    @patch(SUBPROCESS_PATCH)
    def test_returns_empty_on_nonzero_returncode(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(returncode=1, stdout="")
        assert svc._get_wifi_ip() == ""

    @patch(SUBPROCESS_PATCH)
    def test_returns_empty_on_timeout(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=10)
        assert svc._get_wifi_ip() == ""

    @patch(SUBPROCESS_PATCH)
    def test_returns_empty_on_os_error(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.side_effect = OSError("bad")
        assert svc._get_wifi_ip() == ""

    @patch(SUBPROCESS_PATCH)
    def test_skips_lines_without_colon(self, mock_sub, svc):
        _fix(mock_sub)
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout="no-colon-here\nIP4.ADDRESS[1]:172.16.0.1/16\n",
        )
        assert svc._get_wifi_ip() == "172.16.0.1"


# ── _write_stamp_file ───────────────────────────────────────────────


class TestWriteStampFile:
    def test_creates_stamp_file(self, svc, tmp_path):
        err = svc._write_stamp_file()
        assert err == ""
        stamp = tmp_path / ".setup-done"
        assert stamp.exists()
        assert "setup completed" in stamp.read_text()

    def test_creates_parent_dirs(self, store):
        import tempfile

        from monitor.services.provisioning_service import ProvisioningService

        with tempfile.TemporaryDirectory() as td:
            deep = os.path.join(td, "nested", "deep")
            s = ProvisioningService(store=store, data_dir=deep)
            err = s._write_stamp_file()
            assert err == ""
            assert os.path.exists(os.path.join(deep, ".setup-done"))

    def test_returns_error_on_write_failure(self, svc):
        with patch("builtins.open", side_effect=OSError("read only")):
            err = svc._write_stamp_file()
        assert "Failed" in err


# ── _schedule_hotspot_stop ───────────────────────────────────────────


class TestScheduleHotspotStop:
    @patch(TIMER_PATCH)
    def test_creates_daemon_timer(self, mock_timer_cls, svc):
        timer_inst = MagicMock()
        mock_timer_cls.return_value = timer_inst
        svc._schedule_hotspot_stop()
        mock_timer_cls.assert_called_once()
        args = mock_timer_cls.call_args
        assert args[0][0] == 15.0  # delay seconds
        assert timer_inst.daemon is True
        timer_inst.start.assert_called_once()

    @patch(TIMER_PATCH)
    def test_callback_calls_hotspot_stop(self, mock_timer_cls, svc):
        """Verify the Timer callback invokes the hotspot script with 'stop'."""
        mock_timer_cls.return_value = MagicMock()
        svc._schedule_hotspot_stop()
        callback = mock_timer_cls.call_args[0][1]

        with patch(SUBPROCESS_PATCH) as mock_sub:
            _fix(mock_sub)
            mock_sub.run.return_value = MagicMock(returncode=0, stdout="stopped")
            callback()
            args = mock_sub.run.call_args[0][0]
            assert "stop" in args

    @patch(TIMER_PATCH)
    def test_callback_handles_timeout(self, mock_timer_cls, svc):
        mock_timer_cls.return_value = MagicMock()
        svc._schedule_hotspot_stop()
        callback = mock_timer_cls.call_args[0][1]

        with patch(SUBPROCESS_PATCH) as mock_sub:
            _fix(mock_sub)
            mock_sub.run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=30)
            callback()  # should not raise

    @patch(TIMER_PATCH)
    def test_callback_handles_file_not_found(self, mock_timer_cls, svc):
        mock_timer_cls.return_value = MagicMock()
        svc._schedule_hotspot_stop()
        callback = mock_timer_cls.call_args[0][1]

        with patch(SUBPROCESS_PATCH) as mock_sub:
            _fix(mock_sub)
            mock_sub.run.side_effect = FileNotFoundError("gone")
            callback()  # should not raise


# ── complete_setup ───────────────────────────────────────────────────


class TestCompleteSetup:
    def test_blocked_after_setup_complete(self, svc, tmp_path):
        (tmp_path / ".setup-done").write_text("done")
        result, err, code = svc.complete_setup()
        assert code == 403
        assert result is None

    def test_no_wifi_credentials_returns_400(self, svc):
        result, err, code = svc.complete_setup()
        assert code == 400
        assert "WiFi credentials" in err
        assert result is None

    def test_empty_ssid_returns_400(self, svc):
        svc._pending_wifi["ssid"] = ""
        svc._pending_wifi["password"] = "secret"
        _, err, code = svc.complete_setup()
        assert code == 400

    def test_empty_password_returns_400(self, svc):
        svc._pending_wifi["ssid"] = "Net"
        svc._pending_wifi["password"] = ""
        _, err, code = svc.complete_setup()
        assert code == 400

    @patch(TIMER_PATCH)
    @patch(SUBPROCESS_PATCH)
    @patch("monitor.services.provisioning_service.socket")
    def test_success_full_flow(self, mock_socket, mock_sub, mock_timer, svc, tmp_path):
        """Full happy path: connect WiFi, get IP, write stamp, schedule stop."""
        _fix(mock_sub)
        svc._pending_wifi["ssid"] = "HomeNet"
        svc._pending_wifi["password"] = "secret123"

        mock_sub.run.side_effect = [
            MagicMock(returncode=0),  # _connect_wifi
            MagicMock(returncode=0, stdout="IP4.ADDRESS[1]:192.168.1.100/24\n"),
        ]
        mock_socket.gethostname.return_value = "homemonitor"
        mock_timer.return_value = MagicMock()

        result, err, code = svc.complete_setup()
        assert code == 200
        assert err == ""
        assert result["ip"] == "192.168.1.100"
        assert result["hostname"] == "homemonitor.local"

        # Stamp file written
        assert (tmp_path / ".setup-done").exists()

        # Credentials cleared
        assert svc._pending_wifi["ssid"] == ""
        assert svc._pending_wifi["password"] == ""

        # Timer started
        mock_timer.return_value.start.assert_called_once()

    @patch(SUBPROCESS_PATCH)
    def test_wifi_connect_failure_returns_500(self, mock_sub, svc):
        _fix(mock_sub)
        svc._pending_wifi["ssid"] = "Net"
        svc._pending_wifi["password"] = "pass"
        mock_sub.run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=30)

        result, err, code = svc.complete_setup()
        assert code == 500
        assert result is None
        assert "timed out" in err

    @patch(SUBPROCESS_PATCH)
    def test_wifi_connect_wrong_password(self, mock_sub, svc):
        _fix(mock_sub)
        svc._pending_wifi["ssid"] = "Net"
        svc._pending_wifi["password"] = "wrong"
        mock_sub.run.return_value = MagicMock(
            returncode=1, stderr="Secrets were required"
        )
        result, err, code = svc.complete_setup()
        assert code == 500
        assert "Incorrect" in err

    @patch(TIMER_PATCH)
    @patch(SUBPROCESS_PATCH)
    def test_stamp_write_failure_returns_500(self, mock_sub, mock_timer, svc):
        _fix(mock_sub)
        svc._pending_wifi["ssid"] = "Net"
        svc._pending_wifi["password"] = "pass"

        mock_sub.run.side_effect = [
            MagicMock(returncode=0),  # _connect_wifi
            MagicMock(returncode=0, stdout=""),  # _get_wifi_ip
        ]

        with patch.object(svc, "_write_stamp_file", return_value="Disk error"):
            result, err, code = svc.complete_setup()
        assert code == 500
        assert "Disk error" in err

    @patch(TIMER_PATCH)
    @patch(SUBPROCESS_PATCH)
    @patch("monitor.services.provisioning_service.socket")
    def test_credentials_cleared_on_success(
        self, mock_socket, mock_sub, mock_timer, svc
    ):
        _fix(mock_sub)
        svc._pending_wifi["ssid"] = "Net"
        svc._pending_wifi["password"] = "pass"
        mock_sub.run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout=""),
        ]
        mock_socket.gethostname.return_value = "host"
        mock_timer.return_value = MagicMock()

        svc.complete_setup()
        assert svc._pending_wifi["ssid"] == ""
        assert svc._pending_wifi["password"] == ""

    @patch(SUBPROCESS_PATCH)
    def test_credentials_not_cleared_on_wifi_failure(self, mock_sub, svc):
        _fix(mock_sub)
        svc._pending_wifi["ssid"] = "Net"
        svc._pending_wifi["password"] = "pass"
        mock_sub.run.return_value = MagicMock(returncode=1, stderr="generic fail")
        svc.complete_setup()
        # Credentials should still be present for retry
        assert svc._pending_wifi["ssid"] == "Net"
        assert svc._pending_wifi["password"] == "pass"

    @patch(TIMER_PATCH)
    @patch(SUBPROCESS_PATCH)
    @patch("monitor.services.provisioning_service.socket")
    def test_empty_ip_still_succeeds(
        self, mock_socket, mock_sub, mock_timer, svc, tmp_path
    ):
        """If IP lookup returns nothing, setup still completes."""
        _fix(mock_sub)
        svc._pending_wifi["ssid"] = "Net"
        svc._pending_wifi["password"] = "pass"
        mock_sub.run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1, stdout=""),  # IP lookup fails
        ]
        mock_socket.gethostname.return_value = "host"
        mock_timer.return_value = MagicMock()

        result, err, code = svc.complete_setup()
        assert code == 200
        assert result["ip"] == ""
