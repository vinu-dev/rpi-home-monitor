# Feature Spec: Timestamped MP4 Export (Forensic-Grade Audit Trail)

Tracking issue: #251. Branch: `feature/251-timestamped-mp4-export`.

## Title

Embed wall-clock timestamps inside exported MP4 motion clips so forensic
playback preserves original event timing.

## Goal

Operators reviewing or exporting recorded clips for audit, incident review, or
third-party forensic playback get an MP4 whose contents carry the **absolute
wall-clock time of capture** (server-authoritative UTC), not just the filename.
Specifically, every clip on disk - and therefore every clip downloaded via the
existing recordings endpoint - carries:

- MP4 container `creation_time` set to the segment start (UTC, ISO-8601).
- MP4 container `title`, `comment`, `make`, `model` describing the camera and
  capture host so a clip extracted from the recordings tree is self-describing.
- A `mov_text` subtitle track with one cue per second (wall-clock `YYYY-MM-DD
  HH:MM:SS UTC`) so any player that renders subtitles displays the timestamp
  on screen during playback.
- MP4 chapter markers at 60-second boundaries with the same wall-clock label
  so chapter-aware players (VLC, QuickTime) jump-list the clip by minute.

The clip filename and URL contract are unchanged. The dashboard and existing
download flow are unchanged. The new content is **inside** the MP4, surviving
copy/move, email, and re-upload to third-party tooling.

This closes the gap between live dashboard (which shows real-time events with
known absolute times) and archived footage (which today is timestamped only by
filename and filesystem mtime - both detached from the file once copied off the
appliance). It directly supports `docs/ai/mission-and-goals.md`'s
**trustworthiness** principle: an operator can hand a clip to an insurer, a
neighbour, or a small-claims court and have it carry its own provenance.

## Context

Existing code this feature must build on:

- `app/server/monitor/services/streaming_service.py:264` - `start_recorder()`
  spawns `ffmpeg -f segment -segment_format mp4 -strftime 1 ...` writing
  `<recordings_dir>/<cam_id>/YYYYMMDD_HHMMSS.mp4`. No `-metadata` flags today;
  `creation_time` is auto-filled by ffmpeg from the system clock at mux time
  but no other tags are set.
- `app/server/monitor/services/streaming_service.py:345` - `_start_finalizer()`
  spins a per-camera background thread; today it's a legacy no-op (segments
  are written directly as `.mp4`). This is the natural place to plumb in a
  post-close enrichment step that runs once per closed segment.
- `app/server/monitor/services/streaming_service.py:33` -
  `finalize_completed_segments()` and `completed_segment_names()` already track
  which segments ffmpeg has fully closed via the `.segments.log` manifest.
  "Listed in segments.log" is the existing safe-to-touch invariant.
- `app/server/monitor/api/recordings.py:113` - `get_clip()` endpoint:
  `@login_required` thin wrapper that resolves a clip path and returns
  `send_file(clip_path, mimetype="video/mp4")`. **Contract is preserved**;
  the file on disk simply already carries the new metadata by the time the
  endpoint serves it.
- `app/server/monitor/services/recordings_service.py:440` -
  `resolve_clip_path()`: handles dated (`/cam/YYYY-MM-DD/HH-MM-SS.mp4`) and
  flat (`/cam/YYYYMMDD_HHMMSS.mp4`) layouts. Both layouts must be enriched.
- `app/server/monitor/services/snapshot_extractor.py:46` -
  `SnapshotExtractor` already shells `ffmpeg` synchronously per finalised
  clip via `shutil.which("ffmpeg")` with bounded timeout, capture-output, and
  one-shot warning if ffmpeg is missing. **The new enrichment step follows
  the same shape** (sibling responsibility, same lifetime, same failure model).
- `app/server/monitor/models.py:293` - `Clip` dataclass (camera_id, filename,
  date, start_time UTC, duration_seconds, size_bytes, thumbnail). Adds one
  new field: `stamped: bool` (whether the clip carries embedded timestamps).
- `app/server/monitor/models.py:271` - `MotionEvent.clip_ref` already pins
  motion-end to a finalised clip via `{camera_id, date, filename,
  offset_seconds}`. `offset_seconds` is preserved by remux-with-`-c copy`,
  so motion-event correlation is unaffected.
- `app/server/monitor/services/audit.py` - new audit constants
  `CLIP_TIMESTAMP_REMUX_OK`, `CLIP_TIMESTAMP_REMUX_FAILED`,
  `CLIP_TIMESTAMP_BACKFILL_STARTED`, `CLIP_TIMESTAMP_BACKFILL_COMPLETED`.
- `meta-home-monitor/recipes-multimedia/ffmpeg/ffmpeg_%.bbappend:10` -
  ffmpeg is already shipped on the device with `openssl v4l2 gpl`; mov_text
  is part of the default ffmpeg build. **No Yocto change required.**
- ADR-0003 (service-layer pattern) - the new "clip stamper" is a pure
  service; the finalizer thread is the call site. No Flask import in the
  stamper.
- ADR-0017 (snapshot/recorder ownership) - StreamingService remains the
  owner of the recorder pipeline; the stamper plugs into the existing
  finaliser hook.
