# Feature Spec: Outbound Webhook Delivery For Motion Events and System Alerts

Tracking issue: #239. Branch: `feature/239-outbound-webhooks`.

## Title

Outbound webhook delivery for motion events and system alerts.

## Goal

Operators can register one or more outbound webhook destinations (URL, optional bearer or HMAC secret, optional custom headers) and route selected event classes - motion events, camera-offline, storage-low, OTA outcomes - to them. Each webhook posts a stable JSON payload that includes camera id, event type, severity, timestamp, snapshot URL (when applicable), and a signature header so the receiver can verify the request originated from this server. Delivery is best-effort with a short bounded retry budget; failures surface in the audit log so the operator can see when a downstream automation went silent. This unlocks Home Assistant, Node-RED, n8n, and custom-script integration without committing to a heavy MQTT broker dependency on day one.

This closes market-backlog items #73 ("Generic webhook actions", P1 W3) and #82 ("Local REST API for automation clients", P1 W2) per `docs/history/planning/market-feature-backlog-100.md`. The feature fits the mission of being a "trustworthy, self-hosted" product that "feels like a real product" - a self-hosted appliance without clean machine-readable outbound channels forces operators to reverse-engineer one.

## Context

Existing code this feature must build on:

- `app/server/monitor/services/notification_policy_service.py` - already classifies and filters motion events per-user and per-camera; webhook delivery becomes a third "channel" alongside in-app alerts and browser-push notifications, in the same service-layer pattern (ADR-0003).
- `app/server/monitor/services/alert_center_service.py` - catalogs user-visible alerts from audit events, motion events, and camera faults. The same event classes that feed the alert center (motion end, camera offline, storage low, OTA outcomes) feed webhook delivery.
- `app/server/monitor/services/audit.py` (`AuditLogger`) - webhook delivery outcomes (success, 4xx, 5xx, circuit-break) are logged here; this gives operators visibility into downstream failures.
- `app/server/monitor/models.py` - the `Settings` dataclass carries system-wide configuration. Webhook destinations list and delivery policy belong here.
- `app/server/monitor/api/` - admin-only CRUD endpoints for webhook management land in a new blueprint or extend an existing settings blueprint.
- `app/server/monitor/templates/` - settings UI for list, add, edit, delete, test-fire, recent-delivery view.
- `app/server/monitor/__init__.py:119` - app-factory pattern (ADR-0001) wires services into the Flask app.
- ADR-0003 (service-layer pattern) - webhook delivery service is a pure business-logic service; routes are thin HTTP adapters.

## User-Facing Behavior

### Primary path - register a webhook destination

1. Admin opens Settings -> Integrations -> Webhooks.
2. Page shows existing webhook destinations (list, with edit/delete buttons) and an "Add webhook" button.
3. Admin clicks "Add webhook". A form appears with fields:
   - **URL** (required): HTTPS endpoint. HTTP is rejected with a server-side guard and UI validation ("HTTPS only").
   - **Authentication method** (optional dropdown): None, Bearer token, or HMAC-SHA256.
   - **Secret** (optional, shown only if Bearer or HMAC selected): text input, masked on display, never shown in audit/logs.
   - **Custom headers** (optional): key-value pairs (e.g., `X-Custom-Auth: value`). Values masked in logs.
   - **Event routing** (checkboxes): "Motion events", "Camera offline", "Storage low", "OTA outcomes". At least one must be selected.
   - **Enabled** (toggle): default on. Allows operators to pause delivery without deleting the config.
4. Admin submits. Server validates:
   - URL is parseable HTTPS, no redirect chains (follow max 2 redirects, give up).
   - Secret is not empty if auth method is set.
   - At least one event class is selected.
5. On success, the destination is stored in `Settings.webhook_destinations`. Audit event `WEBHOOK_REGISTERED` is written with the URL (but not the secret), IP, and user.
6. Form shows "Saved. Test this webhook?" with a "Send test event" button.
7. Admin clicks "Send test event" to fire a synthetic payload (event type `test`, all fields populated with dummy data). Delivery attempt is logged. If it succeeds (2xx), show "OK Test delivered successfully"; if it fails, show the HTTP status and first 200 chars of response body.

### Primary path - event delivery

When a motion event ends, a camera goes offline, storage drops below threshold, or an OTA completes:

1. The existing notification-policy service already emits the alert to the alert center.
2. A new **webhook delivery service** is triggered by the same events.
3. For each webhook destination in `Settings.webhook_destinations`:
   - If `enabled = false` or the destination does not filter for this event class, skip.
   - Build the JSON payload (see "Payload schema" below).
   - Apply HMAC-SHA256 signature if configured (signature goes into the `X-Webhook-Signature` header).
   - Enqueue the delivery attempt.
