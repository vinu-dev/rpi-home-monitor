# Feature Spec: Offsite/Cloud Backup of Recordings (S3-Compatible)

## Title

Opt-in offsite backup of finalized clips to S3-compatible storage (MinIO, AWS S3, Backblaze B2, Wasabi, Storj, etc.) for tamper- and theft-resistance.

## Problem

A home monitoring system keeps recordings only on the local device. If the device is stolen, destroyed, or the SD card fails, recent forensic evidence is lost. Operators need an optional backup mechanism that mirrors finalized clips to cloud storage under their control, so loss of the local device does not mean loss of evidence.

## User Value

**Trust and resilience**: operators gain confidence that evidence persists even if the hardware is compromised, stolen, or fails. The product moves from "single point of failure" to "local + trusted backup."

**Operator-controlled storage**: no vendor lock-in or data harvesting. Operators choose where clips go (self-hosted MinIO, AWS S3, Backblaze, Wasabi, Storj, or any S3-API-compatible endpoint). The cloud is a *mirror*, not a primary — the local device remains the source of truth.

**Self-hosted ethos**: aligns with the mission to avoid telemetry and external dependencies. Backup is an opt-in, configurable feature that respects the local-first invariant.

## Scope

- Settings UI in Settings › Storage tab: enable/disable backup, S3 endpoint, bucket name, access credentials (access key ID / secret access key), object prefix, retention policy (keep for N days, or unlimited), bandwidth cap (MB/s, optional).
- Server-side offsite-backup service that monitors finalized clips and enqueues them for upload.
- Persistent queue on `/data` (JSON list of pending uploads) so backups survive service restart.
- Status surface in Storage tab: "Backup status" showing "Enabled/Disabled," queue size, last successful upload timestamp, next retry time if failed.
- Audit events: BACKUP_STARTED, BACKUP_SUCCESS, BACKUP_FAILED, BACKUP_CREDENTIALS_UPDATED, BACKUP_RETENTION_DELETED.
- Yocto packagegroup pull-in: boto3 or rclone backend (TBD — see open questions).
- No code in local.conf; policy lives in recipe/packagegroup.

## Non-Goals

- Cloud as primary target (local-first is non-negotiable).
- Vendor-specific cloud APIs (S3 compatibility is the baseline).
- Live remote viewing or streaming from the cloud copy.
- End-to-end encryption with operator-held keys (out of scope for this slice; follow-up issue).
- Mobile/desktop clients reading from the cloud copy directly.
- Cloud-side analytics, search, or deduplication.
- Automatic bandwidth throttling or scheduling (fixed cap is acceptable for v1).
- Retention sync: cloud retention is independent of local retention (operator manages separately).

## Acceptance Criteria

- **Settings persistence**: operator can update backup settings (endpoint, bucket, credentials, retention, bandwidth cap) and changes persist across restarts.
- **Queue-based upload**: finalized clips are enqueued for upload, processed in background without blocking local recording or UI.
- **S3 compatibility**: upload works with AWS S3, MinIO, Backblaze B2, and one other S3-compatible provider tested in smoke.
- **Failure resilience**: upload failure does not delete local clip or crash the service; failed uploads are retried with exponential backoff (up to N retries, then logged as permanent failure).
- **Credentials handling**: access key and secret key are stored encrypted on `/data` (using the same encryption model as existing secrets, if any); not logged in plaintext.
- **Local-first invariant**: backup never blocks local recording, never fills `/data` by queueing too many pending uploads, never deletes local clips to save space for backup.
- **Retention cleanup**: old clips are deleted from cloud storage according to the configured retention policy; local retention policy is separate and unchanged.
- **Status visibility**: operator can see backup enabled/disabled state, queue size, last upload timestamp, and any errors in the Storage tab.
- **Audit trail**: backup-related events are logged to audit.log (start, success, failure, credentials update, retention delete).

## User Experience

### Entry Point

Settings › Storage tab, new "Offsite Backup" section.

### Main Flow (Happy Path)

1. Operator opens Settings › Storage.
2. Sees "Offsite Backup" section with toggle: currently "Disabled."
3. Toggles to "Enable Offsite Backup."
4. Form appears: S3 endpoint (required, e.g., `s3.amazonaws.com` or `minio.example.com:9000`), bucket name (required), access key ID (required), secret access key (required, input type password), object prefix (optional, e.g., `backups/home-monitor/`), retention (dropdown: "Keep for 7 days / 30 days / 90 days / unlimited"), bandwidth cap (optional, MB/s, default unlimited).
5. Operator fills in and clicks "Test Connection."
6. Server attempts to create a test object in the bucket; returns "Connection OK" or error.
7. Operator clicks "Save."
8. Settings persist; backup service wakes up and begins uploading finalized clips.
9. Operator can see "Backup status: Enabled. Queue: 3 pending. Last upload: 2 minutes ago. Next retry: in 5 min."