- Cross-reference: spec `docs/history/specs/250-clock-drift-ntp-health.md`
  - server-clock health is the time authority for embedded timestamps.
  Drift state (green/amber/red) is recorded in the audit event so a
  reviewer can see whether the clip was captured on a synced clock.

The issue body refers to `Recording`, `SnapshotExtractor`, and "video export
pipeline." `Recording` does not exist - the model is `Clip` (recordings_service)
and `MotionEvent` (motion_event_store). `SnapshotExtractor` exists but is
adjacent (still extraction) not the export path. The "video export pipeline"
is the StreamingService recorder + the recordings API. Spec uses the actual
names.

## User-Facing Behavior

### Primary path - operator downloads a clip

1. Operator opens **Recordings** in the dashboard, selects a camera and date,
   clicks a clip's **Download** button (or right-click → Save As on the
   playback element).
2. Server streams the MP4 unchanged from disk via the existing `get_clip()`
   route.
3. The downloaded file - opened in any MP4-aware player - shows:
   - **Container metadata** (visible in VLC: Tools → Codec Information →
     Statistics; in QuickTime: Window → Show Movie Inspector; in `ffprobe
     -show_format`):
     - `creation_time = 2026-05-04T14:30:00.000000Z`
     - `title = "<camera name> - 2026-05-04T14:30:00Z"`
     - `comment = "rpi-home-monitor v<server-version> - <hostname> - clock
       state: green"`
     - `make = "rpi-home-monitor"`
     - `model = "<camera model or 'unknown'>"`
   - **Subtitle track** (default-disabled mov_text track named
     `"timestamps"`): when enabled, displays `2026-05-04 14:30:01 UTC` for
     the second 1, advancing each second to the end of the clip.
   - **Chapters** (visible in VLC: Playback → Chapter; QuickTime: View →
     Show Chapters): one chapter per minute boundary, labelled
     `2026-05-04 14:30 UTC`, `2026-05-04 14:31 UTC`, ...

### Primary path - clip recorded then enriched

When the segment muxer (already running, ADR-0017) closes a 3-minute MP4
segment and writes its filename to `.segments.log`:

1. The existing per-camera finaliser thread (`_start_finalizer` loop) sees a
   new entry in the manifest.
2. The new **clip-stamper service** is invoked synchronously inside the
   finaliser loop for that one segment:
   - Idempotency check: `ffprobe -show_format` the source clip; if
     `tags.creation_time` is present **and** `tags.title` is non-empty,
     consider it already stamped, skip.
   - Compute the wall-clock metadata block from the filename
     (`YYYYMMDD_HHMMSS.mp4` → ISO UTC).
   - Generate a sidecar SRT file in `/tmp` with one cue per second up to
     `duration_seconds`.
   - Run a single `ffmpeg -i <source.mp4> -i <subs.srt> -map 0 -map 1
     -c copy -c:s mov_text -metadata creation_time=... -metadata title=...
     ... <source.mp4>.stamped` invocation. `-c copy` (no re-encode) keeps it
     fast and lossless. mov_text is the only valid subtitle codec inside an
     MP4 container.
   - Chapters are added via a `-f ffmetadata` second input describing
     `[CHAPTER]` blocks at 60-second intervals.
   - On success: `os.replace(source.stamped, source)` (atomic on POSIX),
     remove the SRT and metadata sidecars, audit
     `CLIP_TIMESTAMP_REMUX_OK` (camera_id, filename, duration_seconds,
     server_clock_state, elapsed_ms).
   - On failure (non-zero ffmpeg rc, timeout, OSError): leave the source
     clip untouched, delete the half-written `.stamped` file, audit
     `CLIP_TIMESTAMP_REMUX_FAILED` with stderr-tail.
3. The clip is now downloadable via the existing endpoint with full
   embedded provenance. `Clip.stamped` (in the listing JSON) flips to
   `true` so the UI can show a "Stamped" badge.

### Primary path - operator backfills old clips

Existing clips written before this feature are not auto-stamped on upgrade
(would multiply disk I/O across the entire archive at boot). Instead:

1. Admin opens **Settings → Storage → Recording archive**.
2. A new card "Forensic timestamps" shows:
   - count of stamped vs unstamped clips per camera (cheap header probe via
     ffprobe is too slow for thousands of clips at once - use the new
     `Clip.stamped` field, populated lazily by the lister via a
     cheap-and-cached check; see Open Questions).
   - **"Backfill timestamps now"** button (admin-only, CSRF-protected).
3. Click → modal warns of estimated time and disk I/O; confirm.
4. Server kicks off a background `BackfillTimestampsJob` (single-threaded,
   one camera at a time, one clip at a time, using the same stamper).
5. Progress is rendered in the card (`X / Y clips stamped`) by polling a
   new `GET /api/v1/recordings/timestamp-backfill/status` endpoint.
6. Admin can **Cancel**: the job stops at the next clip boundary (mid-clip
   atomic rename completes or rolls back).
7. Audit: `CLIP_TIMESTAMP_BACKFILL_STARTED` (cam count, clip count) at
   start; `CLIP_TIMESTAMP_BACKFILL_COMPLETED` (success_count, fail_count,
   elapsed_seconds, cancelled_bool) at end.

### Failure states (designed, not just unit-tested)