4. The delivery service runs on a background queue (not blocking the event path):
   - Max 3 retries (Fibonacci backoff: 5s, 8s, 13s). After 3 failures, record `WEBHOOK_DELIVERY_FAILED` and move on.
   - Transient errors (5xx, timeout, connection refused) trigger retry; permanent errors (4xx) fail immediately.
   - Per-destination concurrency cap of 1 in-flight request to prevent the destination from overwhelming operators.
5. On success (2xx response), log `WEBHOOK_DELIVERY_SUCCESS` with the URL and response time.
6. If all destinations are down for a duration (e.g., 5 consecutive failures per destination), emit an audit event `WEBHOOK_DELIVERY_DEGRADED` to alert the operator via the alert center.

### Primary path - manage webhooks

- **Edit**: Admin clicks edit, modifies fields, submits. URL, secret, and headers are re-validated. Audit `WEBHOOK_UPDATED`.
- **Delete**: Admin clicks delete, gets a confirmation modal showing the URL and listing how many events would be lost (0 for a disabled webhook, best-effort count for an active one). On confirm, audit `WEBHOOK_DELETED` and remove from the list.
- **Disable**: Admin toggles the "Enabled" switch. Audit `WEBHOOK_DISABLED` or `WEBHOOK_ENABLED`.
- **View recent deliveries**: Settings page includes a "Recent deliveries" view showing the last 20 attempts across all destinations (timestamp, destination, event type, HTTP status, response time). Older entries are archived or purged after 30 days.

### Failure states (must be designed, not just unit-tested)

- Webhook URL is down -> payload queued, retried 3 times with Fibonacci backoff, then logged as failed. Audit log shows `WEBHOOK_DELIVERY_FAILED:5xx`.
- Secret not provided when auth method is "Bearer" -> form validation rejects on the client; server rejects during save.
- Destination deleted while a delivery is in-flight -> in-flight request completes (no-op if response arrives after deletion), but no retry is attempted.
- Payload too large to send (e.g., > 64 KB with embedded snapshot) -> truncate snapshot field or fall back to snapshot URL without embedding. Log a warning audit event `WEBHOOK_DELIVERY_TRUNCATED`.
- Webhook destination is misconfigured (e.g., 404, 403) -> delivery fails permanently after 3 attempts. Operator can see this in the "Recent deliveries" view and the audit log.
- Network isolation (e.g., no internet, Firewall rule blocks the destination) -> timeout after 10s, trigger retry. After 3 failures, record `WEBHOOK_DELIVERY_FAILED` with reason "timeout/unreachable".
- SSRF attempt (e.g., admin registers `http://127.0.0.1:22`) -> server-side guard rejects non-routable IPs (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, ::1, fc00::/7) with a clear error ("Webhook URL must be reachable from your network; local/private IPs are not allowed").

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: A webhook destination can be registered with URL, auth method, secret, and event class routing.
  **[unit: new `test_webhook_service.py`]**
- AC-2: Webhook URL must be HTTPS; HTTP is rejected with a clear error message.
  **[unit + integration]**
- AC-3: Webhook URL is validated to not be a private/loopback IP (SSRF guard).
  **[unit]**
- AC-4: Secret and custom headers are never logged in plaintext; audit events reference them only by presence/type.
  **[contract test asserting log scrubbing]**
- AC-5: A motion event triggers webhook delivery to registered destinations that filter for motion events.
  **[integration with mocked HTTP client]**
- AC-6: A camera-offline alert triggers webhook delivery to registered destinations that filter for offline events.
  **[integration]**
- AC-7: A storage-low alert triggers webhook delivery to registered destinations that filter for storage events.
  **[integration]**
- AC-8: An OTA outcome alert triggers webhook delivery to registered destinations that filter for OTA events.
  **[integration]**
- AC-9: Webhook delivery is retried up to 3 times with Fibonacci backoff (5s, 8s, 13s) on transient errors (5xx, timeout).
  **[unit with mocked time + HTTP]**
- AC-10: Permanent errors (4xx) fail immediately without retry.
  **[unit]**
- AC-11: Per-destination concurrency is capped at 1 in-flight request.
  **[integration with concurrent event triggers]**
- AC-12: HMAC-SHA256 signature is computed over the JSON payload and placed in `X-Webhook-Signature` header as `sha256=<hex>`.
  **[unit]**
- AC-13: Bearer token auth places the token in the `Authorization: Bearer <token>` header.
  **[unit]**
- AC-14: Custom headers are included in the request without modification.
  **[unit]**
