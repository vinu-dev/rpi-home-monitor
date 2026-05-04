# Feature Spec: Encoder Parameter Presets Per Camera

Tracking issue: #252. Branch: `feature/252-encoder-presets`.

## Title

Named encoder presets ("High Bitrate", "Balanced", "Low Bandwidth", "Mobile
Friendly") that bundle resolution + framerate + bitrate + H.264 profile +
keyframe interval into one-click choices the operator picks per camera, with
"Custom" preserved as the explicit fallback for hand-tuned values.

## Goal

An operator opens the per-camera Settings modal in the dashboard, picks
**Balanced** from a new preset dropdown, and the camera reconfigures to the
matching encoder parameters - no manual juggling of bitrate, resolution,
profile, and keyframe-interval boxes. The same operator who *does* want manual
control still gets it: leaving the dropdown on **Custom** (or tweaking any
field after picking a preset) preserves today's per-knob behaviour.

Concretely the feature delivers:

- A small server-side catalogue of named presets, each a fixed mapping of
  `(width, height, fps, bitrate, h264_profile, keyframe_interval)`.
- A new `Camera.encoder_preset` field that records which preset (if any) is
  currently in effect; `""` means **Custom**.
- Filtering of the catalogue per camera so only presets that actually fit
  the camera's reported `sensor_modes` and `encoder_max_pixels`
  (`app/server/monitor/services/camera_service.py:161`) are offered. A 4K
  preset is hidden on an OV5647 camera; a high-bitrate 1080p60 preset is
  hidden on a Pi Zero 2W whose `encoder_max_pixels` doesn't cover it.
- Wire compatibility with the **existing** server→camera control channel
  (ADR-0015): applying a preset is "set these stream-param fields all at
  once on the Camera record, push them via `_translate_stream_params_for_wire`,
  let the camera restart its pipeline as it does today." No new camera-side
  config keys, no firmware change, no Yocto rebuild.
- A dashboard preset dropdown above the existing Resolution / Bitrate /
  Profile / Keyframe inputs in the Camera Settings modal
  (`app/server/monitor/templates/dashboard.html:406`). Picking a preset
  pre-fills the inputs (which then become editable - editing snaps the
  selector back to **Custom**).

This closes the gap between the issue's "competitive feature parity with
ZoneMinder / Frigate per-camera profiles" framing and the current dashboard,
which exposes ffmpeg-shaped knobs an everyday operator should not have to
reason about. It also gives spec #242 (CPU throttling detection) a natural
remediation lever - "your camera is throttling; switch to **Low Bandwidth**"
becomes a single click.

## Context

Existing code this feature must build on:

- `app/camera/camera_streamer/control.py:48` - `PARAM_SCHEMA`: the camera
  already accepts `width`, `height`, `fps`, `bitrate` (500_000-8_000_000),
  `h264_profile` (`baseline|main|high`), `keyframe_interval` (1-120) via the
  HMAC-authenticated control endpoint. Sensor-derived bounds are layered on
  top via `SensorCapabilities` (`control.py:144`). **Presets resolve to
  values in this schema.** No new param keys are introduced.
- `app/camera/camera_streamer/control.py:79` - `RATE_LIMIT_SECONDS = 5`:
  successive control writes within 5 s return HTTP 429. A preset apply is
  one control write (all fields in one PUT), so it lands on the existing
  rate-limit budget and restarts the stream once.
- `app/camera/camera_streamer/config.py:32` - on-camera `DEFAULTS`:
  `BITRATE=4000000`, `H264_PROFILE="high"`, `KEYFRAME_INTERVAL=30`,
  `WIDTH=1920`, `HEIGHT=1080`, `FPS=25`. Presets snap a camera to one of
  these or to other validated combinations - they do not introduce new
  `DEFAULTS` keys.
- `app/camera/camera_streamer/picam_backend.py:305` - `_start_encoder()`
  reads `cfg.bitrate`, `cfg.keyframe_interval`, `cfg.fps`,
  `cfg.h264_profile` directly off the live `ConfigManager`. Restarting the
  pipeline is what makes a control write take effect today (`control.py:374`
  calls `self._stream.restart()`); preset application reuses that path
  unchanged.
- `app/server/monitor/services/camera_service.py:47` - `STREAM_PARAM_FIELDS`
  already lists `width, height, fps, bitrate, h264_profile, keyframe_interval`
  among the fields pushed to the camera. Preset application sets all six in
  one update.
- `app/server/monitor/services/camera_service.py:86` -
  `_translate_stream_params_for_wire()`: the server→camera key translation
  layer. Presets do not need new translations - all preset fields use names
  the camera recognises today.
- `app/server/monitor/services/camera_service.py:161` -
  `_sensor_mode_max_fps()`: turns the camera's reported `sensor_modes` into
  a `(width, height) → max_fps` lookup. Reused to **filter** the preset
  catalogue per camera so only presets with `(w, h)` in the camera's modes
  AND `fps ≤ max_fps_for(w, h)` AND `w*h ≤ encoder_max_pixels` are surfaced.
- `app/server/monitor/services/camera_service.py:488` - `update()`: existing
  PUT pipeline. Preset application reuses this; no separate "apply preset"
  service is added unless an `encoder_preset` echo field needs it (see
  Module Impact).
- `app/server/monitor/services/camera_service.py:776` - `_validate_update()`:
  validates each stream field against per-sensor `sensor_modes` and the
  legacy bitrate/keyframe bounds. **Preset values must pass this same
  validator** when expanded - i.e., the catalogue is hand-curated to never
  emit values that would fail validation for the cameras it's applied to.
- `app/server/monitor/models.py:80` - `Camera` dataclass: the per-camera
  fields `width, height, fps, bitrate, h264_profile, keyframe_interval`
  already live here. **One new field added: `encoder_preset: str = ""`**
  (echo of which named preset is active, `""` = Custom).
- `app/server/monitor/templates/dashboard.html:406` - the Camera Settings
  modal already renders Resolution, FPS, Bitrate, H.264 Profile, and
  Keyframe Interval as separate inputs. The new preset dropdown sits above
  this group and pre-fills the inputs on selection.
- `app/server/monitor/templates/dashboard.html:1140` - `editStreamFor(cam)`
  builds the Alpine `editForm` from a Camera record. Extended to read
  `encoder_preset` and to surface the per-camera filtered catalogue.
- `app/server/monitor/templates/dashboard.html:1243` - `saveStreamSettings()`
  builds the PUT body. Extended to include `encoder_preset` so the server
  can echo the operator's choice back into the Camera record.
- `app/server/monitor/api/cameras.py:509` - `update_camera()` PUT endpoint:
  unchanged in shape; it already proxies to `CameraService.update()`. The
  new `encoder_preset` field flows through the same path.
- ADR-0015 (server-camera control channel) - preset application is a
  single PUT that fan-outs over the existing translation + push hook. No
  new endpoint on the camera. mTLS, rate-limit, and replay protection
  (`control.py:317`) all apply unchanged.
- ADR-0017 (recorder ownership) - the recorder consumes the same RTSP
  stream the live pipeline produces; changing a preset means the recorder
  picks up the new bitrate/resolution on the next segment boundary. No
  recorder code changes.
- Cross-spec references: spec #242 (CPU throttling detection) - dashboard
  could surface "switch to Low Bandwidth" as a remedial action; this spec
  designs the lever, #242 designs the trigger. Spec #250 (clock-drift
  health) - unrelated, no interaction. Spec #251 (timestamped MP4 export)
  - bitrate changes do not affect the stamper (it runs `-c copy`).