- **ffmpeg missing** (Yocto image misbuilt): one-shot warning logged
  (`stamper: ffmpeg not on PATH`), all clips remain unstamped. The Clip
  listing shows `stamped: false`. The Settings card displays "ffmpeg not
  available - timestamps cannot be added; reflash recovery image."
- **ffmpeg returns non-zero on a specific clip** (truncated input, codec
  unknown): leave source untouched, delete `.stamped` partial, audit failure,
  continue with subsequent segments. Recording pipeline is **not** stalled
  by stamper failures.
- **Disk full during remux**: ffmpeg fails with `ENOSPC`; same as above
  (delete partial, leave source). Operator already gets the existing
  storage-low alert (spec
  `docs/history/specs/r1-storage-retention-alerts.md`).
- **Segment closes during high motion burst** (10+ segments queued): the
  per-camera stamper queue is bounded at 16 pending; oldest is dropped (and
  audited as `CLIP_TIMESTAMP_REMUX_DROPPED`) so the recorder never blocks
  on stamping. Dropped clips can be backfilled later from Settings.
- **Server clock unsynced at remux time** (drift `red` per spec #250): the
  remux still runs but the audit event records `clock_state: red`, and the
  embedded `comment` field reads `"... clock state: red - timestamps may
  be inaccurate"`. The MP4 still gets enriched - omitting it would defeat
  the forensic goal entirely.
- **Operator opens the clip in a player that hides subtitle tracks by
  default** (most players): the metadata in the container header is still
  visible via Get Info / ffprobe; the operator can enable the subtitle
  track manually. The operator-help docs include screenshots for VLC and
  QuickTime.
- **Clip is being remuxed when an operator clicks Download**: the source
  clip is still present (atomic replace hasn't happened yet); send_file
  serves the unstamped version. Subsequent download serves the stamped
  version. No 5xx, no half-written response.
- **Atomic-rename race during simultaneous read** (operator's browser is
  range-streaming the clip while finaliser does `os.replace`): on Linux,
  open file handles survive the rename - operator's stream finishes against
  the original inode; new requests get the stamped inode. No corruption.
- **Backfill cancelled mid-clip**: the `.stamped` partial is deleted; source
  is untouched; the clip is left unstamped (will be picked up on next
  backfill run).
- **Backfill encounters a clip already stamped** (ran before, partial
  completion): idempotency check skips it; counts toward "success."

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: Newly recorded segments carry `creation_time` matching the filename's
  UTC start within ±1 second.
  **[unit + contract: stamper test asserting ffprobe output]**
- AC-2: Newly recorded segments carry `title`, `comment`, `make`, `model`
  metadata as specified, populated from camera record + server hostname.
  **[unit + contract]**
- AC-3: Newly recorded segments carry a `mov_text` subtitle track with one
  cue per second, content `YYYY-MM-DD HH:MM:SS UTC` advancing from clip
  start.
  **[unit + integration: ffprobe stream count == 2 (video + subtitle);
  parse subtitle file, assert cue cadence]**
- AC-4: Newly recorded segments carry MP4 chapter markers at 60-second
  boundaries with wall-clock labels.
  **[unit: ffprobe -show_chapters]**
- AC-5: The download endpoint contract is unchanged: status 200,
  `Content-Type: video/mp4`, raw MP4 body, byte-for-byte equal to the on-
  disk file.
  **[contract test: existing test_api_recordings extended to assert
  byte-equality with disk]**
- AC-6: Stamping is idempotent: running the stamper twice on the same clip
  produces identical bytes (modulo ffmpeg's mux nondeterminism, the second
  run is a fast no-op via the ffprobe pre-check).
  **[unit]**
- AC-7: A failing stamp leaves the source clip untouched and intact; the
  clip remains downloadable as the unstamped original.
  **[integration: inject ffmpeg failure via PATH stub, assert source bytes
  unchanged and audit event written]**
- AC-8: Recording is not stalled by stamper failure: a corrupted segment
  whose stamp fails does not prevent subsequent segments from being
  recorded or stamped.
  **[integration with two segments, first stamper raises, second succeeds]**
- AC-9: Stamper queue per camera is bounded at 16; queue overflow drops
  oldest and audits `CLIP_TIMESTAMP_REMUX_DROPPED`.
  **[unit with mocked queue]**
- AC-10: `Clip.stamped` reflects true post-stamp state; appears in
  `GET /api/v1/recordings/<cam>` payload.
  **[contract]**
- AC-11: `MotionEvent.clip_ref.offset_seconds` continues to point to the
  same wall-clock moment after a stamp (round-trip via player playback).
  **[integration: motion event at offset 30s; after stamp, ffprobe -ss 30
  -show_frames produces a frame whose pts equals 30000ms ±100ms]**
- AC-12: ffmpeg invocation argv is constructed with no operator-controlled
  string interpolation; camera_id and filename are used as path components
  only after `resolve_clip_path()` validation.
  **[security review + unit test asserting no shell=True usage]**
- AC-13: When the server clock is in `red` drift state at remux time
  (spec #250), the embedded `comment` field includes the warning string
  and the audit event records `clock_state=red`.
  **[unit with mocked TimeHealthService]**
- AC-14: Settings → Storage → Recording archive shows count of stamped vs
  unstamped clips per camera and provides a "Backfill timestamps" admin
  action.
  **[integration with template render; admin-only access]**
- AC-15: Backfill action processes all unstamped clips one camera at a time
  and emits start/complete audit events.
  **[integration with seeded unstamped clips]**
- AC-16: Backfill cancellation stops at next clip boundary, leaving in-
  flight clip either fully stamped or fully unstamped (never half).
  **[integration]**
- AC-17: `GET /api/v1/recordings/timestamp-backfill/status` returns
  `{state: idle|running|cancelling, processed: int, total: int,
  current_camera: str, started_at: ISO}` (admin-only).
  **[contract]**
- AC-18: Stamping a clip preserves bytewise A/V content (probed via frame
  hashing of first N keyframes, identical before and after stamp).
  **[integration]**
- AC-19: ffmpeg-not-on-PATH degrades gracefully: log once, all clips remain
  unstamped, Settings card shows the missing-tool state.
  **[unit with PATH override]**
- AC-20: Stamp metadata round-trips through a copy + re-upload (e.g.,
  upload to a third-party tool, download): `ffprobe` of the round-tripped
  file shows the same `creation_time`, `title`, subtitle track.
  **[integration with shutil.copy as proxy for upload]**
- AC-21: Clip listing performance: a directory of 1000 clips lists in
  ≤500 ms when `Clip.stamped` field is populated from a cached lookup
  (not by per-clip ffprobe).
  **[perf test in CI; stamped state cached in `<filename>.stamp.ok` sentinel
  written by stamper after success]**
- AC-22: Hardware smoke: after deploying to a Pi and triggering motion,
  the recorded clip downloaded to a laptop opens in VLC and shows the
  embedded timestamp track.
  **[hardware smoke entry in `scripts/smoke-test.sh`]**

## Non-Goals

- **Visual timestamp overlay (burned-in pixels)**: out of scope. The issue
  body explicitly excludes "watermarking or visual timestamp overlay
  (separate feature)." Burned-in overlays require re-encoding (lossy and
  CPU-expensive) and degrade the original footage. The mov_text + container
  metadata approach is non-destructive.
- **Sub-second cue cadence**: one cue per second is sufficient for forensic
  context per the issue. 250 ms or finer would multiply subtitle file size
  with no operator-visible benefit (most players display only the most
  recent cue).
- **Cryptographic signing of clips**: out of scope. A future spec could add
  a detached signature sidecar (`.mp4.sig`) signed by a per-server key, but
  that's a separate audit/chain-of-custody concern.
- **Per-frame timecode (SMPTE)**: out of scope. SMPTE timecode tracks are a
  professional broadcast feature with limited player support; mov_text is
  more universal.
- **Other container formats** (WebM, MKV, MOV): out of scope. The issue
  scopes to MP4. WebM doesn't ship from this product today.
- **Modifying clips outside the recordings tree** (camera-side files,
  uploads, external imports): the stamper only enriches files written by
  the StreamingService recorder.
- **Retroactively re-stamping already-stamped clips with a new schema**: if
  the stamp schema changes in v2, a separate migration spec covers it.
- **Camera-side timestamp embedding**: clips are muxed on the server (ADR-
  0017), so all stamping is server-side. Camera-side embedding would
  require firmware changes and is unnecessary while the server owns the
  muxer.
- **Per-event motion overlay** (e.g., highlighting which seconds had
  motion): handled by motion-event correlation in the dashboard, not the
  clip itself.
- **NTP server changes / clock authority redesign**: time authority is
  the server's NTP-synced clock. Drift health is owned by spec #250
  (already implemented this cycle). This spec consumes its state but does
  not modify it.

## Module / File Impact List

**New code:**

- `app/server/monitor/services/clip_stamper.py` (new) - `ClipStamper`
  service. Public API:
  - `stamp(clip_path: Path, camera: Camera, server_meta: ServerMeta) ->
    StampResult` - synchronous, idempotent, returns `(ok, reason, elapsed_ms)`.
  - Internal: ffprobe pre-check, SRT generation, ffmetadata generation,
    ffmpeg `-c copy` invocation, atomic replace, sentinel write
    (`<filename>.stamp.ok`), cleanup of temp files.
  - No Flask import. ffmpeg path resolved via `shutil.which("ffmpeg")` and
    `shutil.which("ffprobe")` with one-shot missing-tool warning matching
    `SnapshotExtractor`'s pattern.
  - Bounded subprocess timeout (default 30 s for a 3-min clip, configurable
    via constant).
- `app/server/monitor/services/clip_stamp_queue.py` (new) - `ClipStampQueue`
  per-camera bounded `queue.Queue(maxsize=16)`; worker thread per camera
  pulled by name from a dict; drop-oldest on overflow; audit-on-drop.
  Lifecycle hooked into StreamingService start/stop so the queue stops
  cleanly on shutdown.
- `app/server/monitor/services/timestamp_backfill_service.py` (new) -
  `TimestampBackfillService` orchestrating a one-shot scan of the recordings
  tree, enqueuing each unstamped clip into the `ClipStampQueue`. Tracks
  state (`idle | running | cancelling`), counts, and current camera. Single
  global instance; only one backfill at a time.
- `app/server/monitor/api/timestamp_backfill.py` (new blueprint) -
  admin-only:
  - `POST /api/v1/recordings/timestamp-backfill` - start backfill (CSRF
    + admin).
  - `DELETE /api/v1/recordings/timestamp-backfill` - cancel (CSRF +
    admin).
  - `GET /api/v1/recordings/timestamp-backfill/status` - poll state
    (admin-only, no CSRF for GET).
- `app/server/tests/unit/test_clip_stamper.py` (new) - stamper unit tests:
  idempotency, atomic-rename behaviour, missing-ffmpeg, ffmpeg-failure,
  ffprobe-of-output, subtitle generation correctness, chapter generation,
  metadata round-trip.
- `app/server/tests/unit/test_clip_stamp_queue.py` (new) - queue unit
  tests: bounded behaviour, drop-oldest, audit-on-drop, shutdown drains.
- `app/server/tests/integration/test_recording_stamper_integration.py`
  (new) - end-to-end: spawn fake ffmpeg recorder writing a real (tiny)
  MP4 segment, finaliser invokes stamper, assert downloaded clip carries
  metadata.
- `app/server/tests/integration/test_timestamp_backfill.py` (new) -
  backfill end-to-end: seed unstamped clips, run backfill, assert all
  stamped + audit events present + cancellation works.
- `app/server/tests/contract/test_recordings_endpoint_unchanged.py` (new
  or extension of existing) - assert the GET clip endpoint returns
  identical bytes to the on-disk file before and after this change (the
  contract is "stream the file as-is").

**Modified code:**

- `app/server/monitor/services/streaming_service.py`:
  - Constructor accepts a `clip_stamper` and `clip_stamp_queue` (constructor
    injection per ADR-0001 / engineering-standards.md).
  - `_start_finalizer()` loop: when a new entry appears in `.segments.log`,
    enqueue `(cam_id, clip_path)` into the per-camera stamp queue. The
    queue worker invokes the stamper.
  - `stop()` / `shutdown()`: drains and joins the stamp-queue workers
    cleanly.
- `app/server/monitor/services/recordings_service.py`:
  - `list_clips()` populates `Clip.stamped` from the presence of
    `<filename>.stamp.ok` sentinel (cheap stat call, no ffprobe).
  - `resolve_clip_path()` is unchanged.
- `app/server/monitor/services/recorder_service.py`:
  - `list_clips()` returns `Clip` instances now including `stamped` field.
  - `get_dates_with_clips()` unchanged.
- `app/server/monitor/models.py`:
  - `Clip` dataclass: add `stamped: bool = False`.
  - Optional: a small `ServerMeta` dataclass (hostname, server_version,
    git_sha) the stamper reads at construction time.
- `app/server/monitor/services/audit.py`:
  - New constants `CLIP_TIMESTAMP_REMUX_OK`,
    `CLIP_TIMESTAMP_REMUX_FAILED`, `CLIP_TIMESTAMP_REMUX_DROPPED`,
    `CLIP_TIMESTAMP_BACKFILL_STARTED`,
    `CLIP_TIMESTAMP_BACKFILL_COMPLETED`,
    `CLIP_TIMESTAMP_BACKFILL_CANCELLED`.
  - Audit detail must NOT include any operator PII; only camera_id,
    filename, durations, counts, clock_state.
- `app/server/monitor/__init__.py`:
  - App-factory wires `ClipStamper`, per-camera `ClipStampQueue`,
    `TimestampBackfillService` into the app.
  - Registers the new blueprint.
- `app/server/monitor/templates/settings.html` (Storage / Recording archive
  card):
  - New "Forensic timestamps" section with stamped/unstamped counts and
    backfill button.
- `app/server/monitor/static/css/style.css` - minor additions for the new
  card; reuse existing button + progress styles.
- `app/server/monitor/templates/recordings.html` (or its current
  equivalent):
  - Add a small "Stamped" badge to clip rows where `stamped: true` so an
    operator can see provenance state at a glance.

**Out-of-tree:**

- No camera-side change. Camera firmware is unchanged. Clips are server-
  muxed (ADR-0017), so all stamping is server-side.
- No Yocto recipe change. ffmpeg + mov_text + chapter support are already
  in the base ffmpeg build per
  `meta-home-monitor/recipes-multimedia/ffmpeg/ffmpeg_%.bbappend`.
- No new external Python dependency. ffmpeg/ffprobe are external binaries
  invoked via `subprocess.run`. SRT and ffmetadata are plain text formats
  generated in pure Python.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | new contract test for `/api/v1/recordings/timestamp-backfill/*`; existing test for `GET /recordings/<cam>/<date>/<file>` extended to assert byte-equality with disk |
| Frontend / templates | browser-level check on `/settings` Storage card and `/recordings` Stamped badge |
| Security-sensitive path | argv-construction unit test (no shell=True, no operator-controlled interpolation); admin-only enforcement on backfill endpoints |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Coverage | server `--cov-fail-under=85` (existing); new files counted |
| Hardware behavior | deploy + `scripts/smoke-test.sh` row "stamped clip downloads with embedded timestamps" |

Smoke-test additions (Implementer to wire concretely in
`scripts/smoke-test.sh`):

- "Trigger motion, wait for segment close, download clip, run `ffprobe` on
  it, assert `creation_time` and subtitle stream are present."
- "Run backfill on a deliberately-unstamped fixture clip, assert it
  becomes stamped within N seconds."
- "Crash a stamper invocation (PATH override), assert source clip remains
  intact and downloadable."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-251-1 | Stamper remux corrupts a clip → forensic value lost / clip unplayable. | Major (operational + forensic) | Low | RC-251-1: write to `.stamped`, atomic `os.replace()` on success only; on any error delete the partial and leave the source. ffprobe-validate the `.stamped` output before replacing. Unit + integration test (AC-7, AC-18). |
| HAZ-251-2 | Server clock skewed at remux time → embedded `creation_time` is wrong → operator/court misled by "authoritative" timestamp. | Major (forensic / trust) | Medium | RC-251-2: read clock-drift state from the TimeHealthService (spec #250) at remux start; inject `clock_state: <green|amber|red>` into the `comment` field and the audit event. Operator-visible warning string when red. AC-13. |
| HAZ-251-3 | Stamper falls behind during heavy motion → backlog grows unbounded → memory/disk exhaustion. | Moderate (operational) | Low | RC-251-3: per-camera `Queue(maxsize=16)`; drop-oldest on overflow with `CLIP_TIMESTAMP_REMUX_DROPPED` audit; recorder pipeline never blocks on stamping (queue.put_nowait + drop). Settings shows unstamped count for follow-up backfill. AC-9. |
| HAZ-251-4 | Disk I/O doubles at remux time (write `.stamped`, then replace) → fills `/data` faster than expected. | Moderate (operational) | Medium | RC-251-4: temp `.stamped` is in the same directory and deleted on success/failure; net steady-state disk usage grows by zero (file size ~unchanged with `-c copy` + small subtitle/metadata addition, on the order of KB). Document in deployment notes. |
| HAZ-251-5 | Pre-feature clips never get timestamps → operator confused by mixed-state archive (some stamped, some not). | Minor (operational) | High (every existing clip) | RC-251-5: `Clip.stamped` field surfaced in listings + UI badge; Settings → Storage → Recording archive offers admin-triggered backfill action with progress + cancel + audit. AC-14, AC-15, AC-16, AC-17. |
| HAZ-251-6 | mov_text subtitle track interpreted as caption / accessibility track by some players → confusing UX (subtitle appears unsolicited). | Minor (UX) | Low | RC-251-6: subtitle track marked default-disabled (`disposition:s:0 0`); chapters provide a non-subtitle visual cue; operator help docs show how to enable it in VLC / QuickTime / browser players. AC-3 asserts the disposition. |
| HAZ-251-7 | Operator removes/disables stamping in a future settings toggle → archive is split (stamped vs not) → forensic value reduced. | Minor (operational) | Low | RC-251-7: this spec does NOT introduce a "disable stamping" toggle. Stamping is always-on for new clips. If an operator-toggleable knob is added in v2 it must surface clearly in the audit log (`STAMPING_DISABLED`). Documented as a non-goal. |
| HAZ-251-8 | Atomic replace race during simultaneous range read → operator's playback corrupts mid-stream. | Minor (operational) | Very Low | RC-251-8: on Linux, open file handles survive `os.replace()` - the reader's stream finishes against the original inode. Documented invariant; integration test seeds a long-running read against the source while remux runs. |
| HAZ-251-9 | Backfill on a multi-thousand-clip archive runs for hours, blocks new recordings via I/O contention. | Minor (operational) | Medium | RC-251-9: backfill runs single-threaded, one clip at a time, with a 100 ms throttle between clips; recorder pipeline runs at strictly higher priority (it's the same priority - the throttle is the control). Cancel button always available. Progress is visible. |
| HAZ-251-10 | ffprobe pre-check is fooled by a partially-tagged clip (e.g., `creation_time` present from ffmpeg auto-fill but no `title`) → clip stamped twice (wasteful) or never (if both set). | Minor (operational) | Low | RC-251-10: idempotency check requires BOTH the `<filename>.stamp.ok` sentinel AND a positive ffprobe (`title` non-empty). Sentinel is the primary fast-path; ffprobe is the truth. AC-6. |

Reference `docs/risk/` for the existing architecture risk register; this
spec adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs):

- **Sensitive paths touched:** none of the high-scrutiny paths from
  `docs/ai/roles/architect.md` (no `**/auth/**`, no `**/secrets/**`, no
  `**/.github/workflows/**`, no `pairing.py`, no `wifi.py`, no certificate
  / TLS / OTA flow). The change is confined to:
  - `app/server/monitor/services/` (recorder finaliser + new stamper
    service)
  - `app/server/monitor/api/recordings.py` (no contract change) and a new
    backfill blueprint (admin-only, CSRF-protected)
  - `app/server/monitor/templates/settings.html` (admin-only UI)
- **No new persisted secret material.** No tokens, no credentials, no
  pepper, no signing key. The `comment` field embeds server hostname and
  version - non-secret operational metadata.
- **Subprocess argv construction:** all ffmpeg / ffprobe invocations use
  `subprocess.run([list, of, args])` with `shell=False`. Camera_id and
  filename arrive only after `RecordingsService.resolve_clip_path()`
  validation, which already enforces the allowlist alphabet and traversal
  guard. AC-12 is the security-review gate.
- **Operator-controlled string interpolation:** the only operator-supplied
  value reaching ffmpeg is the camera *name* embedded in the MP4 `title`
  metadata. Names are sanitised at the metadata-string boundary
  (NUL-strip, length-cap 200 chars, `=` and newlines replaced with `_`)
  before being formatted into the `-metadata title=...` argv element.
  ffmpeg parses `-metadata key=value` as a single argv element so even an
  unsanitised value cannot inject another flag - but the sanitiser is
  defence in depth.
- **Backfill endpoint authorization:** `@admin_required` + `@csrf_protect`
  on POST and DELETE; `@admin_required` on GET status. Same gate as the
  existing destructive recordings endpoints. Audit on every state change.
- **Information leakage in metadata:** the embedded `comment` field
  contains hostname + server version + clock_state. This is intentional
  (forensic provenance) and consistent with the trustworthy-self-hosted
  product positioning - the operator owns the clip and the box. Camera
  *coordinates* are NOT embedded (we don't have them today and they're not
  needed).
- **Audit completeness:** every stamp success, failure, drop, and backfill
  state change is logged via the existing `AuditLogger`. Plaintext secrets
  are never logged because none are involved.
- **Resource exhaustion as DoS vector:** the stamper queue is bounded
  (HAZ-251-3); ffmpeg has a per-invocation timeout; only one stamper runs
  per camera at a time. An attacker who could trigger a burst of motion
  cannot DoS the recorder via the stamper.
- **No outbound network calls.** All stamping is local-disk + local-CPU.
  Distinct from spec #239 (webhooks), which has its own SSRF surface.
- **No Range-request behaviour change.** `send_file()` continues to honour
  HTTP Range automatically; the on-disk file is just enriched. Range
  request semantics are governed by Werkzeug, unchanged here.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-251` - User need: "I want my exported motion clips to carry their own
  capture timestamp so I can hand them to a third party (insurer, court,
  neighbour, security company) without separately attesting when the clip
  was recorded."
- `SYS-251` - System requirement: "The system shall embed wall-clock
  capture metadata (container `creation_time`, descriptive tags, and a
  per-second timestamp subtitle track) inside every recorded MP4 segment,
  using the server's NTP-synced clock as the authoritative time source."
- `SWR-251-A` - Stamper service must be idempotent and atomic.
- `SWR-251-B` - Stamper failure must not corrupt the source clip.
- `SWR-251-C` - Stamper failure must not stall the recorder pipeline.
- `SWR-251-D` - Embedded `comment` must record the server clock-health
  state at stamp time (cross-spec dependency on #250).
- `SWR-251-E` - Backfill action must be admin-only, CSRF-protected,
  cancellable, and audit-logged.
- `SWR-251-F` - Subtitle generation must produce one cue per second up to
  the clip's measured duration.
- `SWA-251` - Software architecture item: "Per-camera bounded stamp
  queue + worker thread; stamper invoked from existing recorder
  finaliser; downstream recordings API contract unchanged."
- `HAZ-251-1` ... `HAZ-251-10` - listed above.
- `RISK-251-1` ... `RISK-251-10` - one per hazard.
- `RC-251-1` ... `RC-251-10` - one per risk control listed above.
- `SEC-251-A` (atomic-replace integrity), `SEC-251-B` (admin-only backfill
  endpoint), `SEC-251-C` (subprocess argv hygiene), `SEC-251-D` (audit
  completeness for stamping outcomes), `SEC-251-E` (camera-name
  sanitisation at the metadata boundary).
- `THREAT-251-1` (clip corruption via stamper failure),
  `THREAT-251-2` (DoS via stamper queue overflow),
  `THREAT-251-3` (forensic-trust regression via skewed clock),
  `THREAT-251-4` (operator-supplied camera name injecting ffmpeg flags).
- `SC-251-1` ... `SC-251-N` - controls mapping to the threats above.
- `TC-251-AC-1` ... `TC-251-AC-22` - one test case per acceptance
  criterion above.

## Deployment Impact

- **Yocto rebuild needed: no.** ffmpeg + mov_text + chapter muxing are
  already in the device image
  (`meta-home-monitor/recipes-multimedia/ffmpeg/ffmpeg_%.bbappend` enables
  `gpl` for libx264 + the standard mov_text muxer). ffprobe ships with
  ffmpeg.
- **OTA path:** standard server image OTA. On first boot of the new image:
  - The recorder pipeline starts, finaliser queues now route closed
    segments to the stamper. New clips are stamped within seconds of
    closure.
  - Existing clips are NOT auto-stamped (would multiply disk I/O on
    boot day). They are listed as `stamped: false` in the recordings
    API and shown without the badge. Operator can backfill from
    Settings.
  - No data migration. New `Clip.stamped` field has a `False` default;
    existing serialisation is unaffected.
- **Hardware verification:** required.
  - Smoke entry: "After deploy, trigger motion, wait 200 s for segment
    close, download the clip from a laptop browser, open in VLC, confirm
    embedded timestamp track + chapters + container `creation_time`."
  - Smoke entry: "Run admin backfill on the Pi's full archive; confirm
    completion audit event and at least one previously-unstamped clip
    has `stamped: true` after the run."
- **Default state on upgrade:** stamping is on for new clips; backfill is
  off until an admin runs it. No operator interaction is required for the
  feature to start delivering value on new recordings.
- **Disk-space impact at steady state:** negligible (subtitle + chapters
  + metadata add a few KB to a multi-MB clip; transient `.stamped`
  during remux is in the same directory and deleted within seconds).
- **CPU-time impact on Pi:** one ffmpeg `-c copy` per closed segment per
  camera per ~3 minutes. Empirical assumption (to be validated in
  hardware smoke): under 1.5 s per 3-minute clip on a Pi 4B. Overlaps
  recorder steady state, so peak doesn't shift.

## Open Questions

(None of these are blocking; design proceeds. Implementer captures answers
in PR description.)

- **OQ-1: Stamp at finalisation vs at download time.** Spec chooses
  finalisation (one-time cost, downloads stay fast, on-disk archive is
  forensically self-describing). The alternative (lazy at download) was
  rejected because:
  (a) any operator who lists clips without downloading still wants the
  stamped state visible, and
  (b) re-running ffmpeg on every download multiplies CPU cost vs once
  per clip lifetime.
  **Recommendation:** keep finalisation. Re-evaluate only if Pi CPU
  budget proves marginal in hardware smoke.
- **OQ-2: Subtitle cue cadence.** One cue per second as default; UI
  could expose a "fine-grained timestamps (250 ms)" Setting for
  high-bandwidth cases. v1 ships with one-per-second only.
  **Recommendation:** ship one-per-second; add Settings knob only on
  operator request.
- **OQ-3: Camera name embedding policy.** The `title` and `comment`
  fields embed the operator-chosen camera name. Some operators may
  consider the name PII (e.g., "Front porch - Smith residence"). The
  archive lives on the operator's appliance and the metadata leaves
  the box only when the operator exports - so this is operator-controlled
  data flow, but worth flagging.
  **Recommendation:** embed by default; document in operator help that
  removing the camera name from the metadata requires renaming the
  camera before recording.
- **OQ-4: How is `Clip.stamped` populated cheaply at listing time?**
  Spec proposes a `<filename>.stamp.ok` sentinel file written by the
  stamper after success - cheap stat, no ffprobe. Sentinel can be lost
  if the disk is touched manually; in that case the listing shows
  `stamped: false` and the operator can re-run backfill (which is a no-op
  via the stamper's idempotency check on the actual ffprobe).
  **Recommendation:** sentinel file is the primary fast-path; ffprobe
  is the truth source consulted only by the stamper at remux time.
- **OQ-5: Should chapter granularity be per-minute, per-30-seconds, or
  per-motion-event?** Per-motion-event would be the most useful but
  requires the chapter generator to consume motion events at remux time
  (architectural coupling between the stamper and the motion-event
  store). Per-minute is the simplest and matches typical player UI.
  **Recommendation:** per-minute for v1; per-motion-event chapter
  generation is a v2 spec.
- **OQ-6: Should the stamper service ever re-stamp a clip whose
  metadata predates a schema change?** Out of scope for this spec
  (avoids re-write storms on upgrade). Future schema-change spec must
  add a versioned `meta:rpi_stamp_version` tag and a migration runner.
  **Recommendation:** v1 stamp version is implicit; if v2 changes the
  schema, add the version tag then.
- **OQ-7: ffmpeg invocation timeout for backfill on a long clip
  (e.g., a 30-minute test recording).** Per-invocation timeout default
  is 30 s; long recordings may need a longer ceiling. Spec leaves the
  constant tunable.
  **Recommendation:** 30 s baseline; raise to `max(30, duration_s * 0.5)`
  if the Pi proves slower than expected.

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): routes thin, business logic
  in `ClipStamper` / `ClipStampQueue` / `TimestampBackfillService`. The
  Flask blueprint is a thin adapter.
- Preserve modular monolith (ADR-0006): the stamper is in-process, not a
  separate daemon. Per-camera worker threads are the same shape as the
  existing finaliser threads.
- Preserve recorder pipeline ownership (ADR-0017): StreamingService
  remains the recorder owner; the stamper plugs into the existing
  finaliser hook via constructor injection. No second writer to the
  recordings tree.
- Atomic replace via `os.replace(src, dst)` on POSIX (NOT
  `shutil.move`, which can fall back to copy+unlink across filesystems
  and lose atomicity).
- ffprobe-validate the `.stamped` output before replacing; if the output
  is shorter or missing streams, abort the replace and audit failure.
- `/data` is the only place the stamper writes; temp files go in the
  same directory as the source clip (so atomic-rename within filesystem
  is guaranteed).
- ffmpeg / ffprobe invocations: `subprocess.run([list], check=False,
  capture_output=True, timeout=...)`. Match `SnapshotExtractor`'s shape.
- Cross-spec dependency on #250 (clock-drift health): consume
  `TimeHealthService.get_state()` (or equivalent) at stamp start. If the
  service is unavailable (older deploy), record `clock_state: unknown`
  and continue.
- Tests + docs ship in the same PR as code, per
  `engineering-standards.md`. Operator help under `docs/guides/` adds a
  short "Reading the embedded timestamps" section with VLC and QuickTime
  screenshots.
- Traceability matrix updated in the same PR; `python
  tools/traceability/check_traceability.py` must pass.
