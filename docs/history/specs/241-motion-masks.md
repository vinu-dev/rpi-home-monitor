# Feature Spec: Per-Camera Motion Masks and Privacy Zones

Status: Ready for AI implementation planning
Priority: P2
Roadmap Slot: Release 1.5
Related Issue: [#241](https://github.com/vinu-dev/rpi-home-monitor/issues/241)

## Problem

Cameras installed outdoors or near shared spaces inevitably capture regions the operator never intended to monitor:
- A public street (legal privacy concern)
- A neighbor's window or property (ethical privacy concern)
- A constantly-moving area (swaying tree, flag, ceiling fan) that generates 90% false-positive motion events

Today, every motion trigger fires regardless of location in the frame, leading to:
- **Notification fatigue**: operators disable alerts or stop using the product
- **Privacy liability**: footage includes areas outside the operator's property or consent
- **Storage waste**: recordings include unwanted regions

Per `docs/ai/mission-and-goals.md`, the mission is "trustworthy, self-hosted home monitoring system that feels like a real product." Trust collapses when motion detection is noisy and privacy is not transparent.

## User Value

- **Noise reduction**: exclude swaying trees, moving curtains, and other constant motion sources
- **Privacy by design**: visually redact (black box or blur) regions in live view and recordings so footage of neighbors or public areas is never captured
- **Competitive parity**: Frigate, MotionEye, ZoneMinder, and Shinobi all support masking and zones
- **Self-hosted differentiator**: cloud DVRs do not offer transparent per-camera privacy redaction; this is a competitive advantage for self-hosted systems
- **Operator confidence**: operators control exactly what regions are monitored and recorded

## Scope

This slice delivers per-camera motion masks (v1) and privacy zones (v1):

### Included

- **Motion mask**: operator draws rectangular or polygonal exclude regions on camera preview; motion in those areas is ignored for detection
- **Privacy zone**: subset of motion mask that is also visually redacted (black box or blur) in live view and recordings
- **Per-camera editor**: mask editor on Settings → Cameras → Camera → Motion Mask tab (existing settings surface)
- **Mask preview**: real-time preview overlay on camera live view showing current mask regions
- **Mask persistence**: masks are saved per-camera in the database schema
- **Motion detection integration**: motion runner applies the mask before emitting motion events (excludes motion in masked regions)
- **Recording integration (v1)**: apply privacy-zone redaction to recordings and live view (frame-level annotation; blur or black-box rectangles)
- **API exposure**: mask configuration accessible through the camera config API
- **Audit trail**: mask edits are logged (create, update, delete)

### Phase 2 (Not in v1)

- Per-time-of-day or per-schedule mask variation (e.g., mask the driveway during business hours only)
- Importing/exporting masks across cameras
- Polygon-with-holes or curved regions
- Automated motion-history heatmap to suggest mask regions
- Machine-learning-assisted region suggestion (e.g., "detected constant motion here; mask?")

### Non-Goals

- Object-class detection (person/vehicle/pet filtering) — separate ML feature
- Hardening against adversarial mask evasion (out of scope for v1)
- Cloud-based mask templates or sharing
- Mask encryption or per-mask access control (masks are per-camera settings, subject to existing admin role control)

## Architecture Fit

Per `docs/ai/design-standards.md`, this feature fits the existing architecture:

- **Service-layer**: motion mask configuration and retrieval in `camera_service.py` (existing service)
- **Runtime mutable state on `/data`**: mask definitions are stored in the database (per-camera config)
- **Camera lifecycle**: motion detection logic is in the camera streamer; mask is applied at motion-event emission (no state-machine changes to lifecycle.py)
- **No camera pairing changes**: masks are server-side metadata; cameras do not need to be aware of mask semantics
- **Existing settings UI pattern**: mask editor follows the same Settings → Cameras pattern as existing per-camera config
- **No Yocto changes**: this is a runtime server and camera feature, not image policy

## Technical Approach

### Mask Representation

A mask is defined as:

```json
{
  "camera_id": "uuid",
  "type": "motion_mask" | "privacy_zone",
  "name": "Exclude tree",
  "regions": [
    {
      "shape": "rectangle" | "polygon",
      "coordinates": {
        "x": 0-100,
        "y": 0-100,
        "width": 0-100,
        "height": 0-100
      }
    }
  ],
  "enabled": true,
  "redaction_type": "blur" | "black_box" | null
}
```

Coordinates are percentages (0-100) to be resolution-agnostic; on application, they are scaled to the actual frame size.

### Motion Detection Integration

The motion detection pipeline in `app/camera/camera_streamer/motion.py` and `motion_runner.py`:

1. Receives frame from camera
2. Applies motion mask (excludes masked regions before motion analysis)
3. Emits motion events only for unmasked regions
4. Records motion region metadata (which part of the frame triggered)

### Privacy Zone Redaction

For recordings and live view:

1. Identify privacy zones from camera config
2. For each frame:
   - Detect privacy-zone regions (scaled to frame resolution)
   - Apply redaction (blur or black-box): OpenCV `GaussianBlur()` or `rectangle()`
   - Write redacted frame to recording or live stream

Redaction is applied at frame processing time, before encoding to H.264 / VP9 (part of the existing recording pipeline).

### Database Schema

New/modified tables:

- `motion_masks` (new):
  - `id` (UUID)
  - `camera_id` (foreign key to cameras)
  - `type` (enum: motion_mask, privacy_zone)
  - `name` (string)
  - `regions` (JSON array of shape + coordinates)
  - `enabled` (boolean)
  - `redaction_type` (enum: blur, black_box, null; applies only to privacy_zone)
  - `created_at`, `updated_at` (timestamps)

- `cameras` (existing):
  - No schema changes; motion_masks are referenced by camera_id

### API Changes

New endpoints (or extensions to existing camera API):

- `GET /api/cameras/{id}/motion-masks` — list masks for camera
- `POST /api/cameras/{id}/motion-masks` — create motion mask
- `PATCH /api/cameras/{id}/motion-masks/{mask_id}` — update mask
- `DELETE /api/cameras/{id}/motion-masks/{mask_id}` — delete mask

Existing endpoint `GET /api/cameras/{id}` expands to include `motion_masks` array.

### UI Flow

#### Entry Point
Settings → Cameras → Select Camera → Motion Mask tab

#### Mask Editor
1. Live camera preview in the right panel
2. Overlay showing current masks (motion_mask in yellow outline, privacy_zone in red outline with redaction preview)
3. Toolbar:
   - `Draw rectangle` — drag on preview to draw rectangular mask
   - `Draw polygon` — click points on preview to draw polygon
   - `Delete` — select region and delete
   - `Type` — toggle selected region between motion_mask and privacy_zone
4. Settings panel for selected region:
   - Name
   - Type (motion_mask / privacy_zone)
   - Redaction type (blur / black_box, if privacy_zone)
   - Enabled toggle
5. `Save` button to persist changes

#### Mask Preview in Live View
- Small indicator badge on live view: "🚫 2 masks active"
- On-demand overlay toggle: show/hide current masks on live view

## User-Facing Behavior

### Primary Path: Create a Motion Mask

1. Operator navigates to Settings → Cameras → "Driveway" → Motion Mask
2. Live preview of camera feed is displayed
3. Operator clicks `Draw Rectangle` tool
4. Operator drags a rectangle over the swaying tree in the frame
5. System shows a yellow overlay outline of the rectangle
6. Operator names the mask: "Exclude swaying tree"
7. Operator clicks `Save`
8. System shows a success toast: "Motion mask created. Motion in this region will be ignored."
9. Mask is now active; motion events from that region will not trigger alerts or recordings

### Primary Path: Create a Privacy Zone

1. Operator navigates to Settings → Cameras → "Front Door" → Motion Mask
2. Operator clicks `Draw Rectangle` tool
3. Operator drags a rectangle over the neighbor's window (visible in the frame)
4. System shows a yellow overlay outline
5. Operator clicks `Type` dropdown → selects `Privacy Zone`
6. Overlay changes to red; a preview of redaction appears (blur or black-box)
7. Operator selects redaction type: `Blur` or `Black Box`
8. Operator names the zone: "Neighbor's window — redacted"
9. Operator clicks `Save`
10. System confirms: "Privacy zone created. This region will be blurred in live view and recordings."
11. Live view now shows the neighbor's window blurred in real-time

### Failure States

- **Mask overlaps or conflicts**: If two masks overlap, both are applied (OR logic). UI shows overlapping regions with a warning.
- **Preview lag**: If live preview is slow or unavailable, the mask editor shows a still frame; operator can still draw, but accuracy may suffer. UI shows: "Live preview unavailable; using last captured frame."
- **Apply failure (motion runner down)**: If motion detection is paused or the camera streamer crashes, the mask is not applied. Motion detection resumes and applies the mask when the streamer recovers. Audit log records the event.
- **Redaction performance**: If redaction is too slow (blur on every frame), the frame rate may drop. UI warns: "High redaction load; consider using black boxes instead of blur." Admin can disable real-time redaction and apply it only to recordings (future optimization).

## Acceptance Criteria

- An operator can draw rectangular motion masks and privacy zones on a camera preview.
- Masks are persisted to the database and survive system restart.
- Motion detection respects masks: motion events in masked regions do not trigger alerts or recordings.
- Privacy zones are visually redacted (blur or black-box) in live view and recordings.
- Mask editor shows real-time preview of masks on the live camera feed.
- API exposes mask configuration for each camera (GET /api/cameras/{id}/motion-masks).
- Mask creation and modification are logged in the audit trail.
- Multiple masks per camera are supported (e.g., exclude tree AND redact neighbor window).
- Masks survive camera reconnection (not lost when camera goes offline/online).
- Disabling a mask (via toggle) temporarily stops its application without deletion.
- Operator can export mask configuration (future: per-camera or system-wide export).
- UI clearly distinguishes motion masks (yellow, motion-only) from privacy zones (red, motion + redaction).
- On-camera-settings change (resolution, frame rate), masks are preserved and dynamically scaled.
- Performance: mask application adds <5% CPU overhead to motion detection; redaction adds <10% overhead to frame encoding.

## Module / File Impact List

### Server-Side Changes

- `app/server/monitor/services/camera_service.py`
  - Add `get_camera_masks()`, `create_mask()`, `update_mask()`, `delete_mask()`
  - Schema handling for motion_masks table

- `app/server/monitor/api/cameras.py`
  - New endpoints: POST/PATCH/DELETE `/api/cameras/{id}/motion-masks`
  - Expand GET `/api/cameras/{id}` to include `motion_masks` array

- `app/server/monitor/models/`
  - Add `MotionMask` model (SQLAlchemy ORM)
  - Add `motion_masks` relationship to `Camera` model

- `app/server/monitor/templates/settings.html`
  - New tab: "Motion Mask" under camera settings
  - Mask editor UI with canvas preview, draw tools, settings panel

- `app/server/monitor/static/js/mask-editor.js` (new)
  - Canvas-based drawing for rectangles and polygons
  - Real-time mask preview overlay
  - Interaction handlers

- `app/server/monitor/services/audit.py`
  - Integration: log mask creation, update, deletion with camera_id and operator

- Database migrations
  - Create `motion_masks` table with schema above

### Camera-Side Changes

- `app/camera/camera_streamer/motion.py`
  - New function: `apply_motion_mask(frame, masks, frame_shape)`
  - Integration: before motion analysis, apply mask to exclude regions
  - Coordinate scaling: convert percentage-based mask coordinates to frame pixel coordinates

- `app/camera/camera_streamer/motion_runner.py`
  - Load masks from server config (fetch via camera_service API or config sync)
  - Pass masks to motion detection pipeline
  - Log mask application (debug)

- `app/camera/camera_streamer/recording.py` (if exists) or recording pipeline
  - Load privacy zones from camera config
  - For each frame: apply redaction before encoding
  - New function: `apply_privacy_redaction(frame, zones, redaction_type)`

- `app/camera/camera_streamer/lifecycle.py`
  - On camera config update: refresh masks (no state-machine change; add mask-refresh callback)

### Tests

- `app/server/tests/test_camera_service.py`
  - Unit tests: mask CRUD operations, schema validation

- `app/server/tests/test_masks_api.py` (new)
  - Contract tests: POST/PATCH/DELETE endpoints, error cases

- `app/camera/tests/test_motion_mask_integration.py` (new)
  - Integration: verify motion detection respects masks
  - Synthetic frame + mask → check motion events are filtered

- `app/camera/tests/test_privacy_redaction.py` (new)
  - Unit: redaction application (blur/black-box frame analysis)
  - Verify redacted regions are actually blurred/black-boxed

- `tests/smoke_test.py` (extension)
  - End-to-end: create mask → trigger motion → verify no alert
  - End-to-end: create privacy zone → capture frame → verify redacted in recording

### Documentation

- `docs/guides/camera-masks-guide.md` (new)
  - Operator guide: how to create masks, best practices, troubleshooting
  - Examples: excluding swaying trees, redacting neighbors

- `docs/api/cameras.md` (updated)
  - New API endpoints for mask management

- Inline code comments (REQ annotations)
  - Mark motion mask application points with `REQ: SWR-###`
  - Mark privacy redaction points with `SEC: SEC-###`

## Validation Plan

Per `docs/ai/validation-and-release.md`:

| Area | Required Validation | Evidence |
|---|---|---|
| Schema changes | Parse + migration test | DB migration runs without error; tables exist post-migration |
| Service logic | Unit tests (mask CRUD, coordinate scaling) | `pytest app/server/tests/test_camera_service.py -v --cov-fail-under=85` |
| API behavior | Contract tests (POST/PATCH/DELETE endpoints, permissions) | HTTP contract test suite; admin-only enforcement |
| Motion detection integration | Integration test (frame + mask → motion excluded) | `pytest app/camera/tests/test_motion_mask_integration.py -v` |
| Redaction logic | Unit + visual tests (blur/black-box applied correctly) | `pytest app/camera/tests/test_privacy_redaction.py -v` + frame inspection |
| UI interaction | Manual browser walkthrough (draw, edit, save, preview) | Smoke test: create mask via UI, verify in live view |
| Performance | Profiling (mask application + redaction overhead) | CPU usage <5% overhead for mask; <10% for redaction |
| End-to-end | Integration (create mask → motion trigger → no alert; create privacy zone → redacted in recording) | `pytest tests/smoke_test.py -v -k mask` |
| Audit trail | Log inspection (mask events recorded) | Audit log contains create/update/delete entries |
| Permission | RBAC test (non-admin cannot modify masks) | API returns 403 for non-admin requests |

## Risk

### ISO 14971-Lite Framing

| Hazard ID | Hazard | Severity | Probability | Proposed Risk Control | Residual Risk |
|---|---|---|---|---|---|
| HAZ-001 | Operator accidentally masks critical region (e.g., main entrance), disabling motion detection for that area. Intruder enters undetected. | High | Medium | Mask editor preview shows mask overlay on live feed. Operator should verify mask placement before saving. Runbook advises testing mask with manual motion trigger (walk past camera). Audit log records mask creation (reviewable). | Medium → Low (with operator care) |
| HAZ-002 | Mask editor canvas is unresponsive or lags; operator draws mask in wrong location. | Medium | Low | Real-time preview with visual feedback (yellow outline). Test on target hardware. If lag detected, show warning: "Preview is lagging; accuracy may suffer." | Low (preview + warning) |
| HAZ-003 | Motion runner crashes or fails to load masks; motion detection reverts to unmasked (all regions trigger). Operator loses confidence or receives noise. | Medium | Low | Motion detection is robust to missing masks (graceful degradation: apply masks only if loaded; otherwise, all motion detected). Audit log records failure. | Low (graceful fallback) |
| HAZ-004 | Privacy redaction is applied incorrectly (blur/black-box in wrong region or missed region). Sensitive neighbor footage is recorded unredacted. | High | Low | Redaction pipeline tested with synthetic frames; coordinate scaling verified (unit tests). Manual spot-check of recorded frame redaction before release. | Low (testing + review) |
| HAZ-005 | Operator masks a region, but intruders use that region to approach undetected. Theft or intrusion. | High | Medium | Operator responsibility. Runbook emphasizes "masks exclude regions from motion detection; use only for legitimate noise sources (trees, curtains)." Audit log is transparent. Cannot prevent misuse, but documentation and audit trail support post-incident investigation. | Medium (operator responsibility) |
| HAZ-006 | Mask data is lost during system update or corruption. Operator's mask configuration is gone; must reconfigure. | Low | Very Low | Masks are stored in database (persistent). Database backups are managed by system backup feature (issue #240). | Low (DB persistence) |
| HAZ-007 | Mask performance degrades with many masks per camera (e.g., 10+ masks on one camera). Frame processing stalls. | Medium | Low | Masks are applied as a single operation (bitmap OR of all regions); performance is O(1) per frame relative to mask count. Profiling during implementation. Document limits (e.g., "supports up to 20 masks per camera"). | Low (efficient algorithm) |

**Risk Controls to Implement:**

- Real-time mask preview overlay on editor and live view.
- Graceful fallback: motion detection works even if masks fail to load.
- Comprehensive testing: motion event filtering, redaction accuracy, performance.
- Operator documentation: runbook on mask placement, testing, and audit trail.
- Audit trail: all mask changes recorded with operator and timestamp.

### Outstanding Risks

- **Intruder use of masked region**: operator masks legitimate region but intruder exploits it. Residual: operator responsibility. Mitigation: runbook emphasizes proper placement; audit trail supports investigation.
- **Redaction performance on weak hardware**: if target device cannot sustain redaction at 30 FPS, frame rate drops. Residual: may need optimization or admin setting to disable real-time redaction (apply only to recordings). TBD in implementation.

## Security

### Threat Model

| Threat ID | Threat | Attacker | Impact | Control | Residual |
|---|---|---|---|---|---|
| THREAT-001 | Attacker gains server access and modifies mask data to remove privacy zones, exposing sensitive footage. | Insider / compromised server | Exposure of neighbor footage, privacy violation. | Masks are part of camera config; existing RBAC applies (admin role required to modify). Audit trail logs all modifications. | Low (RBAC + audit) |
| THREAT-002 | Attacker crafts malformed mask JSON (e.g., negative coordinates, huge polygon) to cause motion runner crash or frame-buffer overflow. | Remote (via compromised API) or Local | Denial of service (motion detection down), potential code execution. | Input validation: coordinates bounded 0-100, polygon points <100, shapes whitelist (rectangle/polygon only). Unit tests for malformed masks. | Low (validation) |
| THREAT-003 | Attacker crafts mask to render a malicious image on the operator's screen (e.g., obscene overlay) via the editor UI. | Remote (compromise API) | Operator distress, social engineering. | Masks are geometric regions only (coordinates, no image/text embedding). Rendering is server-side (preview) and client-side canvas (no HTML injection). | Low (data model) |
| THREAT-004 | Privacy zone redaction is applied only to live view, but not recordings. Attacker reviews recordings and sees unredacted sensitive area. | Configuration error or code bug | Privacy violation. | Redaction pipeline applies to both live view AND recordings (frame-level, before encoding). Code review and integration tests verify both paths. | Low (design + testing) |
| THREAT-005 | Non-admin user exports mask config (via API) and learns camera layout and blind spots. | Unprivileged user | Information leakage (camera topology). | Mask endpoints are admin-only (role check). GET /api/cameras/{id}/motion-masks requires admin. | Low (RBAC) |

### Sensitive Paths

This feature touches:

- **`app/server/monitor/api/cameras.py`** (mask endpoints): must enforce admin-only access control.
- **`app/camera/camera_streamer/motion.py`** (mask application): must validate coordinate ranges and handle malformed masks safely.
- **`app/camera/camera_streamer/recording.py`** (redaction): must apply redaction correctly and not leak unredacted frames.
- **Database**: mask data is stored with camera config; no additional encryption needed (subject to existing DB security).

### Code Annotation Pattern

Every traceable code must include a `REQ:` annotation, e.g.:

```python
# REQ: SWR-0XX — apply motion mask to frame before motion detection
def apply_motion_mask(frame, masks, frame_shape):
    ...

# REQ: SEC-0XX — validate mask coordinates to prevent buffer overflow
def validate_mask(mask):
    ...
```

## Traceability

### Requirements to be filled in during implementation

| Type | ID | Title | Status |
|---|---|---|---|
| User Need | UN-XXX | Operator can exclude noisy motion regions (trees, curtains) from motion detection | Open |
| User Need | UN-XXX | Operator can redact sensitive regions (neighbor windows, streets) from live view and recordings | Open |
| System Requirement | SYS-XXX | System shall support per-camera motion masks and privacy zones | Open |
| Software Requirement | SWR-XXX | Motion detection shall respect per-camera masks and exclude masked regions from motion events | Open |
| Software Requirement | SWR-XXX | Live view and recordings shall apply privacy-zone redaction (blur or black-box) | Open |
| Software Requirement | SWR-XXX | Mask editor shall provide real-time preview of masks on camera feed | Open |
| Software Requirement | SWR-XXX | Masks shall be persisted to database and survive system restart | Open |
| Software Requirement | SWR-XXX | Mask configuration shall be accessible via API | Open |
| Security Requirement | SEC-XXX | Mask configuration changes shall be restricted to admin role | Open |
| Security Requirement | SEC-XXX | Mask data shall be validated (coordinate ranges, shape types) to prevent malformed data | Open |
| Security Requirement | SEC-XXX | Privacy-zone redaction shall be applied to both live view and recordings | Open |
| Architecture | ARCH-XXX | Motion masks use per-camera persistent storage (database) | Open |
| Architecture | SWA-XXX | Motion detection applies masks before event emission (motion.py integration) | Open |
| Architecture | SWA-XXX | Redaction is applied at frame level before encoding (recording/live pipeline) | Open |
| Architecture | SWA-XXX | Mask coordinates are percentage-based (0-100) for resolution independence | Open |
| Risk | RISK-XXX | Masking legitimate region disables motion detection for that area | Open |
| Risk | RISK-XXX | Privacy redaction may degrade frame rate or motion detection performance | Open |
| Risk Control | RC-XXX | Real-time mask preview helps operator verify mask placement | Open |
| Risk Control | RC-XXX | Audit trail logs all mask configuration changes | Open |
| Risk Control | RC-XXX | Graceful fallback: motion detection works even if mask loading fails | Open |
| Test Case | TC-XXX | Motion in masked region does not trigger motion event | Open |
| Test Case | TC-XXX | Privacy zone redaction is applied to recorded frames | Open |
| Test Case | TC-XXX | Mask editor preview correctly shows mask overlay on live feed | Open |
| Test Case | TC-XXX | Mask data survives system restart | Open |
| Test Case | TC-XXX | Non-admin user cannot create or modify masks | Open |

## Deployment Impact

### OTA and Update Path

- **Server changes only**: motion mask and privacy zone logic is server-side and camera-side Python. No Yocto recipe changes (Python modules are already deployed).
- **Database migration**: new `motion_masks` table. Migration runs automatically on first startup after update.
- **No firmware changes**: camera runtime supports mask application via existing motion detection pipeline.
- **Backward compatibility**: systems without masks work normally (masks are optional per-camera). New masks are ignored by older camera code (graceful degradation).
- **API expansion**: new mask endpoints are additions; existing endpoints (GET cameras) gain mask field. Older clients ignore new field (backward compatible).

### Rollback Path

- If mask feature is rolled back (reverted), masks are not applied, but data remains in database (no harm).
- API clients using mask endpoints will get 404 (expected).

## Open Questions

### Non-Blocking

1. **Redaction library**: Use OpenCV (already a dependency?) for blur/black-box, or implement custom? OpenCV is more efficient, but adds build complexity. Recommend: check if OpenCV is already in Yocto recipes; if so, use it; else evaluate custom frame pixelation.

2. **Polygon simplification**: Polygon masks with 100+ points may degrade performance. Recommend: limit polygon points to 50; provide polygon simplification as admin feature (future: Douglas-Peucker algorithm).

3. **Mask naming and persistence**: Mask names are operator-provided (e.g., "Exclude tree"). Should names be unique per camera? Recommend: allow duplicate names; use UUID for identity; UI shows names for usability.

4. **UI framework for canvas editor**: Use Konva.js, Fabric.js, or vanilla Canvas API? Recommend: Konva.js if not already in use; evaluate project dependencies. If already in use elsewhere, reuse; else vanilla Canvas with careful UX testing.

5. **Mask inheritance**: If operator duplicates a camera config, should masks be copied? Recommend: yes, copy masks from source camera to new camera (operator expectation).

6. **Real-time redaction vs. recording-only**: Should privacy zones redact live view in real-time? Concern: performance. Recommend: v1 implements both; admin can disable real-time redaction and apply only to recordings (future optimization: admin setting).

7. **Redaction type in live view**: Should live view show "blur" or "black-box"? Recommend: operator choice at mask creation time; consistent with recording (same redaction type for both).

8. **Mask metrics**: Should motion detection emit "motion detected but masked" as a separate metric? Recommend: future enhancement; v1 counts masked-out motion as "ignored" in telemetry (if telemetry exists).

---

**No blocking questions; ready to proceed with implementation.**
