# REQ: SWR-036; RISK: RISK-012; SEC: SC-012; TEST: TC-034
"""Unit tests for camera_streamer.wifi — all nmcli calls are mocked."""

import subprocess
from unittest.mock import MagicMock, patch

from camera_streamer.wifi import (
    HOTSPOT_PASS,
    HOTSPOT_SSID,
    connect_network,
    get_current_ssid,
    get_hostname,
    get_ip_address,
    scan_networks,
    set_hostname,
    start_hotspot,
    stop_hotspot,
    wait_for_interface,
)

# ===========================================================================
# scan_networks
# ===========================================================================


class TestScanNetworks:
    def test_returns_list_of_dicts(self):
        mock_rescan = MagicMock(returncode=0)
        mock_list = MagicMock(
            stdout="HomeWifi:85:WPA2\nCoffeeShop:60:WPA2\nOpenNet:40:\n",
            returncode=0,
        )
        with patch(
            "camera_streamer.wifi.subprocess.run", side_effect=[mock_rescan, mock_list]
        ):
            with patch("camera_streamer.wifi.time.sleep"):
                networks = scan_networks("wlan0")
        assert len(networks) == 3
        assert networks[0]["ssid"] == "HomeWifi"
        assert networks[0]["signal"] == 85
        assert networks[0]["security"] == "WPA2"

    def test_sorted_by_signal_descending(self):
        mock_rescan = MagicMock(returncode=0)
        mock_list = MagicMock(
            stdout="Weak:20:WPA2\nStrong:90:WPA2\nMid:55:WPA2\n",
            returncode=0,
        )
        with patch(
            "camera_streamer.wifi.subprocess.run", side_effect=[mock_rescan, mock_list]
        ):
            with patch("camera_streamer.wifi.time.sleep"):
                networks = scan_networks()
        assert networks[0]["signal"] == 90
        assert networks[-1]["signal"] == 20

    def test_deduplicates_ssids(self):
        mock_rescan = MagicMock(returncode=0)
        mock_list = MagicMock(
            stdout="HomeWifi:85:WPA2\nHomeWifi:80:WPA2\n",
            returncode=0,
        )
        with patch(
            "camera_streamer.wifi.subprocess.run", side_effect=[mock_rescan, mock_list]
        ):
            with patch("camera_streamer.wifi.time.sleep"):
                networks = scan_networks()
        assert len(networks) == 1
        assert networks[0]["ssid"] == "HomeWifi"

    def test_skips_empty_ssids(self):
        mock_rescan = MagicMock(returncode=0)
        mock_list = MagicMock(
            stdout=":85:WPA2\nGoodNet:70:WPA2\n",
            returncode=0,
        )
        with patch(
            "camera_streamer.wifi.subprocess.run", side_effect=[mock_rescan, mock_list]
        ):
            with patch("camera_streamer.wifi.time.sleep"):
                networks = scan_networks()
        assert all(n["ssid"] != "" for n in networks)

    def test_returns_empty_list_on_exception(self):
        with patch(
            "camera_streamer.wifi.subprocess.run",
            side_effect=Exception("nmcli not found"),
        ):
            networks = scan_networks()
        assert networks == []

    def test_non_numeric_signal_defaults_to_zero(self):
        mock_rescan = MagicMock(returncode=0)
        mock_list = MagicMock(
            stdout="Flaky:N/A:WPA2\n",
            returncode=0,
        )
        with patch(
            "camera_streamer.wifi.subprocess.run", side_effect=[mock_rescan, mock_list]
        ):
            with patch("camera_streamer.wifi.time.sleep"):
                networks = scan_networks()
        assert networks[0]["signal"] == 0


# ===========================================================================
# connect_network
# ===========================================================================