The issue body refers to "encoder presets for monitor edit page" (ZoneMinder
#4778) and "per-camera FPS, resolution, bitrate controls" (Frigate). Both
already exist in this product as individual knobs (`dashboard.html:406-440`).
What is missing is the **bundling** of those knobs into named presets and
the **filtering** of unsuitable presets per camera.

## User-Facing Behavior

### Primary path - operator switches a camera to a preset

1. Operator opens the dashboard, clicks **Settings** on a camera tile
   (existing flow at `dashboard.html:editStreamFor()`).
2. The Camera Settings modal opens. A new **Encoder preset** dropdown
   appears above the Resolution input, populated from the per-camera
   catalogue:
   - **High Bitrate** (1920×1080 @ 25 fps, 6 Mbps, high, GOP 50)
   - **Balanced** (1920×1080 @ 25 fps, 4 Mbps, high, GOP 30) - the
     shipping default for cameras paired before this feature
   - **Low Bandwidth** (1280×720 @ 15 fps, 1.5 Mbps, main, GOP 30)
   - **Mobile Friendly** (1280×720 @ 25 fps, 2 Mbps, baseline, GOP 25) -
     baseline profile widens compatibility with older mobile decoders;
     keyframe-every-1s improves seek behaviour on flaky cellular
   - **Custom** - whatever the current per-knob values are; selecting
     this is a no-op
   Presets whose resolution is not in the camera's reported `sensor_modes`
   or whose `width * height` exceeds `encoder_max_pixels` are omitted from
   that camera's dropdown (with a one-line "Some presets hidden because
   this camera doesn't support them" hint when any are filtered).
3. The dropdown's current selection reflects `Camera.encoder_preset` if
   the persisted stream params still match the catalogue entry exactly,
   else **Custom**.
4. Operator picks **Balanced**. The Resolution / FPS / Bitrate / Profile /
   Keyframe inputs **immediately** pre-fill with the preset values in the
   client; nothing is saved until they click **Save**.
5. Operator clicks **Save**. The PUT body carries the preset name plus
   the resolved fields (so the server validates the same payload it would
   for a hand-edited save).
6. Server's `CameraService.update()` validates the resolved fields with
   the existing `_validate_update()` (per-sensor mode check, bitrate
   bounds, profile allowlist) AND validates that the preset name is in
   the catalogue and resolves to the same fields the client sent (defence
   against a stale client racing a catalogue change).
7. Server persists the camera record (with `encoder_preset` echoed),
   pushes the stream params over the control channel, restarts the
   camera's stream pipeline. Operator sees the existing "Settings saved
   and pushed to camera" confirmation. The next motion clip / continuous
   recording uses the new bitrate.

### Primary path - operator hand-tunes after picking a preset

1. Operator picks **Low Bandwidth**, fields pre-fill.
2. Operator nudges Bitrate from 1.5 Mbps to 2.0 Mbps because their LAN
   has headroom.
3. The dropdown selection **automatically snaps to Custom** (Alpine
   watcher on each pre-fillable input).
4. Save persists `encoder_preset=""` so future opens of the modal show
   Custom rather than the (now incorrect) "Low Bandwidth".

### Primary path - operator with a non-default sensor

1. Operator paired a camera with an IMX477 sensor. Its
   `sensor_modes` includes `(2028, 1080)` and `(4056, 3040)`.
2. Modal lists the standard presets PLUS any catalogue entry whose
   resolution fits one of the camera's modes (no auto-generated presets
   in v1; the catalogue is hand-curated, see Open Questions OQ-2).
3. The operator can still hand-pick `4056x3040` via Resolution, Save,
   and the dropdown shows **Custom**.

### Failure states (designed, not just unit-tested)

