# REQ: SWR-056, SWR-057; RISK: RISK-017, RISK-020, RISK-021; SEC: SC-012, SC-020, SC-021; TEST: TC-023, TC-041, TC-042, TC-048, TC-049
"""Integration tests for the webhook management API."""

from monitor.services.webhook_delivery_service import HttpResult


class TestWebhookAuth:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/webhooks").status_code == 401

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.get("/api/v1/webhooks").status_code == 403


class TestWebhookCrud:
    def test_create_list_update_toggle_and_delete(self, logged_in_client):
        client = logged_in_client()

        created = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://hooks.example.com/inbound",
                "auth_type": "bearer",
                "secret": "token-123",
                "custom_headers": {"X-Env": "prod"},
                "event_classes": ["motion", "storage_low"],
                "enabled": True,
            },
        )
        assert created.status_code == 201
        destination = created.get_json()["destination"]
        assert destination["secret_configured"] is True
        assert destination["custom_header_count"] == 1

        listed = client.get("/api/v1/webhooks")
        assert listed.status_code == 200
        assert len(listed.get_json()["destinations"]) == 1

        updated = client.put(
            f"/api/v1/webhooks/{destination['id']}",
            json={
                "url": "https://hooks.example.com/updated",
                "auth_type": "none",
                "event_classes": ["camera_offline"],
                "enabled": False,
                "custom_headers": {},
            },
        )
        assert updated.status_code == 200
        assert updated.get_json()["destination"]["auth_type"] == "none"
        assert updated.get_json()["destination"]["enabled"] is False

        toggled = client.patch(
            f"/api/v1/webhooks/{destination['id']}/enabled",
            json={"enabled": True},
        )
        assert toggled.status_code == 200
        assert toggled.get_json()["destination"]["enabled"] is True

        deleted = client.delete(f"/api/v1/webhooks/{destination['id']}")
        assert deleted.status_code == 200
        assert client.get("/api/v1/webhooks").get_json()["destinations"] == []

    def test_rejects_private_ip_targets(self, logged_in_client):
        client = logged_in_client()
        response = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://127.0.0.1/hook",
                "auth_type": "none",
                "event_classes": ["motion"],
                "enabled": True,
            },
        )
        assert response.status_code == 400
        assert "private" in response.get_json()["error"].lower()


class TestWebhookDeliveryEndpoints:
    def test_send_test_and_recent_deliveries(self, app, logged_in_client):
        client = logged_in_client()
        app.webhook_delivery_service._http_client = lambda url, body, headers, timeout: (
            HttpResult(
                202,
                {},
                "accepted",
                url,
            )
        )

        created = client.post(
            "/api/v1/webhooks",
            json={
                "url": "https://hooks.example.com/test",
                "auth_type": "none",
                "event_classes": ["motion"],
                "enabled": True,
            },
        )
        destination_id = created.get_json()["destination"]["id"]

        tested = client.post(f"/api/v1/webhooks/{destination_id}/test", json={})
        assert tested.status_code == 200
        assert tested.get_json()["delivery"]["delivered"] is True

        deliveries = client.get("/api/v1/webhooks/deliveries")
        assert deliveries.status_code == 200
        data = deliveries.get_json()
        assert data["count"] == 1
        assert data["deliveries"][0]["event_type"] == "test"
