# ADR-0023: Unified Fault + Event Framework

**Status**: Proposed — 2026-04-21
**Supersedes**: the flat `hardware_ok` / `hardware_error` + loose `hardware_faults` list added to the Camera model in v1.3.0.

## Context

We have three different "something went wrong" surfaces, each with their own shape, storage, and UI:

1. **AuditLogger** — append-only JSON event log (`LOGIN_FAILED`, `OTA_COMPLETED`, …). One-shot, immutable, historical.
2. **MotionEvent** — typed record with a real lifecycle (`started_at` → `ended_at`) + clip correlation.
3. **`Camera.hardware_ok` / `hardware_error`** (v1.3.0) — a per-heartbeat state snapshot. No history, no severity, no way to distinguish "sensor missing" from "thermal throttling" on the server dashboard.

v1.3.0's flat fields are enough for one fault (no camera sensor) but don't generalise. Next we need storage-low, thermal-throttling, server-unreachable, OTA-failed, LUKS-opt-in-pending — each with different severity, different subject (camera vs server), and different "how do I fix it" text. Bolting them onto the same two string fields won't scale.

## Decision

Introduce a **Fault** record as a first-class domain concept alongside existing **Events**. Keep them distinct but linked.

```
Fault                                      Event (AuditLogger entry)
─────                                      ─────────────────────────
id          stable UUID                    timestamp   ISO-8601 UTC
code        string  (stable catalogue)     event       enum (LOGIN_SUCCESS, …)
severity    info|warning|error|critical    user        actor username
subject     {type: "camera"|"server"|…,    ip          actor IP
             id: <camera_id | "server">}   detail      free text
message     short label  (≤ 30 chars,      fault_id    (new) reference
             fits a card badge)                        when the event
hint        actionable one-liner                        describes a fault
             (tooltip / drill-in)                       transition
opened_at   ISO-8601 UTC
resolved_at ISO-8601 UTC | null  (null = active)
context     dict  (device paths, numbers, …)
```

### Relationships

- When a fault **opens**, the `FaultService` emits a matching audit event `FAULT_OPENED` with `fault_id` + `fault_code`. Ditto `FAULT_RESOLVED` when it closes. This gives us the **history** trail without duplicating storage: current state lives in `/data/faults.json`, history lives in the existing audit log.
- Motion events stay as-is. They're observations of the real world, not faults.

### Storage

| What | Where | Cardinality |
|---|---|---|
| Active faults | `/data/faults.json` (single file, atomic writes via `tempfile.mkstemp` + `os.replace`) | O(tens) |
| Fault history | `/data/logs/audit.log` — existing file, existing rotation | Unbounded (rotated) |

JSON file, not SQLite — same argument as the rest of the codebase (cardinality is tiny, human-inspectable matters more than query speed).

### Catalogue

Codes are defined in **one** module shared camera-side + server-side: `camera_streamer/faults.py` on the camera, mirrored via a thin wrapper on the server. Each code has:

- stable identifier (`camera_sensor_missing`)
- default severity (can be overridden per-instance)
- default `message` + `hint` copy (operators can translate later without touching code)

Renaming a code is a breaking change — deprecate by emitting both old + new for one release, then drop.

### Wire protocol

Heartbeat payload gains a single field (v1.3.x → v1.4):

```json
"faults": [
  {
    "code": "camera_sensor_missing",
    "severity": "error",
    "message": "Camera sensor missing",
    "hint": "Check ribbon cable and /boot/config.txt ...",
    "context": {"device": "/dev/video14"}
  }
]
```

Server reconciles by camera-id: any fault on the previous snapshot that isn't in the new payload is auto-resolved (emits `FAULT_RESOLVED` event, sets `resolved_at`).

v1.3.0's `hardware_ok` + `hardware_error` stay populated (derived from `faults`) for backward compat, removed in v1.5.

### API

```
GET    /api/v1/faults                        → all active faults across all subjects
GET    /api/v1/faults?subject=camera&id=…    → filter
GET    /api/v1/faults/history?limit=50       → paginated closed faults via audit log
POST   /api/v1/faults/{id}/acknowledge       → admin only (mute without resolving)
```

Camera object endpoints (`GET /api/v1/cameras` + `GET /api/v1/cameras/{id}`) embed the active faults list for that camera so the dashboard doesn't need a second request.

### UX rules

1. **Camera stays online** even when faulted. Online ≠ healthy.
2. **Compact badge** on the dashboard card, right next to the ONLINE pill. Icon + short `message`. Colour keyed off severity (info = grey, warning = amber, error = red, critical = pulsing red).
3. **Hover reveals `hint`** — no click to see the actionable advice.
4. **Tier-1 status strip** aggregates: green = "no active faults"; amber/red = "N active faults — review" with deep link to `/faults`.
5. **Dedicated `/faults` page** lists everything grouped by subject, with open-at and hint. Click a fault row → jumps to the relevant settings section.
6. **Audit log** remains the timeline: "OV5647 sensor missing on cam-d8ee since 22:15" appears there the moment it opens and again when it resolves.

## Consequences

**Good**

- One conceptual model covers sensor missing, storage low, thermal throttling, server-unreachable, OTA failed, and whatever comes next.
- Dashboard gets a real signal to key off ("cameras online, but 2 faults active") — the v1.3.0 "All systems normal — 3/3 online" misleading text goes away automatically.
- Free history trail via the existing audit log; no new storage tier.
- Camera-side and server-side share one fault catalogue file → no drift.

**Bad / accepted**

- New domain object to test + document; small API surface to keep current.
- Migration step — the flat `hardware_ok` / `hardware_error` fields persist in the serialised Camera JSON for a release or two, marked deprecated.

## Scope — v1.4.0 slice

- Ship the `Fault` record + `FaultService` on the server.
- Camera emits `faults[]` in heartbeat (already in flight; just swap naming + fields to match this ADR).
- Dashboard: compact badge + hover tooltip on the card. No `/faults` page yet.
- Status strip aggregation.
- Codes live this release: `camera_sensor_missing`, `camera_h264_unsupported`, `server_unreachable` (camera side only; server→camera TBD), `storage_low`.

Deferred to v1.4.1:

- `/faults` page.
- Acknowledge (mute) endpoint.
- Thermal + OTA fault integrations.

## References

- `app/camera/camera_streamer/faults.py` (introduced in the hardware-status slice; to be renamed + reshaped per this ADR)
- `app/server/monitor/services/audit.py` (existing event log, will gain FAULT_OPENED / FAULT_RESOLVED events)
- ADR-0016 (bidirectional control/health protocol) — context for the heartbeat channel the faults ride on
- ADR-0018 (Tier-1 status strip) — the aggregation logic this ADR feeds into
