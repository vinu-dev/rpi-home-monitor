"""Tests for the settings service."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from monitor.services.settings_service import UPDATABLE_FIELDS, SettingsService


def _make_settings(**overrides):
    """Create a fake settings object with sensible defaults."""
    defaults = {
        "timezone": "Europe/Dublin",
        "storage_threshold_percent": 90,
        "clip_duration_seconds": 180,
        "session_timeout_minutes": 30,
        "hostname": "homemonitor",
        "setup_completed": False,
        "firmware_version": "1.0.0",
        "tailscale_enabled": False,
        "tailscale_auto_connect": False,
        "tailscale_accept_routes": False,
        "tailscale_ssh": False,
        "tailscale_auth_key": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(settings=None, audit=None):
    """Create a SettingsService with mocked store and optional audit."""
    store = MagicMock()
    store.get_settings.return_value = settings or _make_settings()
    return SettingsService(store, audit=audit), store


# ---- get_settings ----


class TestGetSettings:
    """Test retrieving current settings."""

    def test_returns_all_fields(self):
        svc, _ = _make_service()
        result = svc.get_settings()
        assert result["timezone"] == "Europe/Dublin"
        assert result["storage_threshold_percent"] == 90
        assert result["clip_duration_seconds"] == 180
        assert result["session_timeout_minutes"] == 30
        assert result["hostname"] == "homemonitor"
        assert result["setup_completed"] is False
        assert result["firmware_version"] == "1.0.0"

    def test_returns_dict(self):
        svc, _ = _make_service()
        result = svc.get_settings()
        assert isinstance(result, dict)
        assert len(result) == 12

    def test_reflects_custom_values(self):
        settings = _make_settings(timezone="US/Pacific", hostname="mybox")
        svc, _ = _make_service(settings=settings)
        result = svc.get_settings()
        assert result["timezone"] == "US/Pacific"
        assert result["hostname"] == "mybox"


# ---- update_settings ----


class TestUpdateSettings:
    """Test updating system settings."""

    def test_update_single_field(self):
        svc, store = _make_service()
        msg, code = svc.update_settings({"timezone": "US/Eastern"}, "admin", "1.2.3.4")
        assert code == 200
        assert msg == "Settings updated"
        store.save_settings.assert_called_once()

    def test_update_multiple_fields(self):
        svc, store = _make_service()
        data = {"timezone": "US/Eastern", "hostname": "newhost"}
        msg, code = svc.update_settings(data, "admin", "1.2.3.4")
        assert code == 200
        store.save_settings.assert_called_once()

    def test_empty_data_returns_400(self):
        svc, store = _make_service()
        msg, code = svc.update_settings({}, "admin", "1.2.3.4")
        assert code == 400
        assert "No updatable fields" in msg
        store.save_settings.assert_not_called()

    def test_none_data_returns_400(self):
        svc, store = _make_service()
        msg, code = svc.update_settings(None, "admin", "1.2.3.4")
        assert code == 400
        store.save_settings.assert_not_called()

    def test_unknown_field_returns_400(self):
        svc, store = _make_service()
        msg, code = svc.update_settings({"bogus": 42}, "admin", "1.2.3.4")
        assert code == 400
        assert "Unknown fields" in msg
        assert "bogus" in msg
        store.save_settings.assert_not_called()

    def test_mix_known_and_unknown_returns_400(self):
        svc, store = _make_service()
        data = {"timezone": "US/Eastern", "unknown_field": "x"}
        msg, code = svc.update_settings(data, "admin", "1.2.3.4")
        assert code == 400
        assert "unknown_field" in msg
        store.save_settings.assert_not_called()

    def test_validation_error_returns_400(self):
        svc, store = _make_service()
        msg, code = svc.update_settings(
            {"storage_threshold_percent": 10}, "admin", "1.2.3.4"
        )
        assert code == 400
        assert "storage_threshold_percent" in msg
        store.save_settings.assert_not_called()

    def test_setattr_applied_to_settings_object(self):
        settings = _make_settings()
        store = MagicMock()
        store.get_settings.return_value = settings
        svc = SettingsService(store)

        svc.update_settings({"hostname": "newname"}, "admin", "1.2.3.4")
        assert settings.hostname == "newname"

    def test_audit_logged_on_success(self):
        audit = MagicMock()
        svc, _ = _make_service(audit=audit)
        svc.update_settings({"timezone": "US/Eastern"}, "admin", "1.2.3.4")
        audit.log_event.assert_called_once_with(
            "SETTINGS_UPDATED",
            user="admin",
            ip="1.2.3.4",
            detail="updated: timezone",
        )

    def test_audit_detail_lists_sorted_fields(self):
        audit = MagicMock()
        svc, _ = _make_service(audit=audit)
        data = {"timezone": "US/Eastern", "hostname": "box"}
        svc.update_settings(data, "admin", "1.2.3.4")
        call_kwargs = audit.log_event.call_args
        assert "hostname, timezone" in call_kwargs.kwargs["detail"]

    def test_no_audit_when_audit_is_none(self):
        svc, _ = _make_service(audit=None)
        # Should not raise
        msg, code = svc.update_settings({"timezone": "US/Eastern"}, "admin", "1.2.3.4")
        assert code == 200


# ---- _validate ----


class TestValidate:
    """Test field validation logic."""

    # storage_threshold_percent
    def test_storage_threshold_valid_boundary_low(self):
        svc, _ = _make_service()
        assert svc._validate({"storage_threshold_percent": 50}) == []

    def test_storage_threshold_valid_boundary_high(self):
        svc, _ = _make_service()
        assert svc._validate({"storage_threshold_percent": 99}) == []

    def test_storage_threshold_too_low(self):
        svc, _ = _make_service()
        errors = svc._validate({"storage_threshold_percent": 49})
        assert len(errors) == 1
        assert "storage_threshold_percent" in errors[0]

    def test_storage_threshold_too_high(self):
        svc, _ = _make_service()
        errors = svc._validate({"storage_threshold_percent": 100})
        assert len(errors) == 1

    def test_storage_threshold_not_int(self):
        svc, _ = _make_service()
        errors = svc._validate({"storage_threshold_percent": 90.5})
        assert len(errors) == 1

    def test_storage_threshold_string_rejected(self):
        svc, _ = _make_service()
        errors = svc._validate({"storage_threshold_percent": "90"})
        assert len(errors) == 1

    # clip_duration_seconds
    def test_clip_duration_valid_boundary_low(self):
        svc, _ = _make_service()
        assert svc._validate({"clip_duration_seconds": 30}) == []

    def test_clip_duration_valid_boundary_high(self):
        svc, _ = _make_service()
        assert svc._validate({"clip_duration_seconds": 600}) == []

    def test_clip_duration_too_low(self):
        svc, _ = _make_service()
        errors = svc._validate({"clip_duration_seconds": 29})
        assert len(errors) == 1

    def test_clip_duration_too_high(self):
        svc, _ = _make_service()
        errors = svc._validate({"clip_duration_seconds": 601})
        assert len(errors) == 1

    def test_clip_duration_not_int(self):
        svc, _ = _make_service()
        errors = svc._validate({"clip_duration_seconds": "180"})
        assert len(errors) == 1

    # session_timeout_minutes
    def test_session_timeout_valid_boundary_low(self):
        svc, _ = _make_service()
        assert svc._validate({"session_timeout_minutes": 5}) == []

    def test_session_timeout_valid_boundary_high(self):
        svc, _ = _make_service()
        assert svc._validate({"session_timeout_minutes": 1440}) == []

    def test_session_timeout_too_low(self):
        svc, _ = _make_service()
        errors = svc._validate({"session_timeout_minutes": 4})
        assert len(errors) == 1

    def test_session_timeout_too_high(self):
        svc, _ = _make_service()
        errors = svc._validate({"session_timeout_minutes": 1441})
        assert len(errors) == 1

    # hostname
    def test_hostname_valid(self):
        svc, _ = _make_service()
        assert svc._validate({"hostname": "myhost"}) == []

    def test_hostname_single_char_valid(self):
        svc, _ = _make_service()
        assert svc._validate({"hostname": "x"}) == []

    def test_hostname_max_length_valid(self):
        svc, _ = _make_service()
        assert svc._validate({"hostname": "a" * 63}) == []

    def test_hostname_too_long(self):
        svc, _ = _make_service()
        errors = svc._validate({"hostname": "a" * 64})
        assert len(errors) == 1

    def test_hostname_empty_string(self):
        svc, _ = _make_service()
        errors = svc._validate({"hostname": ""})
        assert len(errors) == 1

    def test_hostname_not_string(self):
        svc, _ = _make_service()
        errors = svc._validate({"hostname": 123})
        assert len(errors) == 1

    # timezone
    def test_timezone_valid(self):
        svc, _ = _make_service()
        assert svc._validate({"timezone": "Europe/Dublin"}) == []

    def test_timezone_must_have_slash(self):
        svc, _ = _make_service()
        errors = svc._validate({"timezone": "UTC"})
        assert len(errors) == 1
        assert "timezone" in errors[0]

    def test_timezone_empty_string(self):
        svc, _ = _make_service()
        errors = svc._validate({"timezone": ""})
        assert len(errors) == 1

    def test_timezone_not_string(self):
        svc, _ = _make_service()
        errors = svc._validate({"timezone": 42})
        assert len(errors) == 1

    # multiple errors
    def test_multiple_invalid_fields(self):
        svc, _ = _make_service()
        data = {
            "storage_threshold_percent": 10,
            "clip_duration_seconds": 5,
        }
        errors = svc._validate(data)
        assert len(errors) == 2

    def test_empty_data_no_errors(self):
        svc, _ = _make_service()
        assert svc._validate({}) == []


# ---- get_wifi_status ----


class TestGetWifiStatus:
    """Test WiFi status retrieval."""

    @patch("monitor.services.settings_service.subprocess")
    @patch("monitor.services.settings_service.time")
    def test_returns_ssid_and_networks(self, mock_time, mock_subprocess):
        mock_run = MagicMock()
        mock_subprocess.run = mock_run

        # First call: _get_current_ssid
        ssid_result = MagicMock()
        ssid_result.stdout = "yes:MyNetwork\nno:OtherNet\n"

        # Second call: rescan (no output needed)
        rescan_result = MagicMock()

        # Third call: wifi list
        list_result = MagicMock()
        list_result.stdout = "MyNetwork:85:WPA2\nOtherNet:60:WPA2\n"

        mock_run.side_effect = [ssid_result, rescan_result, list_result]

        svc, _ = _make_service()
        result = svc.get_wifi_status()

        assert result["current_ssid"] == "MyNetwork"
        assert len(result["networks"]) == 2
        assert result["networks"][0]["ssid"] == "MyNetwork"
        assert result["networks"][0]["signal"] == 85
        assert result["networks"][1]["ssid"] == "OtherNet"

    @patch("monitor.services.settings_service.subprocess")
    def test_no_active_network_returns_empty_ssid(self, mock_subprocess):
        mock_run = MagicMock()
        mock_subprocess.run = mock_run

        ssid_result = MagicMock()
        ssid_result.stdout = "no:SomeNet\n"

        mock_run.return_value = ssid_result

        svc, _ = _make_service()
        ssid = svc._get_current_ssid()
        assert ssid == ""

    @patch("monitor.services.settings_service.subprocess")
    def test_get_ssid_exception_returns_empty(self, mock_subprocess):
        mock_subprocess.run.side_effect = OSError("nmcli not found")
        svc, _ = _make_service()
        assert svc._get_current_ssid() == ""

    @patch("monitor.services.settings_service.subprocess")
    @patch("monitor.services.settings_service.time")
    def test_scan_exception_returns_empty_list(self, mock_time, mock_subprocess):
        mock_subprocess.run.side_effect = OSError("nmcli not found")
        svc, _ = _make_service()
        assert svc._scan_wifi_networks() == []

    @patch("monitor.services.settings_service.subprocess")
    @patch("monitor.services.settings_service.time")
    def test_scan_deduplicates_ssids(self, mock_time, mock_subprocess):
        mock_run = MagicMock()
        mock_subprocess.run = mock_run

        rescan_result = MagicMock()
        list_result = MagicMock()
        list_result.stdout = "Net1:90:WPA2\nNet1:80:WPA2\nNet2:70:WPA2\n"

        mock_run.side_effect = [rescan_result, list_result]

        svc, _ = _make_service()
        networks = svc._scan_wifi_networks()
        ssids = [n["ssid"] for n in networks]
        assert ssids == ["Net1", "Net2"]

    @patch("monitor.services.settings_service.subprocess")
    @patch("monitor.services.settings_service.time")
    def test_scan_sorts_by_signal_descending(self, mock_time, mock_subprocess):
        mock_run = MagicMock()
        mock_subprocess.run = mock_run

        rescan_result = MagicMock()
        list_result = MagicMock()
        list_result.stdout = "Weak:30:WPA2\nStrong:95:WPA2\nMedium:60:WPA2\n"

        mock_run.side_effect = [rescan_result, list_result]

        svc, _ = _make_service()
        networks = svc._scan_wifi_networks()
        signals = [n["signal"] for n in networks]
        assert signals == [95, 60, 30]

    @patch("monitor.services.settings_service.subprocess")
    @patch("monitor.services.settings_service.time")
    def test_scan_non_digit_signal_defaults_to_zero(self, mock_time, mock_subprocess):
        mock_run = MagicMock()
        mock_subprocess.run = mock_run

        rescan_result = MagicMock()
        list_result = MagicMock()
        list_result.stdout = "Net1:bad:WPA2\n"

        mock_run.side_effect = [rescan_result, list_result]

        svc, _ = _make_service()
        networks = svc._scan_wifi_networks()
        assert networks[0]["signal"] == 0

    @patch("monitor.services.settings_service.subprocess")
    @patch("monitor.services.settings_service.time")
    def test_scan_skips_empty_ssid_lines(self, mock_time, mock_subprocess):
        mock_run = MagicMock()
        mock_subprocess.run = mock_run

        rescan_result = MagicMock()
        list_result = MagicMock()
        list_result.stdout = ":80:WPA2\nNet1:90:WPA2\n"

        mock_run.side_effect = [rescan_result, list_result]

        svc, _ = _make_service()
        networks = svc._scan_wifi_networks()
        assert len(networks) == 1
        assert networks[0]["ssid"] == "Net1"


# ---- connect_wifi ----


class TestConnectWifi:
    """Test WiFi connection."""

    @patch("monitor.services.settings_service.subprocess")
    def test_successful_connection(self, mock_subprocess):
        result = MagicMock()
        result.returncode = 0
        mock_subprocess.run.return_value = result

        audit = MagicMock()
        svc, _ = _make_service(audit=audit)
        msg, code = svc.connect_wifi("MyNet", "pass123", "admin", "1.2.3.4")

        assert code == 200
        assert "Connected to MyNet" in msg
        audit.log_event.assert_called_once_with(
            "WIFI_CHANGED",
            user="admin",
            ip="1.2.3.4",
            detail="connected to: MyNet",
        )

    @patch("monitor.services.settings_service.subprocess")
    def test_failed_connection(self, mock_subprocess):
        result = MagicMock()
        result.returncode = 1
        result.stderr = "Error: connection failed"
        result.stdout = ""
        mock_subprocess.run.return_value = result

        svc, _ = _make_service()
        msg, code = svc.connect_wifi("BadNet", "pass", "admin", "1.2.3.4")

        assert code == 500
        assert "Error: connection failed" in msg

    @patch("monitor.services.settings_service.subprocess")
    def test_failed_connection_stderr_empty_uses_stdout(self, mock_subprocess):
        result = MagicMock()
        result.returncode = 1
        result.stderr = ""
        result.stdout = "stdout error"
        mock_subprocess.run.return_value = result

        svc, _ = _make_service()
        msg, code = svc.connect_wifi("BadNet", "pass", "admin", "1.2.3.4")

        assert code == 500
        assert "stdout error" in msg

    @patch("monitor.services.settings_service.subprocess")
    def test_failed_connection_no_output(self, mock_subprocess):
        result = MagicMock()
        result.returncode = 1
        result.stderr = ""
        result.stdout = ""
        mock_subprocess.run.return_value = result

        svc, _ = _make_service()
        msg, code = svc.connect_wifi("BadNet", "pass", "admin", "1.2.3.4")

        assert code == 500
        assert msg == "Connection failed"

    @patch("monitor.services.settings_service.subprocess")
    def test_timeout_returns_500(self, mock_subprocess):
        import subprocess as real_subprocess

        mock_subprocess.run.side_effect = real_subprocess.TimeoutExpired(
            cmd="nmcli", timeout=30
        )
        mock_subprocess.TimeoutExpired = real_subprocess.TimeoutExpired

        svc, _ = _make_service()
        msg, code = svc.connect_wifi("SlowNet", "pass", "admin", "1.2.3.4")

        assert code == 500
        assert "timed out" in msg

    @patch("monitor.services.settings_service.subprocess")
    def test_exception_returns_500(self, mock_subprocess):
        mock_subprocess.run.side_effect = OSError("nmcli not found")
        mock_subprocess.TimeoutExpired = type("TimeoutExpired", (Exception,), {})

        svc, _ = _make_service()
        msg, code = svc.connect_wifi("Net", "pass", "admin", "1.2.3.4")

        assert code == 500
        assert "nmcli not found" in msg

    def test_empty_ssid_returns_400(self):
        svc, _ = _make_service()
        msg, code = svc.connect_wifi("", "pass123", "admin", "1.2.3.4")
        assert code == 400
        assert "ssid is required" in msg

    def test_whitespace_ssid_returns_400(self):
        svc, _ = _make_service()
        msg, code = svc.connect_wifi("   ", "pass123", "admin", "1.2.3.4")
        assert code == 400
        assert "ssid is required" in msg

    def test_none_ssid_returns_400(self):
        svc, _ = _make_service()
        msg, code = svc.connect_wifi(None, "pass123", "admin", "1.2.3.4")
        assert code == 400
        assert "ssid is required" in msg

    def test_empty_password_returns_400(self):
        svc, _ = _make_service()
        msg, code = svc.connect_wifi("MyNet", "", "admin", "1.2.3.4")
        assert code == 400
        assert "password is required" in msg

    def test_none_password_returns_400(self):
        svc, _ = _make_service()
        msg, code = svc.connect_wifi("MyNet", None, "admin", "1.2.3.4")
        assert code == 400
        assert "password is required" in msg

    @patch("monitor.services.settings_service.subprocess")
    def test_no_audit_on_failure(self, mock_subprocess):
        result = MagicMock()
        result.returncode = 1
        result.stderr = "fail"
        result.stdout = ""
        mock_subprocess.run.return_value = result

        audit = MagicMock()
        svc, _ = _make_service(audit=audit)
        svc.connect_wifi("BadNet", "pass", "admin", "1.2.3.4")

        audit.log_event.assert_not_called()


# ---- _log_audit ----


class TestLogAudit:
    """Test fail-silent audit logging."""

    def test_audit_called_with_correct_params(self):
        audit = MagicMock()
        svc, _ = _make_service(audit=audit)
        svc._log_audit("TEST_EVENT", "admin", "1.2.3.4", "some detail")
        audit.log_event.assert_called_once_with(
            "TEST_EVENT", user="admin", ip="1.2.3.4", detail="some detail"
        )

    def test_no_audit_service_does_not_raise(self):
        svc, _ = _make_service(audit=None)
        svc._log_audit("EVENT", "user", "ip", "detail")

    def test_audit_exception_silenced(self):
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("audit broken")
        svc, _ = _make_service(audit=audit)
        # Should not raise
        svc._log_audit("EVENT", "user", "ip", "detail")


# ---- UPDATABLE_FIELDS constant ----


class TestUpdatableFields:
    """Test that the expected fields are in UPDATABLE_FIELDS."""

    def test_expected_fields(self):
        expected = {
            "timezone",
            "storage_threshold_percent",
            "clip_duration_seconds",
            "session_timeout_minutes",
            "hostname",
            "tailscale_enabled",
            "tailscale_auto_connect",
            "tailscale_accept_routes",
            "tailscale_ssh",
            "tailscale_auth_key",
        }
        assert expected == UPDATABLE_FIELDS

    def test_setup_completed_not_updatable(self):
        assert "setup_completed" not in UPDATABLE_FIELDS

    def test_firmware_version_not_updatable(self):
        assert "firmware_version" not in UPDATABLE_FIELDS