- AC-15: Disabled webhooks do not receive delivery attempts.
  **[integration]**
- AC-16: Edit and delete operations update and remove webhooks correctly.
  **[unit + integration]**
- AC-17: Webhook delivery outcomes are recorded in audit log with HTTP status and response time.
  **[unit + integration]**
- AC-18: Delivery service recovers from network failures and continues processing subsequent events.
  **[integration with failure injection]**
- AC-19: Test-fire from the settings UI sends a synthetic payload and reports success/failure.
  **[integration]**
- AC-20: Admin cannot register a webhook with URL pointing to a private IP (SSRF mitigation).
  **[unit]**

## Non-Goals

- Inbound webhooks / external triggers (separate concern).
- Per-user webhook routing - system-level destinations only in v1.
- User-supplied payload templating beyond a fixed JSON schema.
- TLS pinning to specific receiver CAs. Standard CA chain verification only.
- Full MQTT broker support (market-backlog item #72; webhook-first keeps surface small).
- Webhook secret rotation (v1 uses static secrets; rotation is admin-delete + re-register).
- Conditional routing based on payload content (e.g., "only motion in Camera A"). v1 is event-type only.
- Webhook delivery history persistence (deliveries logged only in audit; no separate history table).

## Module / File Impact List

**New code:**

- `app/server/monitor/services/webhook_delivery_service.py` - outbound queue, bounded retry (Fibonacci backoff, max 3), HMAC-SHA256 signing, per-destination concurrency cap (1 in-flight), secret masking in logs. Pure business logic, no Flask imports.
- `app/server/monitor/api/webhooks.py` (new blueprint) - admin-only endpoints:
  - `POST /api/v1/webhooks` - register destination
  - `PUT /api/v1/webhooks/<id>` - edit destination
  - `DELETE /api/v1/webhooks/<id>` - delete destination
  - `PATCH /api/v1/webhooks/<id>/enabled` - toggle enabled state
  - `POST /api/v1/webhooks/<id>/test` - send test payload
  - `GET /api/v1/webhooks/deliveries` - recent delivery log (list, paginated)
- `app/server/tests/unit/test_webhook_service.py` - service-layer unit tests covering validation, signing, retry logic, secret masking.
- `app/server/tests/integration/test_webhook_delivery.py` - end-to-end tests: event trigger -> delivery, retry behavior, concurrency cap, audit logging.
- `app/server/tests/integration/test_webhook_ssrf.py` - SSRF guard tests (private IPs rejected).

**Modified code:**

- `app/server/monitor/models.py` - add to `Settings`:
  - `webhook_destinations: list[WebhookDestination] = field(default_factory=list)`
  - `webhook_delivery_history_retention_days: int = 30`
  - New dataclass `WebhookDestination`:
    - `id: str` (UUID)
    - `url: str`
    - `auth_type: Literal["none", "bearer", "hmac"] = "none"`
    - `secret: str = ""` (encrypted at rest, encrypted on disk like `User.totp_secret`)
    - `custom_headers: dict[str, str] = field(default_factory=dict)`
    - `event_classes: frozenset[str]` (e.g., `{"motion", "offline", "storage", "ota"}`)
    - `enabled: bool = True`
    - `created_at: str` (ISO-8601 Z)
    - `last_delivery_at: str | None = None`
    - `consecutive_failures: int = 0` (reset on success; used for degradation detection)
- `app/server/monitor/services/notification_policy_service.py` - call into `webhook_delivery_service.enqueue()` in addition to (not instead of) the existing browser-notification path. No change to the primary logic.
- `app/server/monitor/services/alert_center_service.py` - same: hook webhook delivery at alert emission time.
- `app/server/monitor/services/audit.py` - new audit event constants:
  - `WEBHOOK_REGISTERED`, `WEBHOOK_UPDATED`, `WEBHOOK_DELETED`, `WEBHOOK_ENABLED`, `WEBHOOK_DISABLED`
  - `WEBHOOK_DELIVERY_SUCCESS`, `WEBHOOK_DELIVERY_FAILED`, `WEBHOOK_DELIVERY_TRUNCATED`, `WEBHOOK_DELIVERY_DEGRADED`
  - Event detail must NOT include plaintext secrets; only "auth_type" is logged.
- `app/server/monitor/templates/settings.html` - new "Integrations -> Webhooks" card (list, add, edit, delete, test, recent deliveries).
- `app/server/monitor/static/css/style.css` - minor additions for table and form styling.
- `app/server/monitor/__init__.py` - wire `WebhookDeliveryService` into the app-factory; register new blueprint.

**Dependencies:**

- No new external dependencies (requests is already available for HTTP client; threading is stdlib).

**Out-of-tree:**

- No camera-side change.
- No Yocto recipe change.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | new contract tests for `/api/v1/webhooks/*` (CRUD, auth, SSRF) |
| Frontend / templates | browser-level check on `/settings` Integrations -> Webhooks card |
| Security-sensitive path | SSRF guard tests, secret masking tests, audit scrubbing tests |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Hardware behavior | deploy + `scripts/smoke-test.sh` row covering webhook registration, event delivery, failure recovery |

Smoke-test additions (Implementer to wire concretely):

- "Admin registers a webhook and receives motion events"
- "Webhook delivery retries on 5xx and succeeds on recovery"
- "Webhook URL protected from SSRF attempts"
- "Secret is not logged in audit"

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-239-1 | Admin misconfigures webhook auth (e.g., no secret when Bearer is selected) -> delivery fails silently and operator doesn't know. | Minor (operational) | Medium | RC-239-1: UI enforces required fields based on auth method (client-side); server-side validation also enforces. Settings UI shows "Recent deliveries" with HTTP status. |
| HAZ-239-2 | Webhook secret is logged in plaintext in audit log -> credential exposure. | Major (security) | Low | RC-239-2: secrets never passed to audit.log_event(); only "auth_type: bearer" is logged. Contract test enforces it. |
| HAZ-239-3 | Admin registers webhook to a private IP (127.0.0.1, 192.168.x.x) -> SSRF attack vector to local services. | Major (security) | Low | RC-239-3: server-side SSRF guard rejects non-routable IPs (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, ::1, fc00::/7) with a clear error. Tested explicitly. |
| HAZ-239-4 | Webhook destination down for days -> operator doesn't notice, downstream automation stays silent. | Minor (operational, depends on automation use case) | Medium | RC-239-4: audit log surfaces `WEBHOOK_DELIVERY_FAILED` events; after 5 consecutive failures per destination, emit `WEBHOOK_DELIVERY_DEGRADED`. Settings UI shows "Recent deliveries" with failure counts. |
| HAZ-239-5 | Retry storm if webhook destination is slow (e.g., replies after 30s) -> server spawns many concurrent requests, exhausts memory / FD limits. | Moderate (operational) | Low | RC-239-5: per-destination concurrency cap of 1 in-flight request; short timeout of 10s per request; Fibonacci backoff (5s, 8s, 13s) prevents cascade. |
| HAZ-239-6 | Large snapshot embedded in JSON payload -> out-of-memory on webhook server or payload too large to send. | Minor (depends on receiver capability) | Low | RC-239-6: max snapshot size configurable; payload truncated with audit warning if > 64 KB. Implementer to decide: embed or link-only (snapshot URL without base64 embedding). |
| HAZ-239-7 | Webhook destination replies with a redirect (302 -> another URL) -> SSRF via redirect chain. | Minor (security) | Low | RC-239-7: max 2 redirects; fail if more are attempted. Audit log notes redirect endpoints. |

Reference `docs/risk/` for the existing architecture risk register; this spec adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs):