### Success State

- Operator checks the remote bucket (MinIO UI, AWS Console, etc.) and sees clips arriving.
- Clips are organized by date or the operator's prefix.
- Local clips are unaffected; they retain their original retention schedule.

### Failure States

1. **Bad credentials**: server logs "BACKUP_FAILED: access denied to bucket" in audit.log; status shows "Error: Invalid credentials."
2. **Bucket does not exist**: server logs "BACKUP_FAILED: bucket not found"; operator must create it.
3. **Network timeout**: server retries with backoff; status shows "Last error: timeout, retrying in 30 seconds."
4. **Disk full on device**: backup is suspended until space is freed; local recording is never blocked.
5. **Operator disables backup**: service stops uploading; pending queue is preserved (re-enable to resume).

## Architecture Fit

### Existing Patterns Preserved

- **Service-layer architecture**: new `app/server/monitor/services/offsite_backup.py` handles queue, retry logic, S3 connectivity. Routes in `api/` are thin adapters.
- **App factory wiring**: offsite_backup service is instantiated in the Flask app factory and injected like StorageService, SettingsService, etc.
- **Mutable runtime state on `/data`**: persistent queue lives at `/data/config/offsite_backup_queue.json`; encrypted secrets in `/data/config/settings.json`.
- **Settings model extension**: Settings dataclass gains new fields: `offsite_backup_enabled`, `s3_endpoint`, `s3_bucket`, `s3_prefix`, `s3_access_key_id`, `s3_secret_access_key` (encrypted), `offsite_retention_days`, `bandwidth_cap_mbps`.
- **Audit logging**: offsite_backup service calls `audit.log_event()` for all state transitions.
- **Yocto policy in recipes/packagegroups**: boto3 (or rclone) pulled in via `packagegroup-monitor-base.bb`, not local.conf.

### New Service: Offsite Backup Service

**Responsibilities:**
- Monitor finalized clips: subscribe to CLIP_FINALIZED events (or periodically scan `/data/recordings/finalized/`).
- Enqueue pending uploads: append to `/data/config/offsite_backup_queue.json`.
- Background upload loop: wake every N seconds, pop items from queue, upload to S3, handle errors.
- Retry logic: exponential backoff (initial 1s, cap at 5 min) for up to 5 retries; then permanent failure.
- Retention cleanup: periodically query cloud storage and delete objects older than configured days.
- Bandwidth limiting: track upload rate and throttle if needed.

**Inputs:**
- SettingsService (read S3 credentials and retention policy).
- RecordingsService (read finalized clips).
- AuditLogger (log events).

**Outputs:**
- S3 uploads.
- Audit events (BACKUP_STARTED, BACKUP_SUCCESS, BACKUP_FAILED, etc.).
- Queue persistence.

## Module / File Impact List

