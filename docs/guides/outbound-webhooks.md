# Outbound Webhooks

Status: Draft operator guide for issue #239.

## Purpose

Outbound webhooks let the server push selected events to an HTTPS endpoint
without introducing a required MQTT broker or cloud relay.

Supported event classes:

- `motion`
- `camera_offline`
- `storage_low`
- `ota_outcome`

## Configuration Rules

- Destinations must use `https://`.
- Literal private or loopback IPs such as `127.0.0.1`, `192.168.0.10`, and `::1`
  are rejected.
- Authentication modes:
  - `none`
  - `bearer`
  - `hmac`
- Custom headers are allowed except for reserved transport/auth headers such as
  `Authorization`, `Content-Type`, `Content-Length`, `Host`, and
  `X-Webhook-Signature`.
- Custom header names must be valid HTTP header tokens, and custom header
  values cannot contain newline characters.
- Secrets are write-only in the API and settings UI. Responses expose only
  whether a secret is configured.

## Payload Schema

Each delivery posts JSON with this stable top-level shape:

```json
{
  "schema_version": 1,
  "event_id": "evt-1234567890ab",
  "event_type": "motion",
  "severity": "warning",
  "timestamp": "2026-05-03T12:00:00Z",
  "camera_id": "cam-front",
  "camera_name": "Front Door",
  "message": "Motion detected on Front Door",
  "snapshot_url": "/api/v1/recordings/cam-front/2026-05-03/12-00-00.jpg",
  "metadata": {
    "duration_seconds": 5.0,
    "peak_score": 0.42
  }
}
```

Notes:

- `snapshot_url` is omitted (`null`) when the event has no correlated snapshot.
- `metadata` varies by event type. For example, OTA outcomes include the
  originating audit event and outcome string.

## Delivery Semantics

- Deliveries run asynchronously in background worker threads.
- A single destination is processed serially so one slow receiver cannot
  receive concurrent overlapping requests.
- Transient failures retry with Fibonacci backoff: `5s`, `8s`, `13s`.
- Each attempt is written to the audit log as either
  `WEBHOOK_DELIVERY_SUCCESS` or `WEBHOOK_DELIVERY_FAILED`.
- After five consecutive failed attempts for the same destination, the system
  emits `WEBHOOK_DELIVERY_DEGRADED`, which the alert center surfaces to admins.

## Authentication Headers

- `bearer`: sends `Authorization: Bearer <secret>`
- `hmac`: sends `X-Webhook-Signature: sha256=<hex>`

The HMAC is calculated over the exact JSON request body bytes.
