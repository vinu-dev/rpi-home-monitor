# Feature Spec: MQTT event bus for motion, camera state, system health, and snapshot commands

Tracking issue: #248. Branch: `feature/248-mqtt-event-bus`.

## Title

MQTT event bus for motion events, camera state, and system health (with bounded inbound snapshot command).

## Goal

Operators with an existing local-automation stack (Home Assistant, Node-RED, OpenHAB,
ioBroker) can point the monitor at a single MQTT broker on their LAN and receive
motion events, camera online/offline transitions, storage and OTA health updates in
real time, and publish a single bounded command (`cmd/snapshot`) back without the
server exposing arbitrary control. No cloud, no polling, no per-integration HTTP shim.

Concretely:

- An admin enables MQTT in Settings → Integrations → MQTT and supplies broker host,
  port, TLS toggle, username, password, client-id, and topic prefix (default
  `home-monitor`).
- On connect the server publishes a retained `home-monitor/system/status` document
  (`{state: "online", version, started_at}`) and one retained
  `home-monitor/camera/<id>/state` per known camera (`{state, last_seen, …}`).
- Motion events publish to `home-monitor/camera/<id>/motion` carrying the same
  schema-versioned payload the existing fan-out emits to webhooks
  (`schema_version`, `event_id`, `event_type`, `severity`, `timestamp`,
  `camera_id`, `camera_name`, `message`, `snapshot_url`, `metadata`).
- Camera offline / storage low / retention risk / OTA outcomes publish to
  per-class topics on the same prefix.
- The server's MQTT session declares a Last-Will-Testament (`state: "offline"`,
  retained) on `home-monitor/system/status` so subscribers see graceful and
  ungraceful disconnects.
- Inbound `home-monitor/camera/<id>/cmd/snapshot` is the only subscribed command
  in v1; it is rate-limited per camera and disabled by default so the integration
  surface starts as outbound-only.

This is item #72 (P1 W3) on `docs/history/planning/market-feature-backlog-100.md`
and is one of the three integration emitters Release 02 (`docs/history/releases/release-02.md`
§"Planning Rule") explicitly calls out as needing to share the existing event
publication model rather than ship a separate emitter. Per
`docs/ai/mission-and-goals.md`, MQTT is the lingua franca of the self-hosted
home-automation world; closing this gap is what unlocks "front camera motion →
kitchen light on" without webhook scraping.

## Context

Existing code this feature must build on (do not replicate):

- `app/server/monitor/services/webhook_delivery_service.py` — already classifies
  `motion`, `camera_offline`, `storage_low`, `ota_outcome` and builds the
  versioned JSON payload (`_build_payload`, `_snapshot_url_for_event`,
  `OTA_OUTCOME_EVENTS`). MQTT publishes the same payload from the same trigger
  points so we keep one schema, one fan-out, one set of audit codes per event.
- `app/server/monitor/services/audit.py` — `AuditLogger.add_listener()` is the
  push-style fan-out used today by the webhook service for camera_offline /
  storage_low / OTA. Reuse it; do not introduce a new pub-sub primitive.
- `app/server/monitor/api/cameras.py:455` — the motion-end call site that drives
  `webhook_delivery_service.enqueue_motion_event(event_id)`. This same site
  drives the new `mqtt_publisher.enqueue_motion_event(event_id)`. No new
  triggering hook.
- `app/server/monitor/services/alert_center_service.py` (`MOTION_NOTIFICATION_THRESHOLD`)
  — same gating threshold MQTT applies, so MQTT and webhooks agree on which
  motion events are "noteworthy enough to publish."
- `app/server/monitor/models.py` — `Settings` is the persisted config surface;
  MQTT broker fields land here next to `webhook_destinations`.
- `app/server/monitor/__init__.py` (app-factory, ADR-0001, ADR-0003) — wires
  `MqttPublisher` next to `WebhookDeliveryService` in `_init_infrastructure`,
  registers `mqtt_bp` blueprint at `/api/v1/mqtt`.
- `app/server/monitor/templates/settings.html` — `tab === 'webhooks'` cards are
  the structural template for an `tab === 'mqtt'` card.
- `meta-home-monitor/recipes-monitor/monitor-server/monitor-server_1.0.bb`
  RDEPENDS — `python3-paho-mqtt` is added here. `meta-python` is already in
  `LAYERDEPENDS_home-monitor` (`meta-home-monitor/conf/layer.conf`), so no new
  Yocto layer is pulled in.