- **Adds** outbound network calls to operator-specified destinations. Mitigates the risk of operator being locked into a single alert-delivery method (browser-push only today) by offering an open, verifiable outbound channel.
- **Adds** new persisted secret material: `WebhookDestination.secret` (plaintext equivalent of the bearer token or HMAC key). Treatment:
  - Stored in `Settings.webhook_destinations` on `/data` (LUKS-encrypted per ADR-0010) and not in the source tree.
  - **OPEN QUESTION**: encrypted at rest at the field level using the same pepper infrastructure ADR-0011 introduces (like `User.totp_secret`), or stored plaintext in the `/data` file? For MVP, plaintext is acceptable since `/data` is LUKS-only. Implementer to decide based on ADR-0011 status and threat model review.
  - Never logged in plaintext; audit events log only the auth type ("bearer", "hmac").
  - Never returned in API responses after creation (only "***" redacted marker).
- **Adds** short-lived signed delivery payloads (HMAC-SHA256). Signature key is derived from Flask `SECRET_KEY` via HKDF (distinct sub-key, never reuses the session key directly).
- **Sensitive paths touched:** `**/auth/**` (no direct change, but webhook delivery affects session/event surface), `**/secrets/**` (yes, new webhook-secret storage). Per `docs/ai/roles/architect.md` these need extra scrutiny - flagged here.
- **Audit:** every webhook registration, edit, delete, enable, disable, and delivery outcome is auditable (events listed above). Audit must NEVER carry plaintext secrets.
- **Outbound network security:** each request times out after 10s; retry budget is fixed (3 attempts, Fibonacci backoff); per-destination concurrency is capped at 1. This prevents the server from being used as a vector to DDoS external services.
- **SSRF control:** webhook URL must be HTTPS and must not resolve to a private/loopback IP. Validated on registration and on edit.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in `docs/traceability/traceability-matrix.md`):

