# ADR-0026: Desired-vs-Observed Camera State Reconciliation

**Status**: Proposed — 2026-05-01
**Resolves**: #114 (no spec was filed; this ADR is the design)
**Relates to**: ADR-0015 (server→camera control channel), ADR-0016 (heartbeat protocol), ADR-0017 (recording modes + on-demand streaming), ADR-0023 (unified fault framework)

## Context

The control plane already has the *shape* of a desired-vs-observed
reconciliation model:

- The server owns desired state — `Camera.width / height / fps /
  bitrate / motion_sensitivity / image_quality / recording_mode /
  recording_motion_enabled / desired_stream_state` etc.
- The camera reports observed state via heartbeat (ADR-0016) —
  current resolution, fps, streaming flag, hardware faults
  (ADR-0023), sensor capabilities.
- A coarse sync flag exists — `Camera.config_sync ∈ {synced,
  pending, error, unknown}`.

What's *implicit* in this design:

1. **No version/epoch.** When the server pushes a config change and
   the camera applies it later (e.g. after reconnect, after a
   slow control-channel response), there's no way to tell whether
   the camera's "I applied this config" message refers to the most
   recent push or a stale one. PR #206 fixed the *replay* path but
   not the freshness check.

2. **`pending` doesn't distinguish "we tried and failed" from
   "we never tried because the camera was offline."** The control
   client either succeeds (→ `synced`) or fails (→ `pending`); the
   `error` state is reserved but rarely used in practice. The
   dashboard surfaces `pending` the same way for both cases.

3. **Drift is invisible.** If a user pulls a camera's SD card,
   reflashes a different firmware that ships different defaults
   (e.g. a board change reset the bitrate), and pairs again under
   the same `cam-<serial>` ID, the server has no signal that the
   observed state diverged from desired. The first heartbeat would
   silently update `Camera` fields per ADR-0016's "camera is the
   source of truth on observed values" logic, masking the
   divergence.

4. **Conflict resolution rules aren't named.** If an admin changes
   the dashboard setting at the same instant the camera reports a
   different observed value (because someone hit the camera's
   local Settings page over its admin web UI), which one wins?
   Today: last-write wins, undeterministically.

