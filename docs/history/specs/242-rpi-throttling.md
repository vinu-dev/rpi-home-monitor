# Feature Spec: RPi Throttling Detection And Reporting

## Title

Detect and surface Raspberry Pi SoC throttling and under-voltage states.

## Problem

Silent SoC throttling is a production failure mode that local testing cannot expose.
When a Raspberry Pi is powered by a marginal PSU, undersized USB cable, or sustained heat,
the SoC silently caps ARM frequency, drops bitrate, skips frames, and makes motion detection
unreliable — yet the operator sees no warning. CPU temperature can read normal even while
under-voltage throttling is active (under-voltage throttle fires at ~4.63V regardless of die temp).

This violates the product mission anti-goal: "passing local tests while drifting from hardware reality."
The canonical RPi throttle signal (`vcgencmd get_throttled`) is the definitive source of truth and
must be surfaced to the operator.

## User Value

This is a trust-building feature. When an operator deploys a Pi on a questionable PSU and the
product clearly surfaces "throttled: under-voltage detected," they gain confidence that the system
is honest and observable, not silently degrading. It also enables rapid diagnosis of hardware
misconfigurations without manual SSH debugging.

## Context

**Existing health-monitoring infrastructure:**
- `app/camera/camera_streamer/health.py`: Monitors CPU temp, memory, ffmpeg, connectivity.
- `app/camera/camera_streamer/heartbeat.py`: Sends periodic HMAC-signed liveness updates to the server.
- `app/server/monitor/services/camera_service.py`: `accept_heartbeat()` updates camera health fields.
- `app/server/monitor/services/system_summary_service.py`: Aggregates camera health for dashboard summary.
- `app/server/monitor/services/notification_policy_service.py`: Emits alerts on state changes.
- OpenAPI contracts in `openapi/camera.yaml` and `openapi/server.yaml`.

**Existing patterns:**
- Hardware fault detection via `hardware_faults` list (ADR-0023), already in the heartbeat contract.
- Per-camera status UI on the dashboard showing health badges (CPU temp, free disk).
- Notification policy triggered on first sticky-bit state change (like existing offline alerts).

## Goal

1. **Collect** throttle state on the camera via `vcgencmd get_throttled` (with `/sys` fallback on non-standard Pi OS).
2. **Encode** the throttle bit-flags (under-voltage now/sticky, frequency-capped now/sticky, thermal-throttle now/sticky, soft-temp-limit now/sticky) in the heartbeat.
3. **Persist** throttle state in the camera record so the server tracks which sticky bits have been set since last boot.
4. **Surface** throttle state on the dashboard as an amber/red badge alongside the existing CPU-temp badge.
5. **Alert** the operator the first time any sticky throttle bit becomes set (via existing notification policy).

## User-Facing Behavior

### Entry point
- Camera health status + settings on the dashboard.
- Notification preferences (existing notification policy UI).

### Main flow
1. Camera detects throttling via `vcgencmd get_throttled`.
2. Camera reports throttle state in the next heartbeat.
3. Server persists throttle state in camera record.
4. Dashboard shows a throttle badge on the camera card (e.g., "⚠ Throttled: Under-voltage").
5. On first sticky-bit state change, operator receives an alert (email / push, per notification policy).

### Success state
- Operator quickly understands which Pi is throttled and what condition triggered it.
- Sticky-bit history is preserved across restarts so throttle events are not lost.

### Failure states
- **Non-Pi platform** (simulator, x86, future SBCs): `throttle_state` is `null`. No badge renders.
- **Command unavailable** (non-standard Pi OS, missing `vcgencmd`): Field remains `null`. No alert.
- **Transient read failure**: Last good value is retained until the next successful read.

### Edge cases
- Server restart while a Pi is already throttled → existing throttle state is not forgotten; only NEW sticky-bit transitions trigger an alert.
- Pi reboots (soft or hard) → sticky bits are cleared by the hardware; camera reports clean state; dashboard badge clears.
- Multiple throttle conditions active simultaneously → badge shows the highest-severity condition (e.g., "Critical: Under-voltage + Thermal").

## Acceptance Criteria

- **Heartbeat collection**: Camera reads throttle state every health check (15 seconds); format is stable (JSON object with per-condition now/sticky flags).
  - *Validation*: Unit test mocking `vcgencmd` and `/sys` paths; check payload structure.