- `UN-239` - User need: "I want to integrate my home-monitor alerts with Home Assistant / Node-RED / n8n without being locked into a single notification method."
- `SYS-239` - System requirement: "The system shall support outbound webhook delivery of selected event classes (motion, offline, storage, OTA) to operator-registered destinations with optional authentication (bearer, HMAC)."
- `SWR-239-A` ... `SWR-239-F` - Software requirements (one per functional area: registration, delivery, retry, auth, security, audit).
- `SWA-239` - Software architecture item: "Webhook delivery service in service-layer; Flask blueprint for CRUD; payload schema versioned; audit logging for all delivery outcomes."
- `HAZ-239-1` ... `HAZ-239-7` - listed above.
- `RISK-239-1` ... `RISK-239-7` - one per hazard.
- `RC-239-1` ... `RC-239-7` - one per risk control.
- `SEC-239-A` (webhook secret confidentiality), `SEC-239-B` (HMAC payload integrity), `SEC-239-C` (SSRF guard), `SEC-239-D` (audit completeness), `SEC-239-E` (outbound request rate limiting).
- `THREAT-239-1` (credential exposure via logs), `THREAT-239-2` (SSRF via webhook URL), `THREAT-239-3` (webhook secret in audit), `THREAT-239-4` (DoS vector via webhook delivery).
- `SC-239-1` ... `SC-239-N` - controls mapping to the threats above.
- `TC-239-AC-1` ... `TC-239-AC-20` - one test case per acceptance criterion above.

## Deployment Impact

- Yocto rebuild needed: **no** (no new external dependencies).
- OTA path: standard server image OTA. Migration on first boot of the new image: existing `Settings` records load with the new default (`webhook_destinations = []`, `webhook_delivery_history_retention_days = 30`) - dataclass defaults handle this.
- Hardware verification: yes - required. Register a webhook, trigger a motion event, verify delivery in audit log and "Recent deliveries" UI. Add to `scripts/smoke-test.sh`.
- Default state on upgrade: no webhooks registered; no delivery attempted. No operator impact on upgrade day.

## Open Questions

(None of these are blocking; design proceeds. Implementer captures answers in PR description.)

- OQ-1: Should webhook secrets be encrypted at rest (using ADR-0011 pepper) or stored plaintext in `/data/` (LUKS-only)? For MVP, plaintext is acceptable. Revisit if threat model demands field-level encryption later.
  **Recommendation**: plaintext in `/data/` for MVP; document residual risk if pepper is not yet in place.
- OQ-2: Should the webhook delivery service run in a background thread pool, or use asyncio + thread executor, or synchronously block on event emission? Current proposal is a background thread pool (bounded, separate from Flask's worker threads) to prevent event processing from being blocked by slow webhooks.
  **Recommendation**: thread pool with max 10 worker threads; use queue.Queue for FIFO ordering per destination.
- OQ-3: Should snapshot images be embedded in the JSON payload as base64, or just a URL reference? Embedding makes the webhook self-contained but risks large payloads (100+ KB for a 5MP snapshot).
  **Recommendation**: link-only (snapshot URL) for v1; revisit if operators request embedding after feedback.
- OQ-4: How should the delivery service persist retry state across restarts? If the server reboots mid-retry, in-flight requests are lost.
  **Recommendation**: best-effort only in v1; log as `WEBHOOK_DELIVERY_FAILED` on startup if a restart interrupted a delivery. Persistent retry queue is a v2 enhancement.
- OQ-5: Should admins be able to configure a global retry policy (backoff strategy, max retries) or is Fibonacci (5s, 8s, 13s) fixed?
  **Recommendation**: fixed for v1; if operators need tuning, add a Settings knob in v2.

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): routes thin, business logic in `WebhookDeliveryService`.
- Preserve modular monolith (ADR-0006): webhook delivery is a background queue + service, not a separate daemon.
- `/data` is the only place mutable runtime state lives (webhook destinations, delivery history).
- Do not block event processing on webhook delivery - use a background thread pool.
- Secret handling: never log plaintext; never return plaintext in API responses after creation; only return redacted ("***") marker.
- SSRF guard is non-negotiable: reject private/loopback IPs on registration.
- Audit must surface delivery failures so operators can debug missing automations.
- Tests + docs ship in the same PR as code, per `engineering-standards`.