- **app/server/monitor/models.py**: extend Settings dataclass with S3 config fields.
- **app/server/monitor/services/offsite_backup.py** (NEW): service for queue, upload, retention.
- **app/server/monitor/services/settings_service.py**: add validation for S3 endpoint, test connectivity.
- **app/server/monitor/api/settings.py**: GET `/settings/offsite-backup` (read S3 config + status), PUT (update, admin only), POST (test connection).
- **app/server/monitor/templates/settings.html** (or React component): new "Offsite Backup" section in Storage tab.
- **app/server/monitor/store.py**: schema migration to add offsite_backup_* fields to settings.json.
- **meta-home-monitor/recipes-core/packagegroups/packagegroup-monitor-base.bb**: add boto3 (or rclone) dependency.
- **app/server/tests/**: unit tests for queue logic, upload, retry, retention cleanup; integration test for S3 mock.

## Validation Plan

### Unit Tests (app/server/tests/)
- Queue serialization / deserialization.
- Retry logic: verify exponential backoff calculation.
- Retention cleanup: verify old objects are identified correctly.
- Settings validation: S3 endpoint format, bucket name format.
- Bandwidth limiting: verify rate-limit enforcement.

### Integration Tests
- S3 mock (moto or localstack): upload a clip, verify object lands in bucket with correct metadata.
- Failure scenario: S3 returns 403, verify retry queue is updated and audit event logged.
- Settings update: change S3 endpoint, verify service reads new config on next cycle.
- Queue persistence: restart service, verify pending queue is reloaded.

### Contract Tests
- `/settings/offsite-backup` GET returns status (enabled, queue size, last upload, error).
- `/settings/offsite-backup` PUT with invalid endpoint returns 400.
- `/settings/offsite-backup/test-connection` POST returns 200 on success, 5xx on auth/network failure.

### Smoke Test (on hardware)
- Configure MinIO (or Wasabi) endpoint and bucket.
- Generate a test clip (motion or on-demand).
- Wait 1–2 minutes.
- Verify clip appears in remote bucket.
- Disable backup, verify no new uploads.
- Re-enable, verify uploads resume.
- Delete old clips from local storage, verify cloud retention is independent.

### Validation Matrix Rows

Rows from `docs/ai/validation-and-release.md`:

- API behavior: unit + integration + contract tests.
- Security-sensitive path: S3 credentials handling, encryption at rest; code review of secrets storage.
- Yocto policy: `bitbake packagegroup-monitor-base` parses successfully; VM build includes boto3.

## Risk

### Hazards

1. **Credentials at rest**: S3 access key and secret are stored in `/data/config/settings.json`. If `/data` is not encrypted, attacker with filesystem access can steal credentials.
   - **Severity**: High (credentials grant write access to operator's remote bucket).
   - **Probability**: Medium (attacker needs filesystem access; SD card must be extracted and mounted).
   - **Control**: Use the same encryption model as existing secrets (if `/data` is encrypted, risk is mitigated). If not, escalate to security review.

2. **Uploading from a constrained device**: device has limited network and CPU. Large uploads or many pending uploads could:
   - consume CPU, starving local recording.
   - consume network, degrading streaming quality.
   - fill `/data` with pending queue if uploads are very slow.
   - **Severity**: Medium (local recording impact).
   - **Probability**: Medium (if bandwidth is extremely limited or S3 endpoint is unreachable for hours).
   - **Controls**: bandwidth cap (default unlimited; operator can cap). Pending queue depth limit (e.g., max 100 pending items; oldest are dropped if limit exceeded, logged as failure). Service is low-priority background task, not blocking.

3. **Misconfigured retention**: operator sets short retention on local but forgets to set on cloud, or vice versa. Clips disappear from cloud sooner than expected.
   - **Severity**: Low (operator owns the configuration; data is still on local device).
   - **Probability**: Medium (likely operator error).
   - **Control**: Settings UI clearly separates "Local Retention" and "Cloud Retention" sliders. Status page shows both. Docs explain they are independent.

4. **S3 credentials leaked in logs or error messages**: plaintext credentials in exception stack traces or debug logs.
   - **Severity**: High.
   - **Probability**: Low (if code is careful).
   - **Control**: Never log or repr S3 credentials. Mask in error messages (e.g., "connection failed: access denied" instead of "access_key_id=..."). Code review.

5. **Long-lived uploads block local file rotation**: if an upload takes 10 minutes and local clip rotation happens every 5 minutes, queue can grow unbounded.
   - **Severity**: Medium.
   - **Probability**: Low (uploads are typically fast; network interruptions are rare).
   - **Control**: Timeout on uploads (e.g., 5 min per clip). Retry logic handles failures gracefully. Queue depth limit.

### Risk Mitigation Summary

- **RC-001**: Encrypt `/data` or use existing secret storage pattern (e.g., bcrypt, encrypted config fields). Code review for credential handling.
- **RC-002**: Implement queue depth limit and bandwidth cap enforcement. Mark uploads as low-priority background tasks.
- **RC-003**: Clearly separate local and cloud retention in UI and docs.
- **RC-004**: Never log or repr S3 credentials. Mask errors. Code review.
- **RC-005**: Implement upload timeout and retry backoff. Monitor queue depth in status endpoint.

## Security

### Threat Model

1. **Attacker steals device → extracts SD card → reads `/data/config/settings.json`**: attacker gains S3 credentials, can read/write operator's remote bucket.
   - **Control**: `/data` encryption (existing, if enabled). Code review: credentials must not be logged or exposed in errors.
   - **Residual risk**: if `/data` is not encrypted, risk is high. Escalate to security review before shipping.

2. **Attacker performs MITM on network → intercepts S3 uploads**: attacker reads credentials or clips.
   - **Control**: Use HTTPS/TLS for S3 API calls (boto3 default). TLS certificate validation (boto3 default).
   - **Residual risk**: low (standard TLS practice).

3. **Attacker compromises S3 endpoint (not operator's fault)**: attacker can read/delete clips on remote bucket.
   - **Control**: Out of scope (operator's responsibility to secure their S3 endpoint). Recommend strong IAM policies in docs.
   - **Residual risk**: low for this product (attack is on operator's infrastructure, not the device).

4. **Operator provides wrong endpoint or bucket by mistake**: clips might be uploaded to an attacker's bucket.
   - **Control**: "Test Connection" button verifies endpoint and credentials before saving. Audit log records BACKUP_CREDENTIALS_UPDATED for every change.
   - **Residual risk**: low.

### Security Checklist

- ✓ No new auth/secrets surface required (uses existing Settings auth).
- ✓ S3 credentials must not be logged, printed, or exposed in errors.
- ✓ S3 credentials must be encrypted at rest (same as existing secret fields).
- ✓ No new endpoints that bypass the existing auth model.
- ✓ Audit events for all backup-related actions.
- ✓ Test connection validates credentials before save.

## Traceability

**Placeholder IDs** (Implementer fills in):

- **REQ-OffSite-001** (SWR): System shall allow operator to configure S3-compatible backup endpoint, bucket, and credentials via Settings UI.
- **REQ-OffSite-002** (SWR): System shall upload finalized clips to the configured S3 bucket in background without blocking local recording.
- **REQ-OffSite-003** (SWR): System shall persist the offsite-backup queue across service restarts so failed uploads are retried.
- **REQ-OffSite-004** (SWR): System shall enforce a configurable bandwidth cap for uploads.
- **REQ-OffSite-005** (SWR): System shall delete cloud clips according to a configurable retention policy independent of local retention.
- **REQ-OffSite-006** (SWR): System shall log backup start, success, failure, and credential updates to audit.log for accountability.
- **RISK-OffSite-001** (RISK): Credentials at rest on `/data` — mitigated by encryption (CONTROL-001).
- **RISK-OffSite-002** (RISK): Upload throughput competition with local recording — mitigated by bandwidth cap and queue depth limits (CONTROL-002).
- **SEC-OffSite-001** (SEC): S3 credentials must not be logged or exposed in error messages.
- **TEST-OffSite-001** (TC): Unit tests for queue, retry, retention cleanup logic.
- **TEST-OffSite-002** (TC): Integration test with S3 mock (moto) for upload success and failure scenarios.
- **TEST-OffSite-003** (TC): Smoke test on hardware with real S3 endpoint (MinIO or Wasabi).

## Deployment Impact

- **OTA required**: yes. Yocto recipe changes (boto3 or rclone added to image) require a new OS image build and OTA update.
- **Database migration**: schema migration for Settings dataclass (add S3 config fields). Migration script should handle upgrade from devices without offsite_backup_* fields.
- **Backwards compatibility**: devices on old firmware without offsite_backup_* fields should handle gracefully (e.g., settings defaults to offsite_backup_enabled=false).

## Open Questions

1. **Encryption backend**: boto3 or rclone?
   - *Pro boto3*: lightweight, direct S3 API, fewer dependencies.
   - *Pro rclone*: battle-tested, supports 40+ backends (not just S3), no custom code.
   - *Recommendation*: boto3 for this slice (simpler, smaller image). Rclone as a follow-up if multi-backend support is needed.

2. **Cloud-side retention**: should the service delete old objects from the remote bucket, or is that the operator's responsibility?
   - *Pro automated*: "fire and forget" — operator configures once, cloud retention is managed.
   - *Pro manual*: operator has full control; avoids accidental deletions.
   - *Recommendation*: implement automated deletion based on configured retention (days or unlimited). Provide a "dry run" mode in status so operator can preview what will be deleted.

3. **Schedule/bandwidth throttling**: should backup only run during off-peak hours, or respect a time-based quota?
   - *Pro scheduled*: night-time uploads don't compete with daytime streaming.
   - *Pro unlimited (but capped)*: simpler; bandwidth cap handles the constraint.
   - *Recommendation*: v1 does not implement scheduled backups. Bandwidth cap is sufficient. Follow-up issue for scheduling if needed.

4. **Incremental uploads**: should the service track which clips have been uploaded and skip re-uploads, or upload everything and let S3 dedup?
   - *Pro incremental*: avoids redundant uploads if service restarts.
   - *Pro S3 dedup*: simpler, leverages S3 features (versioning, smart tiering, etc.).
   - *Recommendation*: v1 uses a persistent "uploaded set" in the queue JSON (clip ID + hash). Implementer can optimize later.

5. **E2E encryption with operator-held keys**: out of scope for this slice. Recommend a follow-up issue (new feature post-v1).

6. **Retention cleanup strategy**: should old clips be deleted as soon as retention expires, or in bulk batches?
   - *Pro per-clip*: real-time, accurate.
   - *Pro batch*: fewer API calls, cheaper.
   - *Recommendation*: batch cleanup once per day (or configurable) to reduce API load.

## Implementation Guardrails

- Preserve the modular monolith architecture (service-layer pattern).
- Preserve the server/camera responsibility split.
- Do not add new long-lived daemons (use the same background loop pattern as other services, e.g., recorder, OTA).
- Keep the product local-first: local recording is never blocked or degraded by backup.
- Do not weaken auth, OTA, or device trust boundaries.
- Update tests and docs together with code.
- Mask S3 credentials in logs and error messages.
- Encrypt S3 secrets at rest (same pattern as existing secrets).
- Validate S3 endpoint and bucket before saving via test-connection endpoint.