- **Heartbeat transmission**: Throttle state is included in heartbeat JSON; backwards-compatible default is `null`.
  - *Validation*: Contract test verifying heartbeat schema change in `openapi/server.yaml`.
- **Server persistence**: `camera.throttle_state` is updated from heartbeat; sticky bits are only cleared on reboot (not on each heartbeat).
  - *Validation*: Unit test of `camera_service.accept_heartbeat()` with throttle payloads.
- **Dashboard badge**: Camera card displays a throttle badge when any condition is active (visible on list + detail views).
  - *Validation*: Browser smoke test showing throttle badge with mocked throttle data.
- **Dashboard persistence**: Throttle state survives server restart (persisted in `cameras.json`).
  - *Validation*: Integration test: heartbeat → server restart → verify stored throttle state.
- **Alert on first transition**: Operator receives one notification per sticky-bit condition per boot cycle (not repeatedly while throttled).
  - *Validation*: Unit test of notification policy trigger logic; integration test verifying alert is sent once.
- **Non-Pi graceful**: Non-Pi platforms report `null` throttle state; no errors, no badge.
  - *Validation*: Unit test disabling throttle collection on mock non-Pi platform.

## Non-Goals

- **Auto-mitigation**: Do NOT reduce bitrate, frame rate, or quality when throttled. This is a v2 feature.
- **Voltage/frequency live charts**: A sticky-bit summary plus the existing CPU-temp badge is sufficient for v1.
- **Non-Pi throttle detection**: Detecting under-voltage on x86 simulators, future SBCs, or cloud deployments. v1 is Pi-specific; other platforms report `null`.
- **Replacing CPU-temp warning**: Both signals are complementary. Keep the existing `cpu_temp > 80°C` warning.
- **Granular per-sticky-bit alerts**: Operator gets one alert when ANY sticky bit becomes set; drill-down happens on the dashboard.

## Module / File Impact

### Camera-side collection (app/camera/camera_streamer/)
- **health.py**: Add `read_throttle_state()` function (calls `vcgencmd get_throttled` with `/sys` fallback; returns dict of flags).
- **platform.py**: Add capability detection for throttle-state source (similar to existing thermal-path detection).
- **heartbeat.py**: Include `throttle_state` in heartbeat JSON payload.

### Server-side ingestion (app/server/monitor/)
- **openapi/server.yaml**: Add `throttle_state` field to the Camera schema (object with per-condition flags + timestamps).
- **services/camera_service.py**: `accept_heartbeat()` updates `camera.throttle_state`; logic to only clear sticky bits on detected reboot.
- **services/system_summary_service.py**: Compute camera health summary, treat throttle state as an error condition (amber/red health status).
- **services/notification_policy_service.py**: Emit an alert the first time `throttle_state` shows a new sticky bit set.

### Data model
- **Camera model** (`app/server/monitor/models/camera.py`): Add `throttle_state` field (JSON object or nullable).

### Frontend (app/server/monitor/templates/)
- **dashboard.html**: Render throttle badge on camera card (e.g., "⚠ Throttled: Under-voltage + Frequency").
- **settings.html** (if alerting preferences exist): No changes needed; use existing notification policy UI.