class TestConnectNetwork:
    def test_returns_true_on_success(self):
        mock_run = MagicMock(returncode=0, stdout="", stderr="")
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            ok, err = connect_network("HomeWifi", "password123")
        assert ok is True
        assert err == ""

    def test_returns_false_on_nonzero_returncode(self):
        mock_run = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: No network with SSID 'HomeWifi' found.",
        )
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            ok, err = connect_network("HomeWifi", "wrong")
        assert ok is False
        assert "No network" in err

    def test_returns_false_on_timeout(self):
        with patch(
            "camera_streamer.wifi.subprocess.run",
            side_effect=subprocess.TimeoutExpired("nmcli", 30),
        ):
            ok, err = connect_network("HomeWifi", "pass")
        assert ok is False
        assert "timed out" in err.lower()

    def test_returns_false_on_unexpected_exception(self):
        with patch(
            "camera_streamer.wifi.subprocess.run", side_effect=OSError("no such file")
        ):
            ok, err = connect_network("HomeWifi", "pass")
        assert ok is False
        assert err != ""

    def test_passes_interface_to_nmcli(self):
        mock_run = MagicMock(returncode=0, stdout="", stderr="")
        with patch(
            "camera_streamer.wifi.subprocess.run", return_value=mock_run
        ) as mock:
            connect_network("Net", "pass", wifi_interface="wlan1")
        cmd = mock.call_args[0][0]
        assert "wlan1" in cmd


# ===========================================================================
# wait_for_interface
# ===========================================================================


class TestWaitForInterface:
    def test_returns_true_when_interface_found_immediately(self):
        mock_run = MagicMock(
            stdout="wlan0:wifi\neth0:ethernet\n",
            returncode=0,
        )
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            with patch("camera_streamer.wifi.time.sleep"):
                result = wait_for_interface("wlan0", max_wait=5)
        assert result is True

    def test_returns_false_when_interface_never_appears(self):
        mock_run = MagicMock(stdout="eth0:ethernet\n", returncode=0)
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            with patch("camera_streamer.wifi.time.sleep"):
                result = wait_for_interface("wlan0", max_wait=3)
        assert result is False

    def test_retries_until_success(self):
        not_ready = MagicMock(stdout="eth0:ethernet\n", returncode=0)
        ready = MagicMock(stdout="wlan0:wifi\n", returncode=0)
        with patch(
            "camera_streamer.wifi.subprocess.run",
            side_effect=[not_ready, not_ready, ready],
        ):
            with patch("camera_streamer.wifi.time.sleep"):
                result = wait_for_interface("wlan0", max_wait=10)
        assert result is True

    def test_exception_in_nmcli_does_not_crash(self):
        with patch(
            "camera_streamer.wifi.subprocess.run", side_effect=Exception("nmcli gone")
        ):
            with patch("camera_streamer.wifi.time.sleep"):
                result = wait_for_interface("wlan0", max_wait=2)
        assert result is False


# ===========================================================================
# start_hotspot
# ===========================================================================


