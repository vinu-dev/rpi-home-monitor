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


class TestSetHostnameMdnsGoodbye:
    """Verify the mDNS goodbye path on hostname change (issue #200, RFC 6762 §10.1).

    The daemon-restart shortcut used previously kills avahi-daemon
    before it can broadcast cache-flush records for the OLD hostname,
    so cached resolvers around the network keep the old name
    resolvable for its full TTL window. ``avahi-set-host-name`` swaps
    the daemon's owned name in-place: goodbye for old + announce for
    new in one atomic transition.
    """

    def _ok(self):
        """Stand-in for a successful subprocess.run result."""
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    def _err(self, rc=1, stderr=b"D-Bus error: AccessDenied\n"):
        return MagicMock(returncode=rc, stdout=b"", stderr=stderr)

    def test_calls_avahi_set_host_name_with_new_name(self, tmp_path):
        """The new name must be handed to avahi-daemon via the D-Bus helper."""
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd)
            return self._ok()

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch("camera_streamer.wifi.socket.gethostname", return_value="rpi-old"),
        ):
            assert set_hostname("rpi-new") is True

        # The D-Bus helper was invoked with the new name.
        assert ["avahi-set-host-name", "rpi-new"] in runs, runs

    def test_does_not_restart_avahi_when_set_host_name_succeeds(self, tmp_path):
        """The whole point of the fix: no daemon restart when goodbye succeeded."""
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd)
            return self._ok()

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch("camera_streamer.wifi.socket.gethostname", return_value="rpi-old"),
        ):
            set_hostname("rpi-new")

        commands = [c[0] for c in runs]
        assert "avahi-set-host-name" in commands
        # No fallback restart should fire on the happy path.
        for cmd in runs:
            assert cmd[:2] != ["systemctl", "restart"], (
                f"unexpected daemon restart on success path: {cmd}"
            )

    def test_falls_back_to_daemon_restart_when_set_host_name_returns_nonzero(
        self, tmp_path
    ):
        """Daemon refused → still publish the new name (without goodbye)."""
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd)
            if cmd[0] == "avahi-set-host-name":
                return self._err(rc=2, stderr=b"name conflict\n")
            return self._ok()

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch("camera_streamer.wifi.socket.gethostname", return_value="rpi-old"),
        ):
            assert set_hostname("rpi-new") is True

        commands = [c for c in runs]
        assert ["avahi-set-host-name", "rpi-new"] in commands
        assert ["systemctl", "restart", "avahi-daemon"] in commands

    def test_falls_back_when_set_host_name_missing(self, tmp_path):
        """No avahi-set-host-name binary → fallback to daemon restart."""
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd)
            if cmd[0] == "avahi-set-host-name":
                raise FileNotFoundError(2, "No such file")
            return self._ok()

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch("camera_streamer.wifi.socket.gethostname", return_value="rpi-old"),
        ):
            set_hostname("rpi-new")

        assert ["systemctl", "restart", "avahi-daemon"] in runs

    def test_falls_back_when_set_host_name_times_out(self, tmp_path):
        """Daemon stuck → still set the new name and try the restart fallback."""
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd)
            if cmd[0] == "avahi-set-host-name":
                raise subprocess.TimeoutExpired(cmd, 5)
            return self._ok()

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch("camera_streamer.wifi.socket.gethostname", return_value="rpi-old"),
        ):
            assert set_hostname("rpi-new") is True

        assert ["systemctl", "restart", "avahi-daemon"] in runs

    def test_logs_old_to_new_rotation(self, tmp_path, caplog):
        """The rotation log line must include both the old and new name for debug."""
        import logging as _logging

        with (
            patch(
                "camera_streamer.wifi.subprocess.run",
                return_value=MagicMock(returncode=0, stdout=b"", stderr=b""),
            ),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch(
                "camera_streamer.wifi.socket.gethostname", return_value="rpi-old-d8ee"
            ),
            caplog.at_level(_logging.INFO, logger="camera-streamer.wifi"),
        ):
            set_hostname("rpi-new-a5cf")

        rotation_lines = [
            r.message for r in caplog.records if "mDNS hostname change" in r.message
        ]
        assert any(
            "rpi-old-d8ee" in line and "rpi-new-a5cf" in line for line in rotation_lines
        ), rotation_lines

    def test_no_rotation_log_when_unchanged(self, tmp_path, caplog):
        """Idempotent set (same name) should log a different message — no goodbye dance implied."""
        import logging as _logging

        with (
            patch(
                "camera_streamer.wifi.subprocess.run",
                return_value=MagicMock(returncode=0, stdout=b"", stderr=b""),
            ),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch("camera_streamer.wifi.socket.gethostname", return_value="rpi-same"),
            caplog.at_level(_logging.INFO, logger="camera-streamer.wifi"),
        ):
            set_hostname("rpi-same")

        for r in caplog.records:
            assert "mDNS hostname change" not in r.message, r.message

    def test_socket_gethostname_failure_does_not_break_path(self, tmp_path):
        """socket.gethostname() failures fall through — the change still proceeds."""
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd)
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch(
                "camera_streamer.wifi.socket.gethostname",
                side_effect=OSError("EFAULT"),
            ),
        ):
            assert set_hostname("rpi-new") is True

        # Even with no prior name, we still hand the new name to avahi.
        assert ["avahi-set-host-name", "rpi-new"] in runs