5. **Reconnect / reboot reconciliation is heartbeat-shaped.** The
   server's pending-config replay happens *only* on heartbeat
   (#206). If a heartbeat is missed, the camera doesn't get the
   config until the next one. This is fine for the steady state but
   creates a window where the camera is back online but still on
   stale config.

The system runs fine today because the control surface is small
and the failure rates are low. As we add bulk config, fleet-level
adaptive policies, and richer remote management (issues #114
foresees this), the implicit pattern will start producing
operator-visible bugs.

## Decision

**Make the existing reconciliation pattern explicit. Versioned
desired state. Observed state with last-applied version.
Server-driven epoch resolution. Camera-side state machine.**

Three concepts get formalised:

### 1. Version / epoch on every push

`Camera.config_version: int` — monotonic counter, persisted on
the server, incremented on every successful PUT to
`/api/v1/cameras/<id>` that changes a `STREAM_PARAMS` field.
Starts at 1.

Every control-channel push carries this version:

```json
{
  "config_version": 47,
  "fields": { "fps": 30, "bitrate": 4000000, ... }
}
```

The camera persists `last_applied_config_version` and includes it
in every heartbeat:

```json
{
  "last_applied_config_version": 47,
  ...
}
```

### 2. Five-state sync model (replaces today's four)

| State | Meaning |
|---|---|
| `unknown` | Pre-pair / pre-first-heartbeat. No information either way. |
| `synced` | Camera's `last_applied_config_version` ≥ server's `config_version`. |
| `pending` | Server has a newer config than the camera reports. Control push is in flight or queued. |
| `drifted` | Camera's *observed* values disagree with the desired values for the version it claims to have applied. Indicates the camera tried, partially succeeded, and now has diverged state. |
| `error` | Last control-channel push returned an explicit failure response (not just a timeout). |

`drifted` is the new state. It's how we surface "we sent it,
camera says it applied, but its observed values don't match what
we expected" — a partial-success that the existing four-state
model rolls into `synced`.

### 3. Conflict resolution rule

**Server-wins on commit.** When an admin updates a setting via the
dashboard, the server bumps `config_version` and pushes. If the
camera's local web UI mutates the same field at the same time:

- The camera's mutation is allowed locally (operator agency).
- The next heartbeat reports the camera-local value as `observed`.
- The server detects observed ≠ desired and flips to `drifted`.
- The dashboard surfaces a "configuration drifted on this camera
  — apply server values?" affordance.
- Clicking "apply" re-pushes the server's desired state with a
  bumped version; clicking "accept camera values" pulls the
  camera's observed values into the server's desired state with
  a bumped version.

This keeps the camera's local UI usable for emergency on-device
changes (operator opens its `/status` page to fix something while
disconnected from the server), while making any drift explicit
and recoverable.

### Heartbeat carries observed-vs-applied semantics

Each heartbeat embeds:

```json
{
  "last_applied_config_version": 47,    // what the camera thinks
                                        // is the latest config it
                                        // executed
  "observed": {                         // what the camera is
                                        // ACTUALLY running right
                                        // now (read-back from
                                        // libcamera / encoder)
    "fps": 30,
    "bitrate": 4000000,
    "resolution": "1920x1080",
    ...
  },
  "stream_state": "running"|"stopped"   // existing field
}
```

Server-side sync evaluation per heartbeat:

```python
if observed != desired_at_version(last_applied_config_version):
    config_sync = "drifted"
elif last_applied_config_version < server_config_version:
    config_sync = "pending"
else:
    config_sync = "synced"
```

### Reconnect / reboot reconciliation

On every paired-camera heartbeat:

1. Compare `last_applied_config_version` ↔ `server_config_version`.
2. If `<`, server includes `pending_config` with the delta —
   exactly today's #206 behaviour, just version-anchored.
3. If `>`, log a warning (impossible under nominal flow) and
   accept the camera's claim as the new high-water mark — the
   camera is fresher than the server, which can happen if
   `cameras.json` was restored from backup.
4. If `==` AND `observed != desired`, flip to `drifted`.

### Server-side `desired_state_history`

We need a small log of past `(config_version, fields_dict)` so
the heartbeat-time comparison knows what the *desired* state was
at version 47, not just at the current `server_config_version`.
Bounded, JSON-file-backed (ADR-0002) per-camera, capped at last
50 versions. Older versions purged silently — by the time a
camera shows up reporting `last_applied_config_version=10` when
the server is at 65, the right action is force-resync rather
than honouring the stale claim.

Storage: `/data/config/camera_state_history/<camera_id>.json`.

## Alternatives considered

### A. Status quo (no versioning)

Reject. The current `pending` flag conflates "queued" with
"in flight" and "drifted." As fleet size grows, drift becomes
silent and operators lose trust in what the dashboard shows.

### B. Vector clocks per field

A per-field version (so you can independently version `fps` vs
`bitrate`). Reject — overkill. STREAM_PARAMS is small (10-12
fields); a per-camera epoch handles real conflicts cleanly. The
extra metadata cost outweighs the precision gain.

### C. Camera-wins on every conflict

Make the camera the source of truth always. Reject — the dashboard
becomes read-only, every "save" round-trips through the camera, and
admin changes get lost the moment the camera's local Settings page
overwrites them. This is the model that exists *today* implicitly,
and it's exactly what causes #114 to be filed.

### D. Server-wins always (no operator agency on the camera's local UI)

Lock the camera's local Settings UI to read-only when paired. Reject
— the camera's local web UI is a deliberate fallback for the case
where the server is unreachable (per ADR-0017 "the camera is a
self-sufficient device first"). Removing operator agency there
would break the recovery story.

### E. CRDT-based field merge

Last-write-wins per field, derived from each side's heartbeat
timestamp. Reject — clock skew between server and camera (especially
on a Pi Zero with no RTC pre-NTP-sync) makes timestamp ordering
unreliable. Versioned epochs are coarser but unambiguous.

## Consequences

### Positive

- Drift becomes a first-class state with a clear UI surface.
- Conflicts are visible and resolvable rather than silent
  last-write-wins.
- Out-of-order control messages are detectable (the camera ignores
  any push with `config_version <= last_applied_config_version`).
- The existing pending-config replay path (#206) gains a
  freshness gate — a heartbeat that arrives mid-push doesn't
  cause the camera to apply both old and new values.
- The Tier-1 status strip (ADR-0018) gains a new state to
  surface: amber when any camera is `drifted`, with deep-link
  to a "Resolve drift" affordance.

### Negative

- Adds a per-camera versioned history file (`/data/config/camera_state_history/<id>.json`).
  Bounded but real disk + write cost. ~50 versions × ~500 bytes
  each = ~25 KB per camera. Trivial on the SSD-backed `/data`
  but worth naming.
- Camera-side firmware needs to track and report
  `last_applied_config_version`. That's a wire-protocol addition
  (ADR-0016) and a state-machine addition on the camera. Older
  cameras that don't report it are treated as version 0 — the
  next dashboard save bumps to 1 and the new model takes over.
- The "drifted" state is a new operator-visible concept that
  needs UX writing in the dashboard ("Configuration drifted on
  this camera — apply server values?").

### Neutral

- ADR-0024's alert center catalogue could plausibly add a
  `CONFIG_DRIFT_DETECTED` audit code; deferred — surfacing drift
  in the dashboard's per-camera card is enough for v1, and the
  fault framework (ADR-0023) is a more natural home for "this
  camera has a problem."

## Implementation

This ADR is the contract. Real PRs land later in this order:

1. **Server-side: `Camera.config_version` + history store + sync
   evaluation.** No protocol change yet — pre-versioning cameras
   are treated as version-0, so existing fleet keeps working with
   the new state machine. Adds the `drifted` state + persistence.

2. **Camera-side: track + report `last_applied_config_version`.**
   Heartbeat schema bump. Older servers that don't read it ignore
   the new field per the existing wire-version-tolerant pattern.

3. **Server: enforce freshness gate on inbound observed-state
   updates.** A heartbeat carrying a `last_applied_config_version`
   *less than* what the camera reported in its previous heartbeat
   is suspicious (camera went backwards) and gets logged but not
   trusted.

4. **Dashboard UX:** new `drifted` chip on camera cards + the
   "Resolve drift" affordance with two buttons (apply server
   values / accept camera values).

5. **Hardware verification:** real cameras, real edits via
   dashboard + camera-local UI mid-session, observe correct
   transitions through the new state machine.

Steps 1, 3, and 4 are server-only and unit-testable. Steps 2
and 5 require camera-side firmware change and hardware
verification.

## Validation

- Unit tests: server reconciliation logic across all five states
  with mocked heartbeats.
- Contract test: heartbeat schema accepts the new fields without
  rejecting old shapes (back-compat).
- Integration: PUT settings → camera goes pending → heartbeat
  with new version → synced. Mid-flight second PUT → version
  bump → camera observes both, applies highest, server sees
  `last_applied = 49` while desired = 49 → synced.
- Hardware: pair a camera, change a setting on the camera's
  local web UI directly, confirm dashboard shows `drifted` after
  the next heartbeat.

## Risks

| Risk | Mitigation |
|---|---|
| Versioned-history file grows unbounded if compaction fails | Hard cap at 50 entries with assertion in CI; older versions purge on every write |
| Camera firmware crashes mid-apply, partial state lands | The `drifted` state is exactly the surface for this; existing fault framework (ADR-0023) reports the underlying error |
| Clock skew between server and camera | Versions are monotonic counters not timestamps — clock-independent |
| New "drifted" UI confuses operators ("what does drifted mean?") | UX copy: "configuration drifted — your dashboard says X, this camera reports Y. Apply server values? / Accept camera values?" |
| Two admins edit at the same instant | Last commit on the server wins; the loser's PUT 200s but the resulting `config_version` reflects whichever landed second. UX could add a "you may have overwritten Alice's changes" confirmation in v2 |

## Completion Criteria

- [ ] Server reconciliation phase implements all five states
- [ ] Heartbeat schema documents the new fields
- [ ] Dashboard surfaces `drifted` on camera cards
- [ ] Camera firmware reports `last_applied_config_version`
- [ ] Hardware-verified across at least two cameras with
      simulated drift events
- [ ] Closes #114

## References

- Issue #114 (the open enhancement this ADR resolves)
- ADR-0015 (the control channel this version-stamps)
- ADR-0016 (the heartbeat protocol this extends)
- ADR-0017 (recording modes + on-demand streaming — desired-state
  pattern's earlier form)
- ADR-0023 (fault framework — the natural home for "drift means
  something is wrong" in extreme cases)
- PR #206 (pending-config replay — the existing implicit version
  of this reconciliation)
