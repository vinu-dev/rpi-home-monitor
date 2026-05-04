# Feature Spec: Offsite / Cloud Backup of Recordings

## Title

Opt-in offsite backup of finalized clips to S3-compatible storage.

## Problem

A local-only recorder is a single point of failure. If the device is
stolen, destroyed, or its storage fails, recent evidence can be lost
with it. Operators need an optional mirror that keeps finalized clips
in storage they control without turning the cloud into the system's
primary dependency.

## User Value

**Trust and resilience**: evidence survives local hardware loss.

**Operator-controlled storage**: clips can be mirrored to MinIO, AWS
S3, Backblaze B2, Wasabi, Storj, or another S3-compatible provider
chosen by the operator.

**Local-first behavior**: the appliance still records and manages clips
locally. Remote storage is a background copy, not the source of truth.

## Scope

- Settings UI in Settings > Storage for enable/disable, endpoint,
  bucket, access key, secret key, object prefix, retention days, and an
  optional bandwidth cap.
- Admin-only API endpoints to read redacted backup settings, persist new
  settings, and test remote connectivity.
- A server-side background service that scans finalized clips, persists
  a retry queue on `/data`, uploads pending clips, and performs
  retention cleanup on the remote bucket.
- Status reporting in the Storage tab: enabled/disabled, queue size,
  queue limit, last success timestamp, next retry timestamp, and last
  error.
- Audit events for upload start, success, failure, credential updates,
  queue overflow, and retention deletion failures/successes.
- Yocto-backed server dependency inclusion for `boto3` via the server
  packagegroup and recipe.

## Non-Goals

- Cloud as the primary recording target.
- Vendor-specific storage integrations beyond S3-compatible APIs.
- Remote playback or streaming from the cloud copy.
- End-to-end encryption with operator-held keys.
- Time-of-day scheduling for uploads.
- Cloud-side analytics, search, or deduplication features.

## Acceptance Criteria

- **Settings persistence**: operators can save endpoint, bucket,
  credentials, prefix, retention, and bandwidth settings, and the values
  survive restart.
- **Redaction**: secret material is never returned in plaintext from the
  settings API and is never logged verbatim.
- **Queue-based upload**: finalized clips are queued and uploaded in the
  background without blocking recording or the UI.
- **Failure resilience**: failed uploads remain local, are retried with
  backoff, and do not crash the service.
- **Retention cleanup**: when retention is configured, remote objects
  older than the cutoff are removed without affecting local retention.
- **Status visibility**: operators can see whether backup is enabled,
  queue depth, last success, next retry, and the latest error state.
- **Connectivity check**: operators can test HTTPS connectivity and
  credentials before saving or after updating settings.
- **Local-first invariant**: backup never deletes local clips, never
  blocks local recording, and bounds pending queue growth.

## User Experience

### Entry Point

Settings > Storage, in a new "Offsite Backup" section visible to admins.

### Main Flow

1. The operator enables offsite backup.
2. The operator enters an HTTPS endpoint, bucket, credentials, optional
   prefix, retention days, and optional bandwidth cap.
3. The operator uses "Test Connection" to verify bucket access.
4. The operator saves the configuration.
5. Finalized clips begin uploading in the background.
6. The Storage tab shows queue and health status as uploads progress.

### Failure States

1. Bad credentials: the connection test or upload fails with a redacted
   access-denied style error.
2. Missing bucket: the connection test or upload reports that the bucket
   could not be found.
3. Endpoint unavailable: retries are scheduled with backoff and surfaced
   in status.
4. Queue overflow: the oldest pending items are dropped and the event is
   audited rather than allowing unbounded growth.

## Architecture Fit

### Existing Patterns Preserved

- **Service-layer architecture**: business logic lives in
  `app/server/monitor/services/offsite_backup.py`; routes remain thin.
- **App factory wiring**: the service is created and started through the
  existing Flask app lifecycle in `app/server/monitor/__init__.py`.
- **Mutable runtime state on `/data`**: queue state is stored in
  `/data/config/offsite_backup_queue.json`; persisted settings remain in
  `/data/config/settings.json`.
- **JSON/dataclass migration tolerance**: existing settings loading
  fills defaults for new `offsite_backup_*` fields without a separate
  migration script.
- **Yocto policy**: dependency changes stay in recipes/packagegroups,
  not `local.conf`.

### Service Responsibilities

- Scan finalized recordings under the configured recordings directory.
- Maintain a persistent pending queue and uploaded index.
- Upload eligible clips to S3-compatible storage using `boto3`.
- Apply retry backoff and cap retry attempts.
- Enforce queue and failed-item limits.
- Periodically delete expired remote objects when retention is enabled.
- Surface status and emit audit events without exposing credentials.

## Module / File Impact List