- ADRs anchoring the choices: ADR-0003 (service-layer), ADR-0006 (modular
  monolith — MQTT is a thread inside the server, not a separate daemon),
  ADR-0010 (LUKS `/data`), ADR-0023 (unified fault framework — broker
  connectivity is a fault on the existing surface, not a brand-new "MQTT health"
  primitive).

## User-Facing Behavior

### Primary path — enable MQTT against a user-supplied broker

1. Admin opens Settings → Integrations → MQTT.
2. Page shows current state: disabled / disconnected / connected (broker host,
   client-id, last connected at, retained topics published) and a form.
3. Admin enters:
   - **Enable MQTT** (toggle, default off).
   - **Broker host** (required when enabled).
   - **Broker port** (default `1883`, `8883` when TLS toggled).
   - **Use TLS** (toggle; defaults off, defaults port to 8883 when toggled).
   - **Username** / **Password** (optional).
   - **Client ID** (default `home-monitor`).
   - **Topic prefix** (default `home-monitor`; rejects spaces, `#`, `+`, leading
     `/`, and anything that breaks MQTT topic-name rules).
   - **QoS** (0 or 1; default 1).
   - **Allow inbound snapshot command** (toggle, default off — see "Failure
     states" for why this matters).
4. On submit the server validates the form server-side, persists the new
   `Settings` slice, and triggers `MqttPublisher.reconfigure()`.
5. The publisher tears down the existing connection (if any), re-derives a fresh
   paho client, sets the LWT to `state: "offline"` retained on
   `<prefix>/system/status`, connects (with TLS if requested), publishes a
   retained `<prefix>/system/status` `online` document, publishes one retained
   `<prefix>/camera/<id>/state` per known camera, subscribes to
   `<prefix>/camera/+/cmd/snapshot` (only if the inbound toggle is on), and
   surfaces "Connected since …" in the UI.
6. Audit event `MQTT_CONFIG_UPDATED` records the change with the user, IP, and
   the masked broker URL — never the password.

### Primary path — outbound publish on a motion event

1. A camera ends a motion event; the existing motion-end call site
   (`app/server/monitor/api/cameras.py`) records the event, fires the alert
   center, fires the webhook service, and now also fires
   `mqtt_publisher.enqueue_motion_event(event_id)`.
2. The publisher gates on the same `MOTION_NOTIFICATION_THRESHOLD` the webhook
   path uses, builds the same payload (delegating to the existing
   `_build_payload` helper or a shared module), and publishes to
   `<prefix>/camera/<id>/motion` with QoS 1, retain=false.
3. If the broker is currently disconnected, the publisher drops the message,
   increments `mqtt_messages_dropped_disconnected`, and writes
   `MQTT_PUBLISH_DROPPED` to the audit log no more than once per
   `DROP_AUDIT_RATE_LIMIT_SECONDS` (60s). Motion delivery via webhooks and
   browser-push is unaffected — MQTT failures must never block other channels.

### Primary path — outbound state and health

- On every camera-state transition the existing `AuditLogger` writes
  `CAMERA_ONLINE` / `CAMERA_OFFLINE`. The publisher's audit listener republishes
  the resulting per-camera state to `<prefix>/camera/<id>/state` with
  `retain=true`, so a fresh subscriber sees the latest state on connect.
- `STORAGE_LOW`, `RETENTION_RISK`, and `OTA_*` audit entries publish to
  `<prefix>/system/storage` and `<prefix>/system/ota` with `retain=false` —
  the same trigger and severity mapping the webhook service already uses.
- On graceful shutdown (`MqttPublisher.close()`), the publisher publishes a
  retained `state: "offline"` to `<prefix>/system/status` and then
  disconnects. Ungraceful exit is covered by the LWT.

### Primary path — inbound snapshot command

1. Admin has explicitly enabled "Allow inbound snapshot command".
2. An external client publishes any payload (or empty) to
   `<prefix>/camera/<id>/cmd/snapshot`.
3. The publisher's command dispatcher matches `<id>` against the live camera
   registry. Unknown camera id → drop, write `MQTT_COMMAND_REJECTED` audit with
   reason `unknown_camera`, no broker reply.
4. Per-camera rate limit: at most 1 snapshot trigger per
   `SNAPSHOT_COMMAND_MIN_INTERVAL_SECONDS` (default 10) and at most
   `SNAPSHOT_COMMAND_BURST` (default 3) within `SNAPSHOT_COMMAND_BURST_WINDOW`
   (default 60s). Excess → drop, audit `MQTT_COMMAND_THROTTLED`.
5. Otherwise the publisher invokes the existing `CameraControlClient` snapshot
   primitive (or, if no such primitive exists at impl time, the same internal
   helper the `/api/v1/cameras/<id>/snapshot` endpoint already uses). The
   resulting snapshot's URL is published to
   `<prefix>/camera/<id>/snapshot/result` (retain=false) so the caller can pick
   it up. Audit `MQTT_COMMAND_HANDLED` with command=snapshot.

### Failure states (must be designed, not just unit-tested)

- **Broker unreachable on save** → form save still succeeds (config is
  persisted); UI shows "Connected: no — last error: timeout connecting to
  <host>:<port>". Audit `MQTT_CONNECT_FAILED`. Publisher enters reconnect loop
  with capped exponential backoff (1s → 2s → 4s → … max 60s, jitter ±20%).
- **Broker drops mid-run** → paho's auto-reconnect fires; publisher re-publishes
  the retained `system/status` and per-camera `state` snapshots on every
  successful (re)connect so subscribers can recover from a missed retained
  message. Outbound non-retained messages emitted while disconnected are
  dropped (see "primary path — outbound publish"). No internal queue: the
  webhook service is the durable channel; MQTT is best-effort by design.
- **TLS misconfigured** (CA mismatch, expired cert) → connect fails with a
  specific error string surfaced in the UI; audit `MQTT_CONNECT_FAILED:tls`.
  Publisher does not retry faster than the standard backoff.
- **Auth rejected** → broker returns CONNACK refused; publisher backs off and
  audits `MQTT_CONNECT_FAILED:auth`. UI shows "Authentication rejected". The
  publisher does not loop tight on auth failures (auth rejections back off the
  same as connect failures so we do not lock the broker out of its own client).
- **Topic prefix collision** (e.g., another device publishes retained on our
  prefix) → on connect we publish our authoritative retained state; we do not
  attempt to clear other publishers' retained messages. Documented as operator
  responsibility (broker ACL).
- **Inbound command on unknown camera id** → see primary path #3.
- **Inbound command flood** → see primary path #4. Rate-limit prevents
  snapshot-storms. After 50 throttled events in 5 minutes per camera, audit
  `MQTT_COMMAND_FLOOD_SUSPECTED` once and disable inbound snapshot for that
  camera id for 10 minutes (system-wide alert center entry; admin can re-enable
  by toggling the feature off and on).
- **Inbound command on a topic outside our explicit subscription set**
  (`<prefix>/camera/+/cmd/snapshot`) → ignored by paho (we never subscribed);
  no audit, no action.
- **Settings disabled mid-run** → `MqttPublisher.reconfigure()` publishes
  retained `state: "offline"` to `<prefix>/system/status`, calls
  `client.loop_stop()` + `client.disconnect()`, releases threads, and clears
  retained per-camera state by republishing each as
  `{state: "unknown", reason: "publisher_disabled"}` retained — so subscribers
  do not act on stale `online` retained messages.
- **Camera deleted while MQTT running** → `MqttPublisher` clears the camera's
  retained `state` topic by publishing an empty payload with `retain=true`
  (MQTT spec for deletion of retained messages), per-camera. Driven from the
  existing camera-delete audit event.
- **paho client thread crash** → caught at the worker boundary, logged at
  WARNING, audited as `MQTT_INTERNAL_ERROR` no more than once per 60s. The
  publisher's supervisor restarts the loop. We never propagate paho exceptions
  into the request path.

## Acceptance Criteria

Each bullet is testable; verification mechanism is in brackets.

- AC-1: Admin can save broker host, port, TLS, username, password, client-id,
  topic prefix, QoS, and inbound-snapshot toggle from Settings → MQTT.
  **[unit + integration]**
- AC-2: Form server-side validates topic prefix (no `#`, `+`, leading `/`,
  whitespace; ≤120 chars).
  **[unit]**
- AC-3: TLS toggle defaults port to 8883 only on the form; the persisted port
  remains whatever the operator set.
  **[unit]**
- AC-4: Settings save audits `MQTT_CONFIG_UPDATED` with broker host (no
  password, no username).
  **[unit, contract test asserting log scrubbing]**
- AC-5: Enabling MQTT triggers `MqttPublisher.reconfigure()` which connects to
  the broker and publishes retained `<prefix>/system/status` `online` with
  `version` and `started_at`.
  **[integration with embedded test broker or stub]**
- AC-6: On connect the publisher publishes one retained
  `<prefix>/camera/<id>/state` per known camera with the camera's current
  state (`online | offline | pending`).
  **[integration]**
- AC-7: A motion event with `peak_score >= MOTION_NOTIFICATION_THRESHOLD`
  publishes the same payload schema as the webhook service to
  `<prefix>/camera/<id>/motion` with `retain=false`, QoS 1.
  **[unit + integration]**
- AC-8: A motion event with `peak_score < MOTION_NOTIFICATION_THRESHOLD` does
  not publish to MQTT.
  **[unit]**
- AC-9: `CAMERA_ONLINE` / `CAMERA_OFFLINE` audit events publish updated
  retained state to `<prefix>/camera/<id>/state`.
  **[unit + integration]**
- AC-10: `STORAGE_LOW`, `RETENTION_RISK`, and `OTA_*` audit events publish to
  the documented system topics, with the same severity mapping the webhook
  service uses.
  **[unit]**
- AC-11: Last-Will-Testament publishes retained `state: "offline"` to
  `<prefix>/system/status` on ungraceful disconnect.
  **[integration with broker stub]**
- AC-12: Graceful shutdown publishes retained `state: "offline"` and
  disconnects cleanly (no TCP RST).
  **[integration]**
- AC-13: Disabling MQTT clears retained per-camera state by publishing an
  empty payload with `retain=true` for each camera.
  **[unit + integration]**
- AC-14: When the broker is disconnected, motion / health events are dropped
  rather than queued, and webhook + browser-push delivery is unaffected.
  **[integration with failure injection]**
- AC-15: Drop events are audited as `MQTT_PUBLISH_DROPPED` no more than once
  per 60s per category.
  **[unit with mocked clock]**
- AC-16: Reconnect uses capped exponential backoff (1, 2, 4, 8, 16, 32, 60s)
  with ±20% jitter; auth-rejected errors back off the same as connect failures.
  **[unit with mocked sleep]**
- AC-17: Inbound `<prefix>/camera/<id>/cmd/snapshot` triggers a snapshot only
  when the inbound toggle is on.
  **[integration]**
- AC-18: Inbound snapshot command on unknown camera id is rejected and audited
  as `MQTT_COMMAND_REJECTED:unknown_camera`.
  **[unit]**
- AC-19: Inbound snapshot command exceeds rate limit
  (`SNAPSHOT_COMMAND_MIN_INTERVAL_SECONDS` or burst window) → throttled,
  audited as `MQTT_COMMAND_THROTTLED`, no snapshot taken.
  **[unit + integration]**
- AC-20: Sustained snapshot flood on a camera id (≥50 throttled events in
  5min) → publisher disables inbound for that id for 10 minutes and audits
  `MQTT_COMMAND_FLOOD_SUSPECTED`.
  **[unit]**
- AC-21: MQTT publishing failures (broker unreachable, TLS error, auth error)
  do not raise into request handlers or block the alert/notification pipeline.
  **[integration with failure injection]**
- AC-22: Broker password is never logged in audit; `auth_present: bool` is the
  only credential trace.
  **[contract test asserting log scrubbing]**
- AC-23: With TLS enabled, the publisher uses the system CA bundle by default
  and does not disable certificate verification under any code path.
  **[unit asserting paho.tls_set defaults; lint guard against
  `cert_reqs=ssl.CERT_NONE`]**
- AC-24: Settings UI surfaces connection status, last error, and counts of
  retained-snapshot, dropped-while-disconnected, and throttled-command events.
  **[manual + integration on `/api/v1/mqtt/status` JSON shape]**

## Non-Goals

- Embedded broker auto-provisioning. v1 ships "point at your broker"; bundling
  Mosquitto with auth/ACL set-up is a follow-up (issue body §Out of scope).
- Home Assistant MQTT discovery payloads
  (`homeassistant/<component>/<id>/config`). Separate ticket; depends on this
  landing first.
- Two-way camera control beyond the snapshot command (arm/disarm, recording-mode
  changes, schedule edits). Each of those is its own design.
- TLS mutual-auth and broker-side ACL configuration. That is the operator's
  broker config, not ours.
- Retained-message recovery beyond the publisher's own state. We do not attempt
  to clear retained messages other publishers may have left on our prefix.
- A new internal pub-sub primitive. The audit-listener fan-out is sufficient;
  do not invent a second event bus inside the server.
- Persistent in-process MQTT message queue across publisher restarts. Webhook
  service remains the durable channel; MQTT is best-effort.
- HTTPS endpoint discovery for `snapshot_url` from outside the LAN. The URL we
  publish is the same one the webhook channel publishes today.

## Module / File Impact List

**New code:**

- `app/server/monitor/services/mqtt_publisher.py` — paho-mqtt client lifecycle,
  reconnect with backoff, retained `system/status` and per-camera `state`
  topics, audit listener for `CAMERA_ONLINE/OFFLINE/STORAGE_LOW/RETENTION_RISK
  /OTA_*`, motion enqueue path, command dispatcher with per-camera rate limit
  and flood detector. Pure business logic, no Flask imports. Reuses the
  payload-build helpers from `webhook_delivery_service` (refactor minimally to
  expose `_build_payload`, `_snapshot_url_for_event`, `OTA_OUTCOME_EVENTS` from
  a shared module if needed; otherwise import via a thin re-export).
- `app/server/monitor/api/mqtt.py` — admin-only blueprint:
  - `GET /api/v1/mqtt/config` — return current config (password redacted).
  - `PUT /api/v1/mqtt/config` — replace config (validates, persists, calls
    `mqtt_publisher.reconfigure()`).
  - `GET /api/v1/mqtt/status` — `{connected, last_error, last_connected_at,
    retained_topics_count, dropped_disconnected, throttled_commands,
    inbound_disabled_until_per_camera}`.
  - `POST /api/v1/mqtt/test` — best-effort connect-only ping with a 5s
    timeout; never persists, never publishes; returns `{ok, error}`.
- `app/server/tests/unit/test_mqtt_publisher.py` — service unit tests
  (validation, payload build, gating, retained snapshot, command rate limit,
  flood detector, drop-while-disconnected accounting, audit codes, log
  scrubbing).
- `app/server/tests/integration/test_mqtt_publisher.py` — end-to-end against
  an in-process broker stub or `pytest-mqtt` fixture: motion event →
  publish, camera state transitions → retained republish, LWT on TCP drop,
  reconnect after broker restart, inbound snapshot dispatch with rate limit.
- `app/server/tests/integration/test_api_mqtt.py` — blueprint contract tests:
  CSRF, admin gating, payload validation, status shape, password-never-returned.
- `app/server/tests/contracts/test_api_contracts.py` — extend with the new MQTT
  endpoints' contract assertions.

**Modified code:**

- `app/server/monitor/models.py` — add to `Settings`:
  - `mqtt_enabled: bool = False`
  - `mqtt_broker_host: str = ""`
  - `mqtt_broker_port: int = 1883`
  - `mqtt_use_tls: bool = False`
  - `mqtt_username: str = ""`
  - `mqtt_password: str = ""` (storage policy: see Open Questions OQ-1; default
    is plaintext on `/data` LUKS, matching today's webhook secret handling)
  - `mqtt_client_id: str = "home-monitor"`
  - `mqtt_topic_prefix: str = "home-monitor"`
  - `mqtt_qos: int = 1`
  - `mqtt_inbound_snapshot_enabled: bool = False`
  Defaults are chosen so the feature is fully off on upgrade.
- `app/server/monitor/__init__.py` — instantiate `MqttPublisher` next to
  `WebhookDeliveryService` in `_init_infrastructure`; if `mqtt_enabled` is
  True at startup, call `start()`. Register `mqtt_bp` blueprint at
  `/api/v1/mqtt`.
- `app/server/monitor/api/cameras.py:455` — add a single line that fires
  `current_app.mqtt_publisher.enqueue_motion_event(event_id)` next to the
  existing webhook enqueue, with the same try/except no-op guard. No change
  to ordering or upstream logic.
- `app/server/monitor/services/audit.py` — new event constants:
  - `MQTT_CONFIG_UPDATED`
  - `MQTT_CONNECT_FAILED`
  - `MQTT_PUBLISH_DROPPED`
  - `MQTT_COMMAND_REJECTED`, `MQTT_COMMAND_HANDLED`, `MQTT_COMMAND_THROTTLED`,
    `MQTT_COMMAND_FLOOD_SUSPECTED`
  - `MQTT_INTERNAL_ERROR`
  Audit detail must NEVER include the broker password.
- `app/server/monitor/templates/settings.html` — new MQTT card patterned on
  the existing Webhooks card (add a `tab === 'mqtt'` block + sidebar button;
  Alpine state slice mirrors the webhooks slice).
- `app/server/monitor/static/css/style.css` — minor additions only if the
  webhooks card styles do not already cover the layout.
- `app/server/requirements.txt` — add `paho-mqtt>=1.6,<3` (1.6 ships
  callback API v1; 2.x changes the callback signatures, so pin <3 until we
  decide to migrate).
- `app/server/requirements.lock` — refresh with the resolved paho version.
- `meta-home-monitor/recipes-monitor/monitor-server/monitor-server_1.0.bb` —
  add `python3-paho-mqtt` to RDEPENDS. `meta-python` is already in
  `LAYERDEPENDS_home-monitor`, so no new layer.

**Out-of-tree:**

- No camera-side change. The camera continues to talk mTLS-RTSP/HTTP to the
  server; MQTT is server-side only.
- No new Yocto layer.
- No nginx change.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md` "Validation Matrix":

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | new contract tests for `/api/v1/mqtt/{config,status,test}` (admin gating, CSRF, password redaction) |
| Frontend / templates | browser-level check on `/settings` Integrations → MQTT card |
| Security-sensitive path | broker-credential masking tests, audit scrubbing tests, TLS-default-on-when-toggled tests, inbound rate-limit tests, flood-suspected disable-window test |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Yocto config or recipe | `bitbake -p` for `monitor-server` to confirm the `python3-paho-mqtt` RDEPENDS resolves; VM image build for `home-monitor-image` |
| Hardware behavior | deploy + `scripts/smoke-test.sh` rows below |

Smoke-test additions (Implementer to wire concretely):

- "Admin enables MQTT against a Mosquitto on the LAN and the dashboard shows Connected"
- "External subscriber sees retained `system/status` and per-camera `state` on first subscribe"
- "Motion event publishes to `camera/<id>/motion` with the documented schema"
- "Killing TCP at the broker triggers LWT and operator sees offline state"
- "External publish to `cmd/snapshot` with the inbound toggle off does not trigger a snapshot"
- "External publish at >0.1 Hz to `cmd/snapshot` is throttled and after 50 throttled events the camera id is disabled for 10 minutes"

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-248-1 | Broker credential (password) leaks via audit log or API response. | Major (security) | Low | RC-248-1: password never passed to `audit.log_event()`; `auth_present: bool` only. Password redacted in `GET /api/v1/mqtt/config`. Contract test enforces both. |
| HAZ-248-2 | Publisher tight-loops reconnect on auth-rejected → broker locks the client out. | Moderate (operational) | Medium | RC-248-2: capped exponential backoff (1→60s, ±20% jitter), same on auth-rejected as on connect-failed. Tested with mocked sleep. |
| HAZ-248-3 | MQTT disconnect blocks motion/alert/notification pipeline. | Major (mission) | Low | RC-248-3: `mqtt_publisher.enqueue_motion_event` and audit listener are best-effort, wrapped in try/except no-op at the call sites; webhook + browser-push are independent. Failure-injection integration test. |
| HAZ-248-4 | Inbound snapshot command is abused to mass-trigger camera I/O (LAN attacker or misconfigured automation). | Moderate (operational + privacy) | Medium | RC-248-4: feature default-off, per-camera rate limit (1/10s, burst 3/60s), flood-suspected auto-disable for 10min, audit at every gate. |
| HAZ-248-5 | Subscriber acts on stale `online` retained state after publisher disabled or camera deleted. | Minor (operational) | Medium | RC-248-5: on disable publish `state: "unknown"` retained per camera; on camera-delete clear the retained slot with empty payload retained. AC-13 + camera-delete integration test. |
| HAZ-248-6 | TLS turned on but certificate verification disabled, allowing MITM on the broker connection. | Major (security) | Low | RC-248-6: TLS path uses system CA bundle and `cert_reqs=ssl.CERT_REQUIRED`. Lint guard greps for `CERT_NONE` in `mqtt_publisher.py`; AC-23 unit test asserts. |
| HAZ-248-7 | Retained-topic poisoning by another publisher on shared prefix gives operator a misleading view. | Minor (operational) | Low | RC-248-7: documented as operator's broker-ACL responsibility; we always publish authoritative state on (re)connect so our own picture is correct. README + Settings hint text. |
| HAZ-248-8 | Snapshot command result leaks a guessable `snapshot_url` to a broker shared with non-trusted subscribers. | Minor (privacy) | Medium | RC-248-8: same URL the webhook channel already exposes (no new leakage); admin warned in Settings UI that any MQTT subscriber can read motion/snapshot topics. Recommend operator broker ACLs. |
| HAZ-248-9 | paho thread crash leaves the publisher in a wedged state — no publishes, no audit, operator unaware. | Moderate (operational) | Low | RC-248-9: supervisor catches at worker boundary, restarts loop, audits `MQTT_INTERNAL_ERROR` (rate-limited) and surfaces "last error" in Settings status JSON. |

Reference `docs/risk/hazard-analysis.md` for the existing register; this spec
adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs in
`docs/cybersecurity/threat-model.md`):

- **Adds** outbound TCP (1883/8883) to an operator-specified broker. New
  attack surface is small (single long-lived connection, single client
  identity).
- **Adds** persisted credential material: `Settings.mqtt_password`. Treatment:
  - Stored in `Settings` on `/data` (LUKS-encrypted per ADR-0010), not in
    source.
  - Never logged in plaintext; audit events log only `auth_present: bool`.
  - Never returned in API responses (only `"***"` redacted marker).
  - **OPEN QUESTION** (OQ-1): field-level encryption with the ADR-0011 pepper
    or plaintext on `/data`? For MVP, plaintext on `/data` matches today's
    `WebhookDestination.secret` policy and the webhook spec's recommendation;
    revisit if pepper infra is in place at impl time.
- **Adds** an inbound subscription (`<prefix>/camera/+/cmd/snapshot`). Off by
  default. When enabled, the only command is `snapshot`. No code execution,
  no recording-mode change, no config write.
- **Adds** a rate-limited inbound dispatcher with auto-disable on flood
  detection. This bounds the worst-case I/O on the camera and prevents a
  malicious or buggy LAN client from acting as a snapshot DoS.
- **Sensitive paths touched:** `**/auth/**` no direct change; `**/secrets/**`
  yes (broker password storage); per `docs/ai/roles/architect.md` flagged here
  for extra scrutiny.
- **No `**/.github/workflows/**` change.**
- **TLS:** when `mqtt_use_tls` is true, the publisher uses the system CA bundle
  and certificate verification is required. The codepath that disables
  certificate verification does not exist; lint guard prevents introduction.
- **Audit:** every config update, connect failure, drop, command outcome
  (handled, rejected, throttled, flood-suspected, internal error) is auditable.
  Audit must NEVER carry the broker password.
- **No CORS / no public surface.** `/api/v1/mqtt/*` is admin-only via the
  existing `@admin_required` decorator and CSRF-protected like the rest of the
  settings API. `mqtt_inbound_snapshot_enabled` is the only knob that opens an
  inbound surface, and it is gated by the broker's auth/ACL — i.e., the
  operator's existing trust boundary, not a new one we manage.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-248` — User need: "I want my home-monitor to participate in my existing
  MQTT-based home automation without me having to write a webhook bridge."
- `SYS-248` — System requirement: "The system shall publish motion, camera
  state, and system-health events to a configurable MQTT broker, and shall
  optionally accept a single bounded snapshot command on a documented topic."
- `SWR-248-A` … `SWR-248-G` — Software requirements (one per area: config,
  outbound publish, retained state, LWT, command dispatch, rate limit,
  audit/observability).
- `SWA-248` — Software architecture item: "MQTT publisher in service-layer;
  Flask blueprint for config + status; payload schema shared with webhook
  service; retained `<prefix>/system/status` + per-camera `state`; LWT for
  ungraceful disconnect."
- `HAZ-248-1` … `HAZ-248-9` — listed above.
- `RISK-248-1` … `RISK-248-9` — one per hazard.
- `RC-248-1` … `RC-248-9` — one per risk control.
- `SEC-248-A` (broker credential confidentiality), `SEC-248-B` (TLS
  cert-verification mandatory), `SEC-248-C` (inbound rate limit + flood
  auto-disable), `SEC-248-D` (audit completeness), `SEC-248-E` (best-effort,
  non-blocking integration with notification pipeline).
- `THREAT-248-1` (broker password exposure via logs), `THREAT-248-2` (MITM via
  disabled TLS verification), `THREAT-248-3` (snapshot DoS via inbound
  command), `THREAT-248-4` (stale retained state misleads operator).
- `SC-248-1` … `SC-248-N` — controls mapping to the threats above.
- `TC-248-AC-1` … `TC-248-AC-24` — one test case per acceptance criterion.

## Deployment Impact

- **Yocto rebuild needed: yes.** `python3-paho-mqtt` is added to
  `monitor-server` RDEPENDS. `meta-python` is already in `LAYERDEPENDS`, so no
  new layer; rebuild and re-image required to pick up the dependency on
  hardware. CI must run `bitbake -p` for `monitor-server` and a VM image
  build of `home-monitor-image`.
- **OTA path:** standard server image OTA (ADR-0008 A/B rollback). Migration
  on first boot: existing `Settings` records load with the new defaults
  (`mqtt_enabled = False`, all broker fields empty) — dataclass defaults
  handle it. No operator impact on upgrade day.
- **Hardware verification: yes — required.** Smoke rows above.
- **Default state on upgrade:** MQTT disabled, no broker configured, no
  retained topics published, no inbound subscription. Operator opt-in only.

## Open Questions

(None blocking; design proceeds.)

- OQ-1: Should `Settings.mqtt_password` be encrypted at rest using ADR-0011's
  pepper (like `User.totp_secret`) or stored plaintext on `/data` (LUKS-only)
  to mirror today's `WebhookDestination.secret`?
  **Recommendation:** plaintext on `/data` for MVP — same residual risk as
  the existing webhook-secret handling. Document and revisit if pepper infra
  is wired in at impl time.
- OQ-2: Is `paho-mqtt` 2.x worth pinning to (callback API v2, async-ready) or
  do we stay on 1.6 (callback v1) for now?
  **Recommendation:** pin `>=1.6,<3` and write the publisher against
  callback API v1; 2.x's signature break can be a separate follow-up. We
  do not need any 2.x-only feature.
- OQ-3: Should we expose more inbound commands in v1 (arm/disarm,
  recording-mode toggle)?
  **Recommendation:** no. Snapshot is the safest, lowest-risk command and
  the one operators ask for first. Anything that mutates configuration
  deserves its own design discussion (per issue body §Out of scope).
- OQ-4: Should the publisher honor MQTT v5 features (per-message
  expiry, response topics) or stay v3.1.1?
  **Recommendation:** v3.1.1. Universally supported by Mosquitto, EMQX,
  HiveMQ, Home Assistant's bundled broker. v5 is a v2 enhancement.
- OQ-5: Should retained `system/status` include the server's IP / Tailscale
  hostname?
  **Recommendation:** no. Hostname is enough for routing; IP leaks topology
  to anyone with broker access.
- OQ-6: Do we need a "panic disable" — global kill switch independent of the
  Settings toggle?
  **Recommendation:** no. The Settings toggle is the kill switch; reachable
  via the same admin-only path that already controls webhooks.

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): routes thin, business logic in
  `MqttPublisher`. Routes are HTTP adapters that call publisher methods.
- Preserve modular monolith (ADR-0006): MQTT runs as a thread inside the
  server process, not a separate daemon. paho's threaded client is fine; do
  not introduce a new process or container.
- `/data` is the only place mutable runtime state lives (broker config in
  `Settings`).
- Do not block event processing on MQTT publishes — every fan-out call site
  uses try/except no-op; webhook delivery is the durable channel.
- Reuse the existing payload helpers (`_build_payload`,
  `_snapshot_url_for_event`, `OTA_OUTCOME_EVENTS`,
  `MOTION_NOTIFICATION_THRESHOLD`). If a small refactor is needed to expose
  them from a shared module instead of `webhook_delivery_service`, do that
  refactor in this PR — but do not invent a parallel payload schema.
- Reuse `AuditLogger.add_listener` for `CAMERA_ONLINE/OFFLINE/STORAGE_LOW
  /RETENTION_RISK/OTA_*` fan-out. Do not introduce a second pub-sub primitive.
- Secret handling: never log plaintext password; never return password in API
  responses; only return redacted `"***"` marker. Same policy as
  `WebhookDestination.secret`.
- TLS verification is non-negotiable. The codepath that sets
  `cert_reqs=ssl.CERT_NONE` does not exist; CI / lint should enforce.
- Inbound dispatcher is default-off and rate-limited; the only command in v1
  is `snapshot`. Do not extend the command set in this PR.
- Audit must surface every meaningful state change so operators can debug
  silent automations.
- Tests + docs ship in the same PR as code, per
  `docs/ai/engineering-standards.md`.