### Tests
- **app/camera/tests/unit/**: Unit tests for `read_throttle_state()` mocking `vcgencmd` and `/sys` paths.
- **app/server/tests/unit/**: Unit tests for heartbeat ingestion, sticky-bit logic, and notification trigger.
- **app/server/tests/contract/**: Contract test validating heartbeat payload against OpenAPI schema.
- **smoke test** (`scripts/smoke-test.sh`): Hardware verification with an actual throttled Pi (optional, manual).

## Validation Plan

| Area | Required validation | Who | When |
|------|---------------------|-----|------|
| Camera heartbeat payload | Contract test + unit mock | Implementer | Pre-PR |
| Server heartbeat ingestion | Unit test of `accept_heartbeat()` with throttle data | Implementer | Pre-PR |
| Sticky-bit state machine | Unit test: reboot detection, per-boot persistence | Implementer | Pre-PR |
| Dashboard badge render | Browser smoke (manual or E2E) with mocked throttle data | Implementer | Pre-PR |
| Notification trigger | Unit test + integration test (mock store + policy) | Implementer | Pre-PR |
| Yocto image build | `bitbake -p` if camera-side changes require new tools | Implementer | Pre-PR |
| Hardware smoke (optional) | Throttle a real Pi, verify heartbeat + dashboard + alert | Integrator | Post-merge |

See `docs/ai/validation-and-release.md` for the validation matrix.

## Risk Analysis

**Risk class**: LOW (additive read-only collection, backwards-compatible heartbeat extension, no new dependencies).

| Hazard | Severity | Probability | Proposed Control | Notes |
|--------|----------|-------------|------------------|-------|
| Malformed throttle state causes heartbeat rejection | Minor | Low | Heartbeat handler validates throttle payload type (dict/null); rejects non-dict, retains last good state. | No hazard to data or control. |
| Throttle badge shows outdated state across server restart | Minor | Very Low | Throttle state is persisted in camera record (cameras.json); no stale badge. | Dashboard reflects stored state. |
| Alert spam if sticky bits flap due to boundary conditions | Minor | Low | Sticky bits are hardware state, not software state; transition logic fires only on first bit set per boot. | Reboot clears hardware sticky bits. |
| `vcgencmd` unavailable on non-standard Pi OS or future images | Info | Medium | Graceful fallback to `/sys/devices/platform/soc/.../throttled`; if both unavailable, field is `null`. No alert. | Matches existing thermal-path pattern. |
| False negative: code exits when throttle check fails | Minor | Low | Wrap `read_throttle_state()` in try-except; log failure, return `null`. Never break the heartbeat. | Health monitor is non-critical; heartbeat must always send. |

**RISK: No impact on data persistence, auth, OTA, or device trust. No new external dependencies. No new daemon or long-lived background process.**

## Security Considerations

- **No new auth or secrets**: Throttle state is read-only system info, already visible in `vcgencmd get_throttled` on the Pi.
- **No data exposure**: Throttle flags are operator-facing diagnostics, not sensitive telemetry.
- **No new external dependencies**: Uses OS-provided `vcgencmd` or `/sys` paths.
- **Heartbeat HMAC unchanged**: Throttle field is added to existing signed heartbeat payload; signature verification unchanged.

## Traceability

### Requirements (placeholders for Implementer to fill)
- **SWR-###**: Camera heartbeat MUST include throttle state every 15 seconds.
- **SWR-###**: Server heartbeat handler MUST validate throttle payload type.
- **SWR-###**: Dashboard MUST render throttle badge when any condition is active.
- **SWR-###**: Notification policy MUST alert once per sticky-bit state change per boot.

### Architecture (placeholders)
- **SWA-###**: Throttle state collected in `health.py`, transmitted in `heartbeat.py`.
- **SWA-###**: Server-side state machine in `camera_service.accept_heartbeat()`.
- **SWA-###**: Dashboard health summary in `system_summary_service.py`.

### Risk (placeholders)
- **RISK-###**: Malformed throttle state handled by type validation.
- **RISK-###**: False negatives handled by exception safety in `read_throttle_state()`.

### Security (placeholders)
- **SEC-###**: Throttle state is read-only OS info; no new attack surface.

### Testing (placeholders)
- **TC-###**: Unit: `read_throttle_state()` with mocked vcgencmd + /sys.
- **TC-###**: Unit: heartbeat payload schema validation.
- **TC-###**: Unit: sticky-bit persistence across reboot detection.
- **TC-###**: Integration: alert triggered on first sticky-bit transition.
- **TC-###**: Smoke: dashboard badge visible and correct on real Pi.

## Deployment Impact

- **OTA required**: Camera-side code changes (new `read_throttle_state()` function, heartbeat JSON addition) require OTA.
- **Server rollout**: No breaking changes; server accepts heartbeats with or without throttle field (defaults to `null`).
- **Dashboard**: No mandatory UI redesign; badge is additive (appears next to existing health badges).
- **Backwards compatibility**: Old cameras (pre-#242) will not send throttle field; server treats as `null`. No errors.
- **Yocto changes**: If `vcgencmd` is not in the base image, a Yocto recipe must add it (or use `/sys` fallback with no-op if unavailable).

## Implementation Notes

### Camera-side platform detection
The throttle-state source depends on the platform. Use the existing `platform.py` capability detection (similar to `thermal_path`):
- Pi OS with `vcgencmd`: Use `vcgencmd get_throttled`.
- Non-standard Pi OS or older Raspberry Pi images: Fall back to `/sys/devices/platform/soc/fd012000.thermal/throttled` or similar.
- Non-Pi (x86 sim, other SBCs): Return `null`, no error.

### Bit-flag decoding
`vcgencmd get_throttled` returns a single 32-bit hex value. Example: `0x00050000` means:
- Bit 0 (1): Under-voltage now.
- Bit 1 (2): ARM frequency capped now.
- Bit 2 (4): Currently throttled.
- Bit 3 (8): Soft temperature limit now.
- Bits 16-19: Sticky versions of bits 0-3 (set at power-on or reboot).

Encode as a dict:
```json
{
  "under_voltage_now": false,
  "under_voltage_sticky": true,
  "frequency_capped_now": false,
  "frequency_capped_sticky": true,
  "throttled_now": false,
  "throttled_sticky": false,
  "soft_temp_limit_now": false,
  "soft_temp_limit_sticky": false,
  "last_updated": "2026-05-03T15:42:00Z"
}
```

### Sticky-bit logic on the server
When a heartbeat arrives with throttle state:
1. Compare `throttle_state.*.sticky` fields to the previously stored `camera.throttle_state.*.sticky` fields.
2. If any sticky bit is newly set (was false, now true), emit an alert.
3. Store the new throttle state.
4. On detected reboot (e.g., `uptime_seconds` drops significantly or firmware_version changes), clear sticky bits (hardware clears them on reboot).

### Dashboard badge rendering
Priority (highest to lowest):
- Critical: "Under-voltage" (highest-risk condition).
- Error: "Thermal throttle" or "Frequency capped".
- Warning: Any sticky bit set in the past.
- None: No throttle.

Render as a colored badge next to CPU temperature (e.g., "⚠ CPU 62°C | ⛔ Under-voltage").

## Open Questions

1. **Sticky-bit reset detection**: How reliably can we detect a Pi reboot to clear the sticky-bit state?
   - Option A: Use `uptime_seconds` drop (uptime goes from 10000 to 50 = reboot).
   - Option B: Compare `firmware_version` change (already done in heartbeat).
   - Option C: Both, with a grace period (e.g., if uptime < 60s, assume recent reboot).
   - **Decision deferred to Implementer**; document rationale in code.

2. **Alert frequency**: Should we surface the throttle state in the overall system health (amber system, red if under-voltage)?
   - Option A: Yes; throttle is a critical health signal.
   - Option B: No; throttle is a camera-specific alert; don't aggregate to system health.
   - **Tentatively A** (throttle is operator-critical), but Implementer may choose B if product wants a separation.

3. **Platform fallback priority**: If both `vcgencmd` and `/sys` are available, which takes precedence?
   - Option A: `vcgencmd` first (more reliable, officially supported).
   - Option B: `vcgencmd` only (simplify code).
   - **Decision: Use `vcgencmd` if available, else `/sys`, else `null`.** Simplicity preferred.

4. **Non-Pi platforms**: Should the spec add a stub for x86 simulator throttle detection (CPU frequency scaling on the host)?
   - **No** — v1 is Pi-specific. If x86 throttling is needed, a separate feature can add it.

## Implementation Checklist (for Implementer)

- [ ] Add `read_throttle_state()` to `app/camera/camera_streamer/health.py`.
- [ ] Add platform capability detection to `app/camera/camera_streamer/platform.py`.
- [ ] Update `app/camera/camera_streamer/heartbeat.py` to include throttle state.
- [ ] Update `openapi/server.yaml` with throttle_state schema.
- [ ] Update `app/server/monitor/models/camera.py` to store throttle_state.
- [ ] Update `app/server/monitor/services/camera_service.py` to ingest + persist throttle state.
- [ ] Add sticky-bit transition logic to `notification_policy_service.py`.
- [ ] Update `system_summary_service.py` to compute health summary from throttle state.
- [ ] Update dashboard templates to render throttle badge.
- [ ] Add unit tests (camera + server).
- [ ] Add contract test for heartbeat schema.
- [ ] Update `docs/guides/` with throttle alerting docs.
- [ ] Check Yocto recipes for `vcgencmd` availability; update if needed.
- [ ] Hardware smoke test on a real Pi (manual or CI if available).
- [ ] Fill in placeholder REQ / ARCH / RISK / SEC / TC IDs in traceability matrix.