- `app/server/monitor/models.py`
- `app/server/monitor/__init__.py`
- `app/server/monitor/api/settings.py`
- `app/server/monitor/services/offsite_backup.py`
- `app/server/monitor/templates/settings.html`
- `app/server/tests/unit/test_offsite_backup_service.py`
- `app/server/tests/integration/test_api_offsite_backup.py`
- `app/server/tests/contracts/test_api_contracts.py`
- `app/server/tests/integration/test_api_blueprints.py`
- `app/server/tests/integration/test_views.py`
- `app/server/tests/security/test_security.py`
- `app/server/tests/unit/test_models.py`
- `app/server/tests/unit/test_store.py`
- `app/server/requirements.txt`
- `app/server/setup.py`
- `meta-home-monitor/recipes-core/packagegroups/packagegroup-monitor-web.bb`
- `meta-home-monitor/recipes-monitor/monitor-server/monitor-server_1.0.bb`

## Validation Plan

### Unit Tests

- Queue persistence, upload success, retry state, retention cleanup, and
  secret redaction in `test_offsite_backup_service.py`.
- Settings and store defaults/round-tripping in model/store tests.

### Integration Tests

- Auth/admin behavior, persistence, redaction, validation, and
  connection testing through the settings API.
- Settings UI rendering coverage for the Storage tab controls.

### Contract / Security Tests

- Exact response fields for the new settings endpoints.
- Auth and CSRF coverage for the new routes.

### Release / System Validation

- `bitbake -p` for the touched Yocto layer set.
- Standard server validation gate from `docs/ai/roles/implementer.md`.
- Hardware smoke test with a real S3-compatible endpoint remains
  required before release.

## Risk

### Hazards

1. **Credentials at rest**: S3 credentials live in
   `/data/config/settings.json`.
   Control: rely on the existing device storage protection boundary,
   redact secrets from APIs/logs, and document the residual risk until a
   field-level wrapping design exists.
2. **Upload contention**: background uploads can compete with a
   constrained device's network or CPU.
   Control: bounded uploads per cycle, optional bandwidth cap, queue
   limit, and retry backoff.
3. **Operator retention mistakes**: cloud and local retention are
   independent.
   Control: separate settings and explicit status fields.
4. **Secret leakage in logs or responses**: debug output or errors could
   expose credentials.
   Control: redact secret values and return friendly error classes
   rather than raw provider errors.

## Security

### Threat Model

1. Device theft with filesystem access can expose stored S3 credentials.
   Residual risk remains if the device storage protections are absent or
   bypassed.
2. Man-in-the-middle attempts on the S3 link are mitigated by requiring
   HTTPS endpoints and normal TLS validation through the S3 client.
3. Misdirected endpoints or buckets can send data to the wrong remote
   target.
   Control: explicit test-connection flow before save.

### Security Checklist

- No new authentication bypasses.
- Secrets are not returned in plaintext by the API.
- Secrets are not written to logs or audit details.
- Backup configuration is admin-only and CSRF-protected.
- HTTPS is required for configured endpoints.

## Traceability

Implementation annotations map issue `#243` onto the existing
controlled traceability catalogue:

- Settings UI, admin API, and redacted configuration persistence:
  `SWR-024`, `RISK-012`, `SC-012`, verified by `TC-023` and `TC-041`.
- Background upload, retry, retention, and status reporting behavior:
  `SWR-057`, `RISK-017`, `RISK-020`, `SC-020`, verified by `TC-049`.
- Public API contract surface for the offsite-backup endpoints:
  `SWR-045`, `RISK-021`, `SC-021`, verified by `TC-042`.

Issue-local design notes in this spec intentionally roll up to those
controlled IDs instead of creating new global IDs for this slice.

## Deployment Impact

- **OTA required**: yes. Adding `boto3` to the server image requires a
  new image build and OTA rollout.
- **Data migration**: no standalone migration script is required; new
  settings fields rely on dataclass defaults during load.
- **Backwards compatibility**: older settings files load with backup
  disabled by default.
- **Release planning**: this is an issue-scoped spec. Release-wave
  assignment must be tracked in release planning separately from this
  implementation branch.

## Open Questions

1. Should future work add field-level secret wrapping beyond the current
   `/data` storage boundary?
2. Is a broader backend abstraction needed later for non-S3 cloud
   targets, or is S3 compatibility sufficient for the product roadmap?
3. Does the operator need scheduled upload windows, or is the current
   bandwidth-cap model enough?

## Implementation Guardrails

- Preserve the modular monolith and service-layer pattern.
- Keep local recording authoritative.
- Do not add a new daemon or external control plane.
- Keep mutable runtime state on `/data`.
- Update docs and tests in the same branch as code.
- Treat provider errors and remote content as untrusted input.
