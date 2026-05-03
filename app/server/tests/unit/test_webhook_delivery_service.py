# REQ: SWR-056, SWR-057; RISK: RISK-017, RISK-020, RISK-021; SEC: SC-012, SC-020, SC-021; TEST: TC-023, TC-041, TC-042, TC-048, TC-049
"""Tests for outbound webhook delivery."""

from __future__ import annotations

import hmac
import json
from hashlib import sha256

from monitor.models import Camera, MotionEvent
from monitor.services.audit import AuditLogger
from monitor.services.motion_event_store import MotionEventStore
from monitor.services.webhook_delivery_service import HttpResult, WebhookDeliveryService
from monitor.store import Store


def _make_service(data_dir, http_client, *, sleep_fn=None):
    store = Store(str(data_dir / "config"))
    audit = AuditLogger(str(data_dir / "logs"))
    motion = MotionEventStore(str(data_dir / "config" / "motion_events.json"))
    service = WebhookDeliveryService(
        store=store,
        audit=audit,
        motion_event_store=motion,
        http_client=http_client,
        sleep_fn=sleep_fn,
    )
    return service, store, audit, motion


class TestWebhookConfiguration:
    def test_rejects_private_literal_targets(self, data_dir):
        service, _, _, _ = _make_service(
            data_dir,
            lambda *args: HttpResult(204, {}, "", args[0]),
        )
        try:
            destination, error, status = service.create_destination(
                {
                    "url": "https://127.0.0.1/hook",
                    "auth_type": "none",
                    "event_classes": ["motion"],
                    "enabled": True,
                }
            )
        finally:
            service.close()

        assert destination is None
        assert status == 400
        assert "private" in error.lower()

    def test_rejects_unsafe_custom_headers(self, data_dir):
        service, _, _, _ = _make_service(
            data_dir,
            lambda *args: HttpResult(204, {}, "", args[0]),
        )
        try:
            destination, error, status = service.create_destination(
                {
                    "url": "https://hooks.example.com/inbound",
                    "auth_type": "none",
                    "custom_headers": {"X Bad": "ok"},
                    "event_classes": ["motion"],
                    "enabled": True,
                }
            )
            assert destination is None
            assert status == 400
            assert "valid http header" in error.lower()

            destination, error, status = service.create_destination(
                {
                    "url": "https://hooks.example.com/inbound",
                    "auth_type": "none",
                    "custom_headers": {"X-Env": "prod\r\nX-Injected: yes"},
                    "event_classes": ["motion"],
                    "enabled": True,
                }
            )
        finally:
            service.close()

        assert destination is None
        assert status == 400
        assert "newline" in error.lower()

    def test_send_test_adds_hmac_signature_and_custom_headers(self, data_dir):
        calls = []

        def _http(url, body, headers, timeout):
            calls.append((url, body, headers, timeout))
            return HttpResult(204, {}, "", url)

        service, _, audit, _ = _make_service(data_dir, _http)
        try:
            destination, error, status = service.create_destination(
                {
                    "url": "https://hooks.example.com/inbound",
                    "auth_type": "hmac",
                    "secret": "super-secret",
                    "custom_headers": {"X-Env": "test"},
                    "event_classes": ["motion"],
                    "enabled": True,
                }
            )
            assert not error
            assert status == 201

            result, error, status = service.send_test(destination["id"])
        finally:
            service.close()

        assert not error
        assert status == 200
        assert result["delivered"] is True
        assert len(calls) == 1
        url, body, headers, timeout = calls[0]
        assert url == "https://hooks.example.com/inbound"
        assert timeout == 10
        assert headers["X-Env"] == "test"
        expected = hmac.new(b"super-secret", body, sha256).hexdigest()
        assert headers["X-Webhook-Signature"] == f"sha256={expected}"
        audit_events = audit.get_events(limit=10, event_type="WEBHOOK_DELIVERY_SUCCESS")
        assert len(audit_events) == 1


class TestWebhookDelivery:
    def test_motion_event_delivery_includes_snapshot_url(self, data_dir):
        calls = []

        def _http(url, body, headers, timeout):
            calls.append(json.loads(body.decode("utf-8")))
            return HttpResult(200, {}, "ok", url)

        service, store, _, motion = _make_service(data_dir, _http)
        try:
            store.save_camera(
                Camera(id="cam-front", name="Front Door", status="online")
            )
            destination, _, _ = service.create_destination(
                {
                    "url": "https://hooks.example.com/motion",
                    "auth_type": "none",
                    "event_classes": ["motion"],
                    "enabled": True,
                }
            )
            assert destination is not None

            motion.append(
                MotionEvent(
                    id="mot-1",
                    camera_id="cam-front",
                    started_at="2026-05-03T09:00:00Z",
                    ended_at="2026-05-03T09:00:05Z",
                    peak_score=0.25,
                    duration_seconds=5.0,
                    clip_ref={
                        "camera_id": "cam-front",
                        "date": "2026-05-03",
                        "filename": "09-00-00.mp4",
                    },
                )
            )

            queued = service.enqueue_motion_event("mot-1")
            assert queued == 1
            assert service.wait_for_idle()
        finally:
            service.close()

        assert len(calls) == 1
        payload = calls[0]
        assert payload["event_type"] == "motion"
        assert payload["camera_id"] == "cam-front"
        assert payload["camera_name"] == "Front Door"
        assert (
            payload["snapshot_url"]
            == "/api/v1/recordings/cam-front/2026-05-03/09-00-00.jpg"
        )

    def test_transient_failures_retry_and_emit_degraded(self, data_dir):
        calls = []

        def _http(url, body, headers, timeout):
            calls.append(url)
            return HttpResult(503, {}, "busy", url)

        service, store, audit, _ = _make_service(
            data_dir, _http, sleep_fn=lambda _: None
        )
        try:
            destination, _, _ = service.create_destination(
                {
                    "url": "https://hooks.example.com/storage",
                    "auth_type": "none",
                    "event_classes": ["storage_low"],
                    "enabled": True,
                }
            )
            assert destination is not None

            event = {
                "timestamp": "2026-05-03T09:00:00Z",
                "event": "STORAGE_LOW",
                "detail": "recordings free space 9.0%",
            }
            service.handle_audit_entry(event)
            assert service.wait_for_idle()
            service.handle_audit_entry(event)
            assert service.wait_for_idle()
        finally:
            service.close()

        assert len(calls) == 8
        degraded = audit.get_events(limit=10, event_type="WEBHOOK_DELIVERY_DEGRADED")
        assert len(degraded) == 1
        settings = store.get_settings()
        assert settings.webhook_destinations[0].degraded is True
        assert settings.webhook_destinations[0].consecutive_failures >= 5
