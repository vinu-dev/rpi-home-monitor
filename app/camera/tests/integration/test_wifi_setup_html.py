# REQ: SWR-036; RISK: RISK-012; SEC: SC-012; TEST: TC-034
"""Rendered setup-page HTML tests for the camera WiFi setup server."""

import json
from unittest.mock import patch
from urllib.request import urlopen

from camera_streamer.wifi_setup import WifiSetupServer

TEST_PORT = 18081


def _fetch_html(path):
    with urlopen(f"http://127.0.0.1:{TEST_PORT}{path}", timeout=5) as response:
        return response.read().decode("utf-8"), response.status


@patch("camera_streamer.wifi.scan_networks", return_value=[])
def test_setup_page_inlines_qr_library_and_slots(mock_scan, unconfigured_config):
    with patch("camera_streamer.wifi_setup.LISTEN_PORT", TEST_PORT):
        server = WifiSetupServer(unconfigured_config)
        server.start()
        try:
            html, status = _fetch_html("/setup")
            assert status == 200
            assert "{{QRCODE_LIB}}" not in html
            assert "Project Nayuki" in html
            assert 'id="cam-address-qr"' in html
            assert 'id="result-address-qr"' in html
            assert html.count("Resolving IP...") >= 2
            assert (
                html.index('id="cam-address-display"')
                < html.index('id="cam-address-qr"')
                < html.index('id="cam-login-user"')
            )
        finally:
            server.stop()


@patch("camera_streamer.wifi.scan_networks", return_value=[])
def test_setup_page_serves_even_with_alternate_qr_library_source(
    mock_scan, unconfigured_config
):
    with (
        patch("camera_streamer.wifi_setup.LISTEN_PORT", TEST_PORT),
        patch(
            "camera_streamer.wifi_setup._QRCODE_LIB",
            json.dumps("window.qrcodegen = { broken: true };"),
        ),
    ):
        server = WifiSetupServer(unconfigured_config)
        server.start()
        try:
            html, status = _fetch_html("/setup")
            assert status == 200
            assert "window.qrcodegen = { broken: true };" in html
            assert 'id="cam-address-display"' in html
            assert 'id="result-address-display"' in html
        finally:
            server.stop()
