# REQ: SWR-024; RISK: RISK-012; SEC: SC-012; TEST: TC-023
"""Tests for monitor.services.network_info."""

from unittest.mock import patch

from monitor.services.network_info import get_network_payload


class TestNetworkInfo:
    def test_prefers_private_request_host(self):
        payload = get_network_payload("https://192.168.1.42:5443/", "127.0.0.1")

        assert payload == {
            "server_url": "https://192.168.1.42:5443/",
            "ip": "192.168.1.42",
            "port": 5443,
            "source": "request_host",
        }

    def test_falls_back_to_interface_ip_for_private_remote(self):
        fake_socket = type(
            "FakeSocket",
            (),
            {
                "__enter__": lambda self: self,
                "__exit__": lambda self, *_args: None,
                "connect": lambda self, _addr: None,
                "getsockname": lambda self: ("192.168.1.77", 12345),
            },
        )

        with patch(
            "monitor.services.network_info.socket.socket", return_value=fake_socket()
        ):
            payload = get_network_payload("https://homemonitor.local/", "192.168.1.99")

        assert payload == {
            "server_url": "https://192.168.1.77:443/",
            "ip": "192.168.1.77",
            "port": 443,
            "source": "wifi_iface",
        }

    def test_hides_payload_when_no_private_address_is_available(self):
        payload = get_network_payload("https://example.com/", "203.0.113.10")

        assert payload == {"server_url": "", "ip": "", "port": 443, "source": ""}