class TestStartHotspot:
    def _mock_success(self):
        """Return a sequence of successful subprocess.run results."""
        ready = MagicMock(stdout="wlan0:wifi\n", returncode=0)
        delete = MagicMock(returncode=0)
        add = MagicMock(returncode=0, stdout="", stderr="")
        up = MagicMock(returncode=0, stdout="", stderr="")
        return [ready, delete, add, up]

    def test_returns_true_on_success(self):
        with patch(
            "camera_streamer.wifi.subprocess.run", side_effect=self._mock_success()
        ):
            with patch("camera_streamer.wifi.time.sleep"):
                result = start_hotspot("wlan0")
        assert result is True

    def test_returns_false_when_interface_not_found(self):
        with patch("camera_streamer.wifi.wait_for_interface", return_value=False):
            result = start_hotspot("wlan0")
        assert result is False

    def test_returns_false_on_add_failure(self):
        MagicMock(stdout="wlan0:wifi\n", returncode=0)
        delete = MagicMock(returncode=0)
        with patch("camera_streamer.wifi.wait_for_interface", return_value=True):
            with patch(
                "camera_streamer.wifi.subprocess.run",
                side_effect=[delete, subprocess.CalledProcessError(1, "nmcli")],
            ):
                result = start_hotspot("wlan0")
        assert result is False

    def test_uses_default_ssid_and_password(self):
        with patch("camera_streamer.wifi.wait_for_interface", return_value=True):
            with patch("camera_streamer.wifi.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                start_hotspot("wlan0")
        all_args = [str(call) for call in mock_run.call_args_list]
        combined = " ".join(all_args)
        assert HOTSPOT_SSID in combined
        assert HOTSPOT_PASS in combined

    def test_retries_activation_on_failure(self):
        MagicMock(side_effect=subprocess.CalledProcessError(1, "nmcli", stderr="busy"))
        MagicMock(returncode=0, stdout="", stderr="")
        with patch("camera_streamer.wifi.wait_for_interface", return_value=True):
            with patch("camera_streamer.wifi.subprocess.run") as mock_run:
                # delete, add succeed; up fails once then succeeds
                mock_run.side_effect = [
                    MagicMock(returncode=0),  # delete
                    MagicMock(returncode=0),  # add
                    subprocess.CalledProcessError(
                        1, "nmcli", stderr=b"busy"
                    ),  # up fail
                    MagicMock(returncode=0),  # up success
                ]
                with patch("camera_streamer.wifi.time.sleep"):
                    result = start_hotspot("wlan0")
        assert result is True


# ===========================================================================
# stop_hotspot
# ===========================================================================


class TestStopHotspot:
    def test_calls_nmcli_down_and_delete(self):
        with patch("camera_streamer.wifi.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            stop_hotspot()
        assert mock_run.call_count == 2
        calls_str = str(mock_run.call_args_list)
        assert "down" in calls_str
        assert "delete" in calls_str

    def test_does_not_raise_on_error(self):
        with patch(
            "camera_streamer.wifi.subprocess.run",
            side_effect=FileNotFoundError("nmcli not found"),
        ):
            stop_hotspot()  # must not raise


# ===========================================================================
# get_current_ssid
# ===========================================================================


class TestGetCurrentSSID:
    def test_returns_active_ssid(self):
        mock_run = MagicMock(stdout="no:OtherNet\nyes:HomeWifi\n", returncode=0)
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            assert get_current_ssid() == "HomeWifi"

    def test_returns_empty_when_not_connected(self):
        mock_run = MagicMock(stdout="no:HomeWifi\nno:OtherNet\n", returncode=0)
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            assert get_current_ssid() == ""

    def test_returns_empty_on_exception(self):
        with patch("camera_streamer.wifi.subprocess.run", side_effect=Exception):
            assert get_current_ssid() == ""


# ===========================================================================
# get_ip_address
# ===========================================================================


class TestGetIPAddress:
    def test_parses_ip_from_nmcli_output(self):
        mock_run = MagicMock(stdout="IP4.ADDRESS[1]:192.168.1.42/24\n", returncode=0)
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            assert get_ip_address("wlan0") == "192.168.1.42"

    def test_returns_empty_when_no_ip(self):
        mock_run = MagicMock(stdout="", returncode=0)
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            assert get_ip_address("wlan0") == ""

    def test_returns_empty_on_exception(self):
        with patch("camera_streamer.wifi.subprocess.run", side_effect=Exception):
            assert get_ip_address("wlan0") == ""


# ===========================================================================
# get_hostname / set_hostname
# ===========================================================================


class TestGetHostname:
    def test_returns_hostname_from_command(self):
        mock_run = MagicMock(stdout="homecam-01\n", returncode=0)
        with patch("camera_streamer.wifi.subprocess.run", return_value=mock_run):
            assert get_hostname() == "homecam-01"

    def test_returns_empty_on_exception(self):
        with patch("camera_streamer.wifi.subprocess.run", side_effect=Exception):
            assert get_hostname() == ""


class TestSetHostname:
    def test_returns_true_on_success(self, tmp_path):
        with patch(
            "camera_streamer.wifi.subprocess.run", return_value=MagicMock(returncode=0)
        ):
            with patch("camera_streamer.wifi.os.makedirs"):
                with patch(
                    "builtins.open",
                    MagicMock(
                        return_value=MagicMock(
                            __enter__=MagicMock(return_value=MagicMock()),
                            __exit__=MagicMock(return_value=False),
                        )
                    ),
                ):
                    result = set_hostname("homecam-new")
        assert result is True

    def test_returns_false_on_exception(self):
        with patch(
            "camera_streamer.wifi.subprocess.run",
            side_effect=Exception("no hostname binary"),
        ):
            result = set_hostname("homecam-new")
        assert result is False

    def test_persists_hostname_to_data_dir(self, tmp_path):
        """Hostname is written to /data/config/hostname for reboot persistence."""
        tmp_path / "config" / "hostname"

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0)

        with patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run):
            with patch("camera_streamer.wifi.os.makedirs"):
                with patch("builtins.open", MagicMock()) as mock_open:
                    set_hostname("test-cam")
        # Verify we tried to open the persistence file path
        open_calls = [str(c) for c in mock_open.call_args_list]
        assert any("/data/config/hostname" in c for c in open_calls)