- **Catalogue resolves to invalid params for this camera**: at PUT time
  the resolved `(w, h, fps, bitrate, ...)` fail `_validate_update()`. Server
  returns 400 with the existing per-field error string (e.g. "resolution
  4056x3040 not supported by sensor"). Modal surfaces the error inline;
  no partial apply. **This should not happen** because the catalogue is
  filtered per-camera before being sent to the client - it's a defence in
  depth against a stale client + sensor swap race.
- **Preset name unknown to server** (client sent `preset: "ultra"` from a
  pre-release build): server returns 400 `"unknown encoder preset"`. The
  resolved fields are still validated and applied if otherwise valid; the
  `encoder_preset` is set to `""` (Custom). This degrades to "save
  individual fields" rather than failing the whole save.
- **Preset name + resolved fields disagree** (client tampered with one
  but not the other): server treats the resolved fields as the truth,
  sets `encoder_preset=""`, persists, and emits an audit event
  `CAMERA_PRESET_FIELD_MISMATCH` with cam_id and the offending preset
  key. Stream params still apply; UI shows Custom on next open.
- **Camera offline at preset apply time**: same behaviour as today's
  per-field save - `config_sync` flips to `pending`, the server stores
  the new fields, and the camera replays them on its next heartbeat
  (`camera_service.py:118`'s `_heartbeat_stream_config_matches`). Preset
  echo (`encoder_preset`) is also stored; no extra heartbeat handling
  needed.
- **Pi Zero 2W cannot encode the chosen preset's bitrate × resolution
  combination at the requested fps** (encoder_max_pixels exceeded): the
  preset is hidden from that camera's dropdown in step 2, so the
  operator never picks it. If they hand-edit to those fields,
  `_validate_update()` rejects via the existing per-sensor / encoder-cap
  check.
- **Catalogue changes on server upgrade and the operator's saved
  `encoder_preset` no longer matches the catalogue entry exactly**: the
  selector falls back to **Custom** in the modal (the resolved fields
  are still valid; only the named-preset link is broken). No data loss,
  no apply-time error.
- **Multiple operators editing the same camera concurrently**: the
  existing `recently_modified_by` audit trail (`camera_service.py:962`)
  surfaces who made the last change. Last-writer-wins on the persisted
  record; no preset-specific locking is added (out of scope - the same
  is true today for any field).
- **Operator selects Custom on a camera that already had Custom**: no-op;
  no Save is required. The modal does not enable a dirty indicator
  unless an actual field change occurred.
- **Preset dropdown disabled** (camera has not yet reported sensor
  capabilities and `sensor_modes` is empty): show only the legacy
  preset-resolution fallback (720p / 1080p) AND only the catalogue
  entries whose resolution is in `VALID_RESOLUTIONS`
  (`camera_service.py:27`). Hint: "Camera has not reported its sensor
  capabilities yet; preset list is conservative."

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: A new `Camera.encoder_preset: str = ""` field is persisted and
  round-trips through `cameras.json`. Default for legacy records is `""`
  (Custom).
  **[unit: dataclass + store round-trip]**
- AC-2: A server-side preset catalogue exposes at least four named
  entries (`high_bitrate`, `balanced`, `low_bandwidth`, `mobile_friendly`)
  plus the implicit `custom`. Each entry resolves to a deterministic
  `(width, height, fps, bitrate, h264_profile, keyframe_interval)` tuple.
  **[unit: catalogue exposes a stable mapping]**
- AC-3: The catalogue is filtered per-camera: only entries whose
  `(width, height)` is in the camera's `sensor_modes` AND whose `fps` is
  ≤ that mode's `max_fps` AND whose `width * height` is ≤
  `encoder_max_pixels` (when reported) are surfaced.
  **[unit: filter against synthetic camera records for OV5647 / IMX219 /
  IMX477 / IMX708 + missing-caps fallback]**
- AC-4: `GET /api/v1/cameras/encoder-presets` returns the global catalogue
  payload `{presets: [{key, label, params: {...}}, ...]}` (no per-camera
  filter; clients filter against the camera's reported caps). Auth
  required (any logged-in user); read-only.
  **[contract: endpoint shape, auth gate]**
- AC-5: `GET /api/v1/cameras/<id>` includes `encoder_preset` in the
  response payload.
  **[contract test extended]**
- AC-6: `PUT /api/v1/cameras/<id>` accepts `encoder_preset` alongside the
  existing stream fields. When the preset name is in the catalogue AND
  the supplied resolved fields exactly match the catalogue entry, the
  echo is persisted as supplied.
  **[contract]**
- AC-7: When `encoder_preset` is a known name but the resolved fields
  diverge from the catalogue entry, the server persists the resolved
  fields, sets `encoder_preset=""`, and emits a
  `CAMERA_PRESET_FIELD_MISMATCH` audit event.
  **[unit + audit-log assertion]**
- AC-8: When `encoder_preset` is an unknown name, the server logs once
  per name+session, persists `encoder_preset=""`, and otherwise applies
  the resolved fields normally.
  **[unit]**
- AC-9: An operator-edit of any pre-fillable field (resolution, fps,
  bitrate, profile, keyframe_interval) after selecting a named preset
  causes the dropdown to snap to Custom in the client (no save round-trip
  needed).
  **[browser-level smoke: open modal, select Balanced, change bitrate,
  assert dropdown text shows "Custom"]**
- AC-10: The dashboard preset dropdown lists only the catalogue entries
  surfaced by the per-camera filter (AC-3). Hidden entries do not appear
  even on hover/expand.
  **[unit: Alpine helper returning the filtered list; integration with a
  rendered template snapshot]**
- AC-11: Selecting a preset in the modal pre-fills the Resolution / FPS /
  Bitrate / Profile / Keyframe inputs without round-tripping the server.
  **[browser-level smoke]**
- AC-12: Saving a preset issues a single `PUT /api/v1/cameras/<id>` that
  triggers exactly one camera-control push (no N-puts per field).
  **[integration: count `_control.set_config` calls when saving Balanced
  on a camera previously in Custom]**
- AC-13: Applying a preset whose resolved fields fail the existing
  `_validate_update()` returns HTTP 400 with the existing per-field error
  message; the camera record is NOT modified and no control push fires.
  **[integration]**
- AC-14: Camera-side `set_config` accepts the preset's resolved field
  bundle in one request; the camera restarts its pipeline once.
  **[integration: camera unit test feeding a Balanced bundle]**
- AC-15: A camera that reports no sensor capabilities (pre-#173 firmware)
  surfaces only catalogue entries whose resolution is in the legacy
  `VALID_RESOLUTIONS` allowlist.
  **[unit]**
- AC-16: The dashboard surfaces the persisted preset on subsequent opens
  of the modal: `Camera.encoder_preset == "balanced"` AND the persisted
  fields match the Balanced catalogue entry → dropdown shows "Balanced";
  any divergence → "Custom".
  **[browser-level smoke]**
- AC-17: An audit event `CAMERA_PRESET_APPLIED` is emitted on successful
  preset apply (cam_id, preset_key, resolved_fields, user, ip). No event
  fires when the saved values match the previously-persisted record (the
  no-op case).
  **[unit + audit assertion]**
- AC-18: The catalogue module is the single source of truth: tests assert
  that `_validate_update()` accepts every catalogue entry's resolved
  fields against the lowest-common-denominator sensor (OV5647) for the
  entries marked `min_sensor: ov5647`, and against IMX477 for entries
  marked `min_sensor: imx477`.
  **[unit, parametrized over the catalogue]**
- AC-19: Hardware smoke: on a Pi Zero 2W with an OV5647, switching from
  Balanced to Low Bandwidth measurably reduces the camera's CPU load and
  the stream's bitrate (sampled from `cpu_temp` / heartbeat over a 60 s
  window).
  **[hardware smoke entry in `scripts/smoke-test.sh`]**
- AC-20: No regressions in the existing per-knob save flow: a save with
  no `encoder_preset` field in the body sets `encoder_preset=""`
  (Custom) and otherwise behaves byte-identically to today.
  **[contract: existing test_api_cameras suite passes unchanged after
  the field is added]**

## Non-Goals

- **Dynamic bitrate adaptation**: out of scope per the issue body.
  Adapting bitrate based on observed network load or CPU is a separate
  feature (potentially folded into spec #242).
- **H.265 / HEVC**: the camera's `H264Encoder`
  (`picam_backend.py:306`) is hardware-locked to H.264 on the Pi camera
  modules this product ships with. A future H.265 spec would expand the
  catalogue.
- **Custom codec selection (VP9 / AV1)**: out of scope; same hardware
  reason.
- **Per-camera FPS override beyond what the existing FPS knob already
  does**: presets bundle FPS like any other field, but the standalone
  FPS input is unchanged.
- **User-defined presets**: v1 ships a hand-curated catalogue. Allowing
  operators to save their own named presets ("Driveway-Night") is a
  reasonable v2 enhancement but adds JSON-store schema, CRUD endpoints,
  and a settings UI - all unjustified for the issue's stated scope.
- **Auto-applying presets based on detected conditions** (low light,
  high motion): scope explosion; spec #242 / a future "scene profiles"
  spec covers this.
- **Per-clip preset overrides** (record motion clips at higher bitrate
  than continuous): the recorder reads whatever the streamer outputs;
  no per-clip override is plumbed today and adding one is out of scope.
- **Migrating existing cameras to a named preset on upgrade**: the new
  `encoder_preset` field defaults to `""` (Custom) on every existing
  cameras.json record. The operator picks one explicitly when they want
  to. An auto-migration ("if your params match Balanced exactly, set
  encoder_preset=balanced for you") is rejected because it surprises the
  operator on first dashboard open after upgrade and obscures the
  upgrade audit trail.
- **Camera-side preset awareness**: the camera receives concrete fields,
  not a preset name. This is intentional - the camera firmware has no
  reason to know presets exist, and it lets the server evolve the
  catalogue without firmware change.
- **Bitrate units in the catalogue**: catalogue values are always bps
  (matching `Camera.bitrate`). UI converts to Mbps for display; no
  magic-number constants in the catalogue.
- **Re-applying a preset on every server boot to "self-heal" a camera
  that was hand-edited out of band**: `encoder_preset` is descriptive,
  not enforcing. The camera's persisted fields are the truth.

## Module / File Impact List

**New code:**

- `app/server/monitor/services/encoder_presets.py` (new) - the catalogue
  module. Public API:
  - `PRESETS: dict[str, EncoderPreset]` - module-level constant; keys
    are the stable preset identifiers (`"high_bitrate"`, `"balanced"`,
    `"low_bandwidth"`, `"mobile_friendly"`); values are dataclasses with
    `key, label, description, params: dict[str, Any], min_sensor: str |
    None, min_encoder_pixels: int | None`.
  - `available_for(camera) -> list[EncoderPreset]` - applies the
    per-camera filter (sensor_modes intersection + encoder_max_pixels
    cap + legacy fallback when no caps reported).
  - `resolve(key: str) -> EncoderPreset | None` - dictionary lookup.
  - `matches(camera, key: str) -> bool` - true iff the camera's
    persisted stream fields exactly equal the named preset's params.
  - No Flask import. Pure data + filter helpers.
- `app/server/monitor/api/cameras.py` (extended) - new
  `GET /api/v1/cameras/encoder-presets` route. Returns
  `{presets: [{key, label, description, params}]}` for the FULL
  catalogue (clients filter per-camera using each camera's reported
  caps in the existing `GET /cameras` payload).
- `app/server/tests/unit/test_encoder_presets.py` (new) - catalogue
  unit tests: filter behaviour for OV5647 / IMX219 / IMX477 / IMX708,
  missing-caps fallback, `matches()` round-trip, `resolve()` for
  unknown keys.
- `app/server/tests/integration/test_api_encoder_presets.py` (new) -
  contract tests for the new GET endpoint, auth gate, and the
  PUT-with-`encoder_preset` flow on `update_camera`.
- `app/camera/tests/test_control_preset_bundle.py` (new, optional) -
  asserts that a single `set_config` call carrying every preset's
  resolved fields validates and triggers exactly one stream restart.
  (Most coverage already exists in test_control.py for individual
  fields; this is a parametrized smoke over the catalogue.)

**Modified code:**

- `app/server/monitor/models.py:80` - `Camera` dataclass: add
  `encoder_preset: str = ""`. No other field changes.
- `app/server/monitor/services/camera_service.py:776` -
  `_validate_update()`: accept `encoder_preset` as a known field;
  validate type (`str`), length cap (≤ 32 chars), and (optionally)
  membership in the catalogue's known keys with `""` allowed for
  Custom. Unknown preset names do NOT 400 - they are coerced to `""`
  per AC-8 with an audit event.
- `app/server/monitor/services/camera_service.py:488` - `update()`:
  before persisting, if `encoder_preset` was supplied AND the resolved
  fields don't match the catalogue entry, set `encoder_preset=""` and
  emit `CAMERA_PRESET_FIELD_MISMATCH`. After successful persist, emit
  `CAMERA_PRESET_APPLIED` (only when the preset key actually changed
  or the apply re-affirmed a non-Custom preset).
- `app/server/monitor/services/camera_service.py:306` -
  `list_cameras()` and `get_camera_status()`: include `encoder_preset`
  in the dict.
- `app/server/monitor/services/audit.py` (or wherever `AuditLogger`
  constants live) - new constants `CAMERA_PRESET_APPLIED`,
  `CAMERA_PRESET_FIELD_MISMATCH`. Audit detail must NOT include
  operator PII; only camera_id, preset_key, resolved field summary.
- `app/server/monitor/__init__.py` - register the new
  `/api/v1/cameras/encoder-presets` route. App-factory wiring is the
  only change; the new module imports cleanly without lifecycle
  hooks.
- `app/server/monitor/templates/dashboard.html:406` - Camera Settings
  modal: add a new `<div class="form-group">` above the Resolution
  input with the **Encoder preset** label and a `<select>` populated
  by an Alpine helper.
- `app/server/monitor/templates/dashboard.html:607` - default
  `editForm` shape: add `encoder_preset: ''` to the initial state.
- `app/server/monitor/templates/dashboard.html:1140` -
  `editStreamFor(cam)`: read `cam.encoder_preset || ''` into the form;
  build `editForm.presetOptions` from the global catalogue (loaded
  once at page init via the new GET endpoint) AND filter by the
  camera's `sensor_modes` / `encoder_max_pixels`.
- `app/server/monitor/templates/dashboard.html:1243` -
  `saveStreamSettings()`: include `encoder_preset:
  this.editForm.encoder_preset || ''` in the PUT body.
- `app/server/monitor/templates/dashboard.html` (new helpers) -
  `applyPreset(key)` (writes preset params into editForm fields and
  sets `editForm.encoder_preset = key`); Alpine watcher on each of
  the five pre-fillable fields that snaps `editForm.encoder_preset`
  to `''` if the value diverges from the named preset's params.
- `app/server/monitor/static/css/style.css` - no new classes; reuse
  existing `.form-group` / `.form-input` styling. The dropdown is a
  standard `<select>` matching the Resolution dropdown above it.
- `app/server/tests/integration/test_api_cameras.py` - extend
  existing tests to round-trip `encoder_preset` and assert the field
  appears in `GET /cameras` output.
- `app/server/tests/unit/test_camera_service.py` - extend
  `_validate_update()` tests with the new field and audit assertions.

**Out-of-tree:**

- No camera-side firmware change. The camera continues to receive a
  bundle of stream-param fields it already accepts (`control.py:48`).
  No new keys, no schema bump.
- No Yocto recipe change. ffmpeg, picamera2, libcamera all unchanged.
- No new external Python dependency.
- No data migration. New `encoder_preset` field defaults to `""` on
  every legacy record; existing `cameras.json` deserialises
  unchanged.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| Camera Python | `pytest app/camera/tests/ -v` (no production-code change; the new test_control_preset_bundle is optional) |
| API contract | new contract test for `GET /api/v1/cameras/encoder-presets`; existing `GET/PUT /cameras` tests extended for `encoder_preset` field |
| Frontend / templates | browser-level smoke on the Camera Settings modal: dropdown populates, pre-fills inputs, snaps to Custom on edit, saves round-trips |
| Security-sensitive path | none touched (no auth / secrets / pairing / wifi / OTA / certificate code modified). `_validate_update()` adds one new optional field with a tight allowlist; argv-construction code unchanged. |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Coverage | server `--cov-fail-under=85` (existing); new `encoder_presets.py` is straightforward dict + filter, expected high coverage |
| Hardware behavior | deploy + `scripts/smoke-test.sh` row "switch preset on a paired camera, observe bitrate change in heartbeat" |

Smoke-test additions (Implementer to wire concretely in
`scripts/smoke-test.sh`):

- "Pair a camera, open Settings, observe Encoder preset dropdown, pick
  Low Bandwidth, Save, wait 5 s, confirm camera's heartbeat reports
  bitrate ≈ 1.5 Mbps."
- "Repeat with a high-bitrate preset and confirm; ensure the recorder's
  next motion clip is sized in line with the new bitrate (within 25 %)."
- "Open the modal again, confirm the dropdown reflects the saved preset;
  hand-edit bitrate, confirm dropdown snaps to Custom, Save, reopen,
  confirm Custom persists."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-252-1 | Operator picks a preset whose bitrate × resolution is too high for the Pi Zero 2W's encoder, the camera fails to start its pipeline, and the live feed goes dark. | Major (operational - operator loses live view of a security camera) | Low (the per-camera filter excludes presets exceeding `encoder_max_pixels`) | RC-252-1: client-side filter (AC-3) hides over-cap presets; server-side `_validate_update()` re-checks because a stale client could race a sensor swap; if the camera fails to start after apply, the existing stream-supervisor backoff (`stream.py:71`) and `consecutive_failures` counter surface the failure on the dashboard within 60 s. AC-13 covers the validation gate. |
| HAZ-252-2 | Catalogue ships a preset whose resolved fields fail the camera's `_validate_update()` for one of the supported sensors, so applying it returns 400 and the operator can't change the camera. | Moderate (UX) | Low (the catalogue is small and hand-curated) | RC-252-2: parametrized test (AC-18) asserts every catalogue entry validates against the sensor it claims to support. CI catches the regression before merge. |
| HAZ-252-3 | `encoder_preset` echo drifts from the resolved fields after an out-of-band edit (e.g. an admin curls the PUT endpoint with one without the other), and the dashboard misleads the operator about what's currently active. | Minor (UX trust) | Medium | RC-252-3: server treats resolved fields as truth (AC-7); on mismatch, `encoder_preset` is forcibly cleared to `""` and an audit event records the discrepancy. The selector then renders Custom on next open, surfacing the divergence rather than hiding it. |
| HAZ-252-4 | Preset apply triggers a stream restart that interrupts an in-progress motion recording. | Minor (operational) | Medium (every preset apply restarts the streamer) | RC-252-4: stream restart already happens for any single field change today (`control.py:374`); presets are no worse. The recorder's segment-close + finaliser path tolerates restarts (proven by today's per-field saves). Operators learn to avoid changing settings during a known incident; documented in the operator-help "Camera Settings" page as it already is for individual knobs. |
| HAZ-252-5 | Catalogue evolves between server upgrades; a saved `encoder_preset=balanced` no longer matches the new Balanced params bundle exactly, so the dropdown shows Custom even though the operator's intent was Balanced. | Minor (UX) | Low (catalogue changes are infrequent and explicitly versioned in source) | RC-252-5: the selector falls back to Custom (no data loss); release notes call out catalogue changes; operator picks the new Balanced if they want it. Catalogue changes are guarded by a unit test that pins the four shipping presets' resolved-field tuples (so a refactor doesn't silently shift them). |
| HAZ-252-6 | Two operators edit the same camera near-simultaneously; one picks Balanced, the other tweaks bitrate; the latter overwrites the former and the dropdown shows Custom while the audit trail attributes both changes. | Minor (UX) | Low (small operator team) | RC-252-6: last-writer-wins is the existing behaviour for any field; presets do not introduce new contention. The per-event audit trail (`CAMERA_PRESET_APPLIED` + `CAMERA_UPDATED`) preserves the sequence for after-the-fact reasoning. |
| HAZ-252-7 | A malicious or buggy client sends a preset name with a path-traversal or shell-meta payload (e.g. `preset: "../../etc/passwd"`); the server logs it verbatim and the log file becomes a vector. | Minor (security hygiene) | Very Low (catalogue keys are matched against a 4-entry allowlist) | RC-252-7: server validates the preset name against the catalogue's known keys and a 32-char length cap; unknown values are coerced to `""` and logged as a counted-once event (per session per name). Audit log entries serialise structured fields, not raw user input. SC-252-A covers the boundary. |
| HAZ-252-8 | Catalogue includes a preset with an FPS the camera's `encoder_max_pixels` allows but the sensor doesn't support at that resolution (e.g. 60fps@1080p on OV5647), and the per-camera filter misses it. | Minor (operational) | Low | RC-252-8: filter combines `sensor_modes` AND `encoder_max_pixels` AND fps ≤ `max_fps_for(w, h)` (AC-3). Tests parametrize over the four supported sensors and assert the filter result is a subset of catalogue entries that actually pass `_validate_update()` for that sensor. |
| HAZ-252-9 | The new `encoder_preset` field is added to the cameras.json schema in a way that breaks deserialisation of pre-feature records on first read. | Major (data loss) | Very Low (Python dataclasses with default values are tolerant) | RC-252-9: the field is added with `default=""`, matching the pattern used for every other field added since v1 (e.g. `motion_sensitivity`, `image_quality`). The `Store.get_camera()` path uses `dataclasses.asdict` / from-dict round-tripping that ignores unknown keys. Test loads a fixture pre-feature `cameras.json` and asserts the new field defaults to `""`. |
| HAZ-252-10 | The dashboard fetch of the catalogue (`GET /api/v1/cameras/encoder-presets`) fails on page load, blocking the Camera Settings modal entirely. | Minor (UX) | Low | RC-252-10: the modal renders without the dropdown when the catalogue fetch fails; per-knob inputs continue to work as today. The fetch is fired on `init()` and the failure is visible only as the dropdown's absence (with a console warning). The operator can refresh; degraded path is graceful. |

Reference `docs/risk/hazard-analysis.md` for the existing register; this spec
adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs):

- **Sensitive paths touched:** none. The change does NOT modify
  `**/auth/**`, `**/secrets/**`, `**/.github/workflows/**`, `pairing.py`,
  `wifi.py`, certificate / TLS / OTA flow, or `docs/cybersecurity/**`.
  The change is confined to:
  - `app/server/monitor/services/encoder_presets.py` (new, pure data)
  - `app/server/monitor/services/camera_service.py` (one new optional
    field in `_validate_update`; one new audit event in `update`)
  - `app/server/monitor/api/cameras.py` (one new GET route, no auth-
    relevant changes)
  - `app/server/monitor/templates/dashboard.html` (one new dropdown)
  - `app/server/monitor/models.py` (one new defaulted field on Camera)
- **No new persisted secret material.** The catalogue is hard-coded; no
  tokens, no credentials, no signing keys.
- **Auth:** the new GET `/api/v1/cameras/encoder-presets` requires
  `@login_required` (any authenticated user, viewer or admin). Catalogue
  is non-secret. The PUT path reuses the existing `@admin_required` /
  CSRF gate on `update_camera()` - no change.
- **Input validation:** preset name is validated against a 32-char length
  cap and (effectively) the catalogue's allowlist. Unknown names are
  coerced, not rejected, to preserve the rest of the save (AC-8). All
  resolved field values flow through the existing `_validate_update()`,
  which already enforces per-sensor and bitrate-bound checks.
- **No subprocess invocation** in any new code. The existing camera
  control client (`_control.set_config`) is reused unchanged - argv
  construction on the camera side (`picam_backend.py:256`) is also
  unchanged because the same field set is being sent.
- **No operator-controlled string interpolation reaching ffmpeg.** Preset
  values are integers and a small enum of profile strings; the camera's
  ffmpeg argv only sees `bitrate`, `keyframe_interval`, `fps`, and
  `profile` as bound numeric / enum values via the H264Encoder
  constructor (`picam_backend.py:310`), not as argv strings.
- **Audit completeness:** `CAMERA_PRESET_APPLIED` records who applied
  what preset on which camera, with the resolved-field summary.
  `CAMERA_PRESET_FIELD_MISMATCH` records tampering / drift. Both flow
  through the existing `AuditLogger` so log retention is unchanged.
- **No DoS surface added.** The new GET endpoint serves a static
  catalogue (≤ 5 KB JSON); no per-request computation. The PUT path is
  rate-limited at the camera (`control.py:79`, 5 s between writes) so a
  rapid-fire preset apply cannot DoS the camera-side encoder restart.
- **No outbound network calls.** The catalogue fetch is same-origin from
  the dashboard; nothing leaves the appliance.
- **No information leakage in the catalogue.** Preset labels and params
  are non-secret operational metadata. They are visible to viewers (any
  logged-in user) by design - viewers must understand what their cameras
  are currently configured for to use the dashboard.
- **CSRF on PUT:** the existing PUT `/api/v1/cameras/<id>` already has
  CSRF protection; no change is required - the new field travels in the
  same body.
- **Rate-limit interaction:** a preset apply sets all stream fields in
  one PUT, which is one camera-control push, which lands on the existing
  5 s rate-limit budget once (not five times). This is strictly *better*
  than today's per-field workflow if an operator changes multiple
  fields back-to-back.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-252` - User need: "I want to optimise a camera's video quality vs.
  bandwidth vs. storage by picking from a small set of named tradeoffs,
  without learning what bitrate / keyframe interval / H.264 profile
  even mean."
- `SYS-252` - System requirement: "The system shall provide a hand-
  curated catalogue of named encoder presets, filterable per camera by
  reported sensor capabilities, applied via the existing server→camera
  control channel without firmware change, with the operator's choice
  echoed back in the camera record for UI continuity."
- `SWR-252-A` - Encoder preset catalogue is a server-side module-level
  constant exposing `key, label, description, params` and a per-camera
  filter helper.
- `SWR-252-B` - Catalogue exposure endpoint is read-only and
  authenticated; payload is the full catalogue (clients filter).
- `SWR-252-C` - Camera record carries an `encoder_preset` echo field
  that is "" (Custom) by default and persisted across upgrades.
- `SWR-252-D` - Preset apply is a single PUT carrying both the preset
  name and the resolved fields; server treats fields as truth and
  echoes the preset name only when they match exactly.
- `SWR-252-E` - Per-camera filter intersects catalogue with reported
  `sensor_modes`, `encoder_max_pixels`, and per-mode `max_fps`.
- `SWR-252-F` - Pre-fillable inputs in the modal snap the dropdown to
  Custom on any divergence from the named preset's params (client-side).
- `SWR-252-G` - Audit events `CAMERA_PRESET_APPLIED` and
  `CAMERA_PRESET_FIELD_MISMATCH` capture preset key, cam_id, resolved
  fields, user, ip.
- `SWA-252` - Software architecture item: "Catalogue lives in a pure
  data module; server-camera control channel is reused unchanged;
  camera firmware is preset-unaware."
- `HAZ-252-1` ... `HAZ-252-10` - listed above.
- `RISK-252-1` ... `RISK-252-10` - one per hazard.
- `RC-252-1` ... `RC-252-10` - one per risk control listed above.
- `SEC-252-A` (preset-name allowlist + length cap), `SEC-252-B` (audit
  completeness for preset apply), `SEC-252-C` (no new auth surface; GET
  is `@login_required`, PUT is `@admin_required` and CSRF-protected).
- `THREAT-252-1` (operator misconfigures camera into a stream-failing
  preset), `THREAT-252-2` (catalogue / resolved-field drift causes UI
  to mislead), `THREAT-252-3` (malicious client supplies bad preset
  name as a logging vector).
- `SC-252-1` ... `SC-252-N` - controls mapping to the threats above.
- `TC-252-AC-1` ... `TC-252-AC-20` - one test case per acceptance
  criterion above.

## Deployment Impact

- **Yocto rebuild needed: no.** No camera-side change; no new device-side
  binary, no new recipe, no `.bbappend` modification.
- **OTA path:** standard server image OTA. On first boot of the new
  image:
  - Existing cameras.json deserialises with `encoder_preset=""` for
    every record - the dropdown opens to Custom on first modal open
    (correct, since their saved fields were never picked from a named
    preset).
  - The new GET endpoint becomes available; the dashboard's new fetch
    fires on page load.
  - Cameras themselves require **no update** - they continue to receive
    the same stream-param bundle they always have.
- **Hardware verification:** required (low-risk).
  - Smoke entry: "Pair a camera, open Settings, pick Low Bandwidth,
    Save, confirm heartbeat reports bitrate ≈ 1.5 Mbps within 30 s."
  - Smoke entry: "Hand-edit bitrate after picking Balanced; confirm
    dropdown shows Custom on next open."
- **Default state on upgrade:** every existing camera shows Custom in
  the new dropdown until an operator picks a named preset. No silent
  reconfiguration.
- **Disk-space impact:** negligible (new field is a short string per
  camera; cameras.json grows by < 100 bytes per camera).
- **CPU-time impact on Pi:** zero on the camera (no new code runs
  there). Server-side: catalogue is a static dict, filter is O(catalogue
  × sensor_modes) ≈ a handful of comparisons - undetectable.
- **Backwards compatibility:** clients that don't know about
  `encoder_preset` continue to round-trip the field unchanged via
  `GET → edit → PUT` because they ignore unknown fields when serialising
  the form back.

## Open Questions

(None of these are blocking; design proceeds. Implementer captures
answers in PR description.)

- **OQ-1: Should the catalogue's preset key set live in source or in
  `/data` config?** Spec chooses source (a Python module). Reasons:
  the catalogue is a curated product surface, changes to it ship with
  the server image and audit-log via git history; allowing operator-
  edited presets in `/data` opens a CRUD endpoint, a UI, and a
  validation surface that the issue does not request. v2 can revisit
  if operators ask for "custom named profiles."
  **Recommendation:** source-resident catalogue for v1.
- **OQ-2: Should preset values be expressed as ratios** (e.g. "50% of
  encoder_max_pixels") rather than absolute numbers? This would make
  presets self-scaling to high-end sensors. The cost is loss of
  predictability ("Balanced" no longer means a stable bitrate across
  hardware) and harder-to-reason-about validation.
  **Recommendation:** absolute values for v1; we can add a
  "scale-with-sensor" preset family in v2 if hardware variance demands
  it.
- **OQ-3: Default catalogue choice for "shipping default" cameras.**
  Today's default is `width=1920, height=1080, fps=25, bitrate=4_000_000,
  h264_profile=high, keyframe_interval=30` (`config.py:32`). The
  Balanced preset is hand-tuned to match this exactly so legacy
  cameras opting in show "Balanced" the moment they pick it. The
  dropdown still opens to Custom on first open (per non-goal).
  **Recommendation:** keep Balanced ≡ existing defaults; document the
  invariant in `encoder_presets.py`.
- **OQ-4: Should picking a preset auto-Save** instead of pre-filling
  inputs and waiting for explicit Save? Pre-fill + explicit Save is
  consistent with every other field in the modal today and gives the
  operator a chance to see what changed before committing. Auto-save
  would be quicker but inconsistent.
  **Recommendation:** explicit Save for v1.
- **OQ-5: Catalogue per-preset description copy.** Copy is operator-
  facing; needs a tone pass before merge. The Architect's first cut:
  - High Bitrate: "Best image quality. Uses the most LAN bandwidth
    and storage."
  - Balanced: "Recommended. Good quality at moderate bandwidth."
  - Low Bandwidth: "Lower resolution and frame rate for slow networks
    or limited storage."
  - Mobile Friendly: "Most compatible with phones and older browsers
    when viewing remotely."
  **Recommendation:** Implementer pins copy with the operator-help
  reviewer; the spec does not bind exact wording.
- **OQ-6: Localisation of preset labels.** The product currently
  ships in English only (`docs/ai/repo-map.md`); preset labels live in
  the catalogue module as plain strings. If localisation is added in
  the future, presets fold into the same i18n scheme as the rest of
  the dashboard.
  **Recommendation:** English-only v1, no extra abstraction.
- **OQ-7: Should the dropdown also offer a "Maximum compatibility"
  preset that pins `h264_profile=baseline` regardless of resolution
  for compatibility with older HLS clients?** Today's "Mobile Friendly"
  already sets baseline at 720p. A separate "Maximum compatibility"
  entry might confuse operators ("isn't Mobile Friendly already
  compatible?"). Spec ships four presets; a fifth can land in v2 if
  operator feedback demands.
  **Recommendation:** four presets in v1.

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): the catalogue is a pure
  data module; routes stay thin; business logic in
  `CameraService.update()`.
- Preserve the server-camera control channel (ADR-0015): no new wire
  keys, no new camera-side acceptance logic, no firmware change.
- Preserve modular monolith (ADR-0006): the catalogue is in-process;
  no separate service.
- Preserve `_validate_update()` as the single validation choke point
  for any stream-param change. The catalogue is curated such that
  every entry's resolved fields pass validation for the cameras it's
  surfaced to.
- Add no new external Python dependency.
- No camera-side change; the camera continues to accept the existing
  stream-param bundle through its existing control endpoint.
- Tests + docs ship in the same PR as code, per
  `engineering-standards.md`. Operator help under `docs/guides/`
  (whichever file owns the Camera Settings walkthrough today) gains
  a short "Encoder presets" section explaining the four entries and
  the Custom fallback.
- Traceability matrix updated in the same PR; `python
  tools/traceability/check_traceability.py` must pass.
- Catalogue values are pinned by a unit test asserting the exact
  `(w, h, fps, bitrate, profile, GOP)` tuples for each shipping
  preset; a refactor cannot silently change what "Balanced" means.