class TestRawMdnsGoodbye:
    """Privilege-free fallback when avahi-set-host-name is denied (#233).

    The camera-streamer service runs as user `camera` and avahi's
    default D-Bus policy gates SetHostName to root + user `avahi`. The
    raw-UDP goodbye path constructs an RFC 6762 §10.1-compliant
    cache-flush record and sends it to 224.0.0.251:5353 — multicast
    UDP is unrestricted, so this works regardless of D-Bus policy.
    """

    def test_packet_byte_structure(self):
        """Byte-for-byte assert the wire format. Future refactors that
        scramble header flags or forget the cache-flush bit will fail
        here — operators rely on the exact 0x8001 CLASS for resolvers
        to actually flush their caches."""
        from camera_streamer.wifi import _build_mdns_goodbye_packet

        ip = b"\xc0\xa8\x01\x73"  # 192.168.1.115
        packet = _build_mdns_goodbye_packet("rpi-divinu-cam", ip)

        # Header
        assert packet[0:2] == b"\x00\x00"  # Transaction ID
        assert packet[2:4] == b"\x84\x00"  # Flags: QR=1, AA=1
        assert packet[4:6] == b"\x00\x00"  # QDCOUNT
        assert packet[6:8] == b"\x00\x01"  # ANCOUNT
        assert packet[8:10] == b"\x00\x00"  # NSCOUNT
        assert packet[10:12] == b"\x00\x00"  # ARCOUNT

        # NAME — length-prefixed labels
        # 0x0e = 14, "rpi-divinu-cam" = 14 bytes
        assert packet[12:13] == b"\x0e"
        assert packet[13:27] == b"rpi-divinu-cam"
        # 0x05 = 5, "local" = 5 bytes
        assert packet[27:28] == b"\x05"
        assert packet[28:33] == b"local"
        # Root terminator
        assert packet[33:34] == b"\x00"

        # Answer trailer
        assert packet[34:36] == b"\x00\x01"  # TYPE = A
        assert packet[36:38] == b"\x80\x01"  # CLASS = IN | cache-flush
        assert packet[38:42] == b"\x00\x00\x00\x00"  # TTL = 0 (goodbye)
        assert packet[42:44] == b"\x00\x04"  # RDLENGTH = 4
        assert packet[44:48] == ip  # RDATA

        assert len(packet) == 48

    def test_packet_handles_long_label(self):
        """A 63-byte label is the DNS limit; one byte over must raise."""
        from camera_streamer.wifi import _build_mdns_goodbye_packet

        ip = b"\xc0\xa8\x01\x73"
        # 63-byte label is fine.
        ok = _build_mdns_goodbye_packet("a" * 63, ip)
        assert isinstance(ok, bytes)
        # 64-byte label must error — we'd otherwise send a malformed packet.
        try:
            _build_mdns_goodbye_packet("a" * 64, ip)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for 64-byte label")

    def test_packet_rejects_non_ascii_label(self):
        """mDNS names are ASCII (no Punycode here) — a unicode label must raise."""
        from camera_streamer.wifi import _build_mdns_goodbye_packet

        ip = b"\xc0\xa8\x01\x73"
        try:
            _build_mdns_goodbye_packet("rpi-café", ip)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for non-ASCII label")

    def test_broadcast_sends_to_correct_multicast_target(self):
        """Multicast group + port + TTL must match RFC 6762 §11."""
        import socket as _socket

        from camera_streamer.wifi import _broadcast_mdns_goodbye

        sock = MagicMock()
        with (
            patch("camera_streamer.wifi.socket.socket", return_value=sock),
            patch("camera_streamer.wifi.time.sleep"),  # don't sleep 1s in tests
        ):
            assert _broadcast_mdns_goodbye("rpi-divinu-cam", "192.168.1.115") is True

        # Both sends went to the right multicast group.
        assert sock.sendto.call_count == 2
        for call in sock.sendto.call_args_list:
            target = call[0][1]
            assert target == ("224.0.0.251", 5353)

        # TTL=255 set on the socket per RFC 6762 §11. The numeric value
        # of socket.IP_MULTICAST_TTL differs by platform (Linux=33,
        # Windows=10, macOS=10) so we match against the live constant
        # rather than hard-coding it.
        ttl_calls = [
            c
            for c in sock.setsockopt.call_args_list
            if c[0][0] == _socket.IPPROTO_IP and c[0][1] == _socket.IP_MULTICAST_TTL
        ]
        assert ttl_calls, [c.args for c in sock.setsockopt.call_args_list]
        assert ttl_calls[0][0][2] == 255

    def test_broadcast_strips_dot_local_suffix(self):
        """Caller may pass ``foo`` or ``foo.local`` — both must produce a
        packet whose label is plain ``foo``."""
        from camera_streamer.wifi import _broadcast_mdns_goodbye

        sock = MagicMock()
        with (
            patch("camera_streamer.wifi.socket.socket", return_value=sock),
            patch("camera_streamer.wifi.time.sleep"),
        ):
            _broadcast_mdns_goodbye("rpi-cam.local", "192.168.1.50")

        sent = sock.sendto.call_args_list[0][0][0]
        # 0x07 length = "rpi-cam" (7 bytes)
        assert sent[12:13] == b"\x07"
        assert sent[13:20] == b"rpi-cam"

    def test_broadcast_fails_silently_on_send_error(self):
        """Multicast send raising must be logged and swallowed — the goodbye
        is best-effort and must never propagate up to break the hostname
        change itself."""
        from camera_streamer.wifi import _broadcast_mdns_goodbye

        sock = MagicMock()
        sock.sendto.side_effect = OSError("ENETUNREACH")
        with (
            patch("camera_streamer.wifi.socket.socket", return_value=sock),
            patch("camera_streamer.wifi.time.sleep"),
        ):
            # Must not raise; must return False.
            assert _broadcast_mdns_goodbye("rpi-cam", "192.168.1.50") is False

    def test_broadcast_skips_invalid_ip(self):
        """Garbage IP must early-return rather than crash inet_aton at send."""
        from camera_streamer.wifi import _broadcast_mdns_goodbye

        with patch("camera_streamer.wifi.socket.socket") as mock_sock_cls:
            assert _broadcast_mdns_goodbye("rpi-cam", "not-an-ip") is False
            # Socket was never even created.
            mock_sock_cls.assert_not_called()

    def test_broadcast_skips_empty_hostname(self):
        from camera_streamer.wifi import _broadcast_mdns_goodbye

        with patch("camera_streamer.wifi.socket.socket") as mock_sock_cls:
            assert _broadcast_mdns_goodbye("", "192.168.1.1") is False
            assert _broadcast_mdns_goodbye(".local", "192.168.1.1") is False
            mock_sock_cls.assert_not_called()

    def test_broadcast_closes_socket_on_success(self):
        """No fd leaks — socket.close() is called even on the happy path."""
        from camera_streamer.wifi import _broadcast_mdns_goodbye

        sock = MagicMock()
        with (
            patch("camera_streamer.wifi.socket.socket", return_value=sock),
            patch("camera_streamer.wifi.time.sleep"),
        ):
            _broadcast_mdns_goodbye("rpi-cam", "192.168.1.50")

        sock.close.assert_called_once()

    def test_broadcast_closes_socket_on_failure(self):
        """No fd leaks even when sendto raises mid-flow."""
        from camera_streamer.wifi import _broadcast_mdns_goodbye

        sock = MagicMock()
        sock.sendto.side_effect = OSError("EMFILE")
        with (
            patch("camera_streamer.wifi.socket.socket", return_value=sock),
            patch("camera_streamer.wifi.time.sleep"),
        ):
            _broadcast_mdns_goodbye("rpi-cam", "192.168.1.50")

        sock.close.assert_called_once()

    def test_set_hostname_calls_raw_goodbye_when_avahi_denied(self):
        """End-to-end: set_hostname must invoke the raw-UDP goodbye when
        avahi-set-host-name fails (the production scenario on this image
        — camera user lacks D-Bus permission)."""
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd)
            if cmd[0] == "avahi-set-host-name":
                return MagicMock(returncode=1, stdout=b"", stderr=b"Access denied\n")
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch(
                "camera_streamer.wifi.socket.gethostname",
                return_value="rpi-divinu-cam",
            ),
            patch("camera_streamer.wifi.get_ip_address", return_value="192.168.1.115"),
            patch("camera_streamer.wifi._broadcast_mdns_goodbye") as mock_goodbye,
        ):
            assert set_hostname("rpi-divinu-cam-a5cf") is True

        # Goodbye fired with old name + current IP.
        mock_goodbye.assert_called_once_with("rpi-divinu-cam", "192.168.1.115")
        # Daemon restart still happens after the goodbye so the new name
        # gets announced (we can't make THAT happen via raw UDP).
        assert ["systemctl", "restart", "avahi-daemon"] in runs

    def test_set_hostname_skips_raw_goodbye_when_no_change(self):
        """Idempotent set (same name) must not broadcast a goodbye —
        there's nothing to flush."""

        def fake_run(cmd, **kwargs):
            if cmd[0] == "avahi-set-host-name":
                return MagicMock(returncode=1, stderr=b"denied\n", stdout=b"")
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch("camera_streamer.wifi.socket.gethostname", return_value="rpi-same"),
            patch("camera_streamer.wifi.get_ip_address", return_value="192.168.1.115"),
            patch("camera_streamer.wifi._broadcast_mdns_goodbye") as mock_goodbye,
        ):
            set_hostname("rpi-same")

        mock_goodbye.assert_not_called()

    def test_set_hostname_skips_raw_goodbye_on_avahi_success(self):
        """When avahi-set-host-name works (root context), the daemon owns
        the goodbye + announce — no raw-UDP path needed."""

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        with (
            patch("camera_streamer.wifi.subprocess.run", side_effect=fake_run),
            patch("camera_streamer.wifi.os.makedirs"),
            patch("builtins.open", MagicMock()),
            patch(
                "camera_streamer.wifi.socket.gethostname",
                return_value="rpi-old",
            ),
            patch("camera_streamer.wifi.get_ip_address", return_value="192.168.1.115"),
            patch("camera_streamer.wifi._broadcast_mdns_goodbye") as mock_goodbye,
        ):
            set_hostname("rpi-new")

        mock_goodbye.assert_not_called()
