# Feature Spec: Config Backup and Restore

Status: Ready for AI implementation planning
Priority: P1
Roadmap Slot: Release Next
Backlog Source: [market-feature-backlog-100.md](../planning/market-feature-backlog-100.md)
Related Issue: [#240](https://github.com/vinu-dev/rpi-home-monitor/issues/240)

## Problem

A silent failure is worse than no failure. When an SD card fails or hardware is migrated, an admin faces full re-onboarding: re-pairing every camera, re-tuning every threshold, re-entering every user credential, re-configuring retention and alert policy. That's not a product—it's a prototype.

Every appliance-class peer (Synology, UniFi, Home Assistant) ships configuration backup and restore. Users expect it. Today, it is the missing link between "feels like a real product" and "is a real product."

## User Value

- **Disaster recovery**: After SD-card failure, hardware migration, or factory reset, the system returns to its prior state without manual re-onboarding.
- **System confidence**: Admins know they can recover without data loss.
- **Competitive parity**: Home Assistant, Synology, and UniFi all offer this. It is now table stakes.
- **Operational realism**: Deployment paths must be real; recovery is part of real deployment.

Per the mission: "trustworthy, self-hosted home monitoring system that feels like a real product."

## Scope

This slice delivers admin-initiated export and import of signed configuration bundles:

Included:
- export full or partial configuration (users, cameras, settings, retention policy, alert thresholds, OTA channel selection)
- signed manifest and integrity verification
- schema versioning and version mismatch rejection
- per-secret-class policy (pepper key, TLS material, TOTP secrets, webhook secrets, recovery-code hashes)
- import preview before committing restore
- atomic import with rollback on error
- admin-only UI under Settings → System
- audit log entries (export, import attempt, import success, rejection, schema mismatch, signature mismatch)
- bundle format: signed JSON or signed tarball with manifest

## Non-Goals

Not in this slice:
- backing up recordings or motion-clip media (storage rotation handles those)
- Yocto image or firmware backup (covered by SWUpdate A/B partition story — ADR-0008)
- camera-side `/data` backup (cameras re-pair on import; their state is rebuilt from server-side record)
- cross-major-version restores (refuse with clear error; target same schema version)
- scheduled or automatic backups in v1 (manual export only; scheduling is a follow-up issue)
- encrypted-at-rest local archive of multiple historical backups
- multi-device or cloud-synchronized backup

## Acceptance Criteria

- An admin can export a signed bundle containing selected configuration subsets.
- The bundle includes a manifest with bundle version, schema hash, export timestamp, and signature.
- Import preview displays what configuration will be imported (full vs partial, count of users/cameras/rules).
- Import commits atomically: all changes succeed or none do; on error, the system state is unchanged.
- Signature verification rejects tampered bundles with a clear error.
- Schema version mismatch rejects old bundles with a clear error and guidance on upgrade path.
- Audit log records export, import attempt, import success, and all rejection reasons.
- Round-trip restore (export then re-import on the same system) reproduces the prior configuration.
- Partial-restore mode (e.g., import users but not cameras) works correctly and does not orphan related data.
- Per-secret-class policy is enforced: some secrets are included (with warning), others are re-issued on restore.
- UI clearly explains which secrets will be restored vs re-issued.

## User Experience

### Entry point

Admin navigates to Settings → System → Configuration Backup.

### Export flow

1. Admin clicks `Export configuration`.
2. Admin chooses scope: Full backup (all users, all cameras, all settings) or Partial (users only, cameras only, settings only).
3. Admin is presented with a clear breakdown:
   - User count, camera count, rule count, etc.
   - Warning: "Pepper key and TLS certificates will be included. Keep the bundle secure."
   - Option to exclude sensitive secrets: "Export without secrets — users and cameras will be imported, but secrets will be re-issued on restore."
4. Admin clicks `Download`. Browser receives a signed `.tar.gz` or `.json` file with metadata.
5. Export is recorded in audit log.

### Import flow

1. Admin navigates to Settings → System → Configuration Restore.
2. Admin uploads a bundle file.
3. System validates: signature check, schema version check.
4. If validation fails: clear error message (e.g., "Bundle was modified" or "Bundle is from an older version; please upgrade the target system first").
5. If validation succeeds: **preview** the import:
   - List of users to be created/updated
   - List of cameras to be created/updated
   - Policies and settings to be restored
   - Warning about any conflicting or stale data (e.g., "2 users will be overwritten"; "1 camera pair-token has changed since backup")
6. Admin clicks `Confirm restore` to commit atomically.
7. If import fails (e.g., database corruption, I/O error): rollback to pre-import state; show error and invite support contact.
8. On success: success message, audit log entry, and automatic redirect to dashboard.
9. Admin is advised to verify system state (e.g., re-check camera connectivity, user access).

### Secret handling display

UI shows at export and import time:

- **Included with warning** (pepper key, TLS certificates, TOTP secrets):
  - "These are security-sensitive. Keep the backup file secure. On restore, the system will use the backed-up values."
  - Option to exclude.

- **Re-issued on restore** (webhook secrets, recovery-code hashes):
  - "For security, these will be regenerated on restore. Users may need to re-enroll 2FA or re-register webhook endpoints."

## Architecture Fit

This feature fits cleanly within the existing architecture per `docs/ai/design-standards.md`:

- **Service-layer**: business logic in a new `ConfigBackupService` in `app/server/monitor/services/config_backup_service.py`.
- **Runtime mutable state on `/data`**: bundles are not stored on the system; they are downloaded or uploaded by the user.
- **Admin-only routes**: new endpoints in `app/server/monitor/api/system.py` (or dedicated `config.py`), protected by admin role check.
- **Audit trail**: integration with existing `audit.py` service.
- **No camera changes**: cameras are not aware of this feature; they re-pair post-restore from the server-side record.
- **No Yocto changes**: this is runtime configuration, not image policy.

## Technical Approach

### Bundle Structure

A bundle is a signed container:

```
bundle.tar.gz (or bundle.json, to be decided)
├── manifest.json  (includes schema version, timestamp, signature)
├── users.json
├── cameras.json
├── settings.json
├── retention_policy.json
├── alert_rules.json
└── ota_config.json (optional)
```

Schema version in manifest allows rejection of incompatible bundles.

### Signing and Verification

- Server has a long-lived signing keypair (generated during first boot or system initialization, stored on `/data`).
- Manifest is signed with Ed25519 or RSA (TBD in implementation).
- On import, system verifies the signature. Tampered bundles are rejected.
- No cloud verification (stays local-first).

### Secret Handling Policy

Per-secret-class decisions (explicit in code and audit log):

| Secret Class | Action | Rationale |
|---|---|---|
| Pepper key | Include + warn | Core auth credential; without it, password hashes are invalid. Admin must protect bundle. |
| TLS certificates + keys | Include + warn | System identity; re-issuing complicates camera trust re-establishment. |
| TOTP secrets | Include + warn | User 2FA enrollment; re-issuing requires user re-enrollment. |
| Webhook secrets | Re-issue | Shared secrets; on restore, assume old channel may be compromised. Webhook registrants must re-enroll. |
| Recovery code hashes | Re-issue | 2FA backup codes; old codes are invalidated; users receive new codes. |

Rationale per security section below.

### Import Atomicity

- Begin transaction.
- Validate all data before writing.
- Perform upsert on users, cameras, settings, rules.
- Commit or rollback on any error.
- Log outcome.

### Audit Trail

New entries in `audit.py`:

- `action=config_export, scope=full|partial, timestamp, initiator=admin_user`
- `action=config_import_attempt, bundle_schema_version, timestamp, initiator=admin_user`
- `action=config_import_success, users_count, cameras_count, timestamp, initiator=admin_user`
- `action=config_import_rejected, reason=signature_mismatch|schema_version|corruption, timestamp, initiator=admin_user`

## Affected Areas

- `app/server/monitor/services/config_backup_service.py` — new
- `app/server/monitor/api/system.py` (or dedicated `config.py`) — new routes
- `app/server/monitor/templates/` — admin UI components
- `app/server/monitor/services/audit.py` — integration for audit log
- Database: no schema changes for users/cameras/settings (upsert semantics), but new schema row for bundle version and signing key(s) may be needed.
- `tests/` — unit tests for bundle creation, signing, verification, import, rollback; contract tests for API; smoke test for round-trip.

## Validation Plan

Per `docs/ai/validation-and-release.md`:

| Area | Required Validation | Evidence |
|---|---|---|
| Service logic | Unit tests (bundle creation, signing, verification, import, rollback) + contract tests (API shape) | `pytest app/server/tests/test_config_backup_service.py -v --cov-fail-under=85` |
| API behavior | Contract tests (export, import, preview endpoints) | HTTP contract test suite |
| Admin UI | Manual UI verification (export modal, import flow, preview, confirm) | Browser walkthrough + smoke test |
| Round-trip | Integration test (export, then import on same system, verify state matches) | `pytest tests/test_config_backup_integration.py -v` |
| Partial restore | Integration test (export full, import partial, verify no orphaned data) | `pytest tests/test_config_backup_partial.py -v` |
| Rejection cases | Unit + integration tests (tampered bundle, schema mismatch, corrupted JSON) | Dedicated test cases |
| Audit trail | Integration test (log entries created for export, import, rejection) | Audit log inspection in tests |
| Smoke test | Hardware verification (export, wipe, restore, verify cameras re-connect, users re-authenticate) | `scripts/smoke-test.sh` extension |
| Security | Full suite + code review on secret handling, signing, verification, key storage | Pre-merge security review |

## Risk

### ISO 14971-Lite Framing

| Hazard ID | Hazard | Severity | Probability | Proposed Risk Control | Residual Risk |
|---|---|---|---|---|---|
| HAZ-001 | Restored bundle contains stale TLS certificates; cameras no longer trust the server. | High | Medium | Certificate re-validation on camera reconnect; audit log documents timestamp. Runbook advises re-pairing cameras if cert changed. | Medium → Low (with runbook) |
| HAZ-002 | Tampered bundle (signed by attacker) is imported, overwriting valid users/settings. | High | Low | Signature verification (Ed25519 or RSA). Bundle must be signed with the system's private key; external bundles are rejected. | Low (crypto) |
| HAZ-003 | Schema version mismatch: old bundle from v1.0 imported into v2.0, causing silent data loss or corruption. | High | Medium | Schema version in manifest; strict version check; reject on mismatch with clear error. | Low (explicit check) |
| HAZ-004 | Bundle export includes secrets; bundle is leaked to an attacker (e.g., left on a USB drive, emailed unencrypted). | High | Medium | UI warning at export time. Admin policy controls per-secret inclusion. Consider recommending password-protected archive (out of scope for v1). | Medium (admin responsibility) |
| HAZ-005 | Import fails midway (e.g., disk full, database corruption), leaving system in inconsistent state. | Medium | Low | Atomic import (transaction-based rollback). Verify on commit or abort before state change. | Low (atomic) |
| HAZ-006 | Webhook secrets and recovery codes are re-issued on restore; integrations and users lose 2FA backup codes without warning. | Medium | Medium | UI clearly explains which secrets are re-issued. Audit log documents it. Runbook explains recovery. | Low (with documentation) |
| HAZ-007 | Restore deletes a recently-created user that wasn't in the backup (user created after backup). | Medium | High | Preview step shows which users will be overwritten. Document (and enforce if time permits) a "merge" mode that updates but does not delete. For v1, "delete absent users" is explicit in UI. | Medium (mitigated by UI clarity) |

**Risk Controls to Implement:**

- Signature verification (cryptographic).
- Schema version check (strict rejection on mismatch).
- Atomic import with rollback.
- Clear UI messaging at export (secret handling, bundle security) and import (preview, consequences).
- Comprehensive audit log (all actions and rejection reasons).
- Runbook for recovery after bad restore (rollback procedure, re-pairing cameras).

### Outstanding Risks

- **Cross-version import**: v1 bundles cannot be imported into v2 or later. Upgrade path is "export from v1, upgrade, cannot re-import v1 bundle." This is acceptable for v1 (non-goal) but should be documented and tested before v2.
- **Operator misuse**: Admin exports bundle with secrets, keeps it insecure. Risk Control: UI warning. Residual: admin responsibility.

## Security

### Threat Model

| Threat ID | Threat | Attacker | Impact | Control | Residual |
|---|---|---|---|---|---|
| THREAT-001 | Attacker intercepts bundle (e.g., over HTTP, unencrypted email) and modifies it. | Active MITM | Stale/overwritten users, settings, TLS state. | Signature verification. Bundle is signed with system private key; attacker cannot forge signature without the key. HTTPS should be enforced (out of scope for backup design, but implicit in API design). | Low (crypto + HTTPS) |
| THREAT-002 | Attacker gains access to server `/data` and steals the signing private key. | Insider / compromised device | Attacker can forge valid bundles and import malicious config. | Private key is stored on `/data`, which is encrypted or trusted per the system's threat model. Host-level access controls are assumed (e.g., SSH keys, /root permissions). | Medium (scope of system threat model) |
| THREAT-003 | Attacker replays an old, valid bundle to downgrade auth state (e.g., re-create a deleted admin user). | Attacker with bundle from backup + access to import UI | Privilege escalation, unauthorized access. | Timestamp in bundle allows human review ("bundle is from 2024-01-15; are you sure?"). Audit log documents import and source bundle timestamp. Cannot cryptographically prevent replay without additional state (e.g., sequence numbers), which is deferred to v2. | Medium (mitigated by audit + review) |
| THREAT-004 | Admin exports bundle with secrets enabled, accidentally commits it to GitHub. | Negligent admin | Secret exposure (pepper key, TLS keys, TOTP secrets). | UI warning at export. Recommend `.gitignore` for bundles. Cannot enforce encryption within the scope of v1, but recommend in runbook. | Medium (admin responsibility + warning) |
| THREAT-005 | Attacker with partial access to the system (e.g., unprivileged user account) tries to export a bundle. | Unprivileged user | Configuration leakage. | Export is admin-only (role-based access control). API enforces `@require_admin`. | Low (RBAC) |

### Sensitive Paths

This feature touches:

- **`app/server/monitor/api/system.py`** (new routes): export, import, preview — must be admin-only.
- **`app/server/monitor/services/config_backup_service.py`** (new): signing, verification, bundle creation — must handle secrets safely (no plaintext logs, no debug dumps).
- **`app/server/monitor/services/audit.py`**: integration — must log all export/import events.
- **Secrets on `/data`**: pepper key, TLS certificates, TOTP secrets — existing infrastructure, but now explicitly included in bundles. Policy is explicit in code and tests.

### Secret Handling Code Comments

Code must include inline comments at the point where each secret class is decided:

```python
# REQ: SEC-0XX — pepper key is included in backup (user passwords depend on it)
# THREAT: bundle exposure leaks auth material; admin must protect bundle file
```

## Traceability

### Requirements to be filled in during implementation

| Type | ID | Title | Status |
|---|---|---|---|
| User Need | UN-XXX | Admin can recover system after hardware failure without re-onboarding | Open |
| System Requirement | SYS-XXX | System shall provide a mechanism to export and import signed configuration bundles | Open |
| Software Requirement | SWR-XXX | Config backup service shall validate bundle signature before import | Open |
| Software Requirement | SWR-XXX | Config backup service shall verify schema version and reject mismatches | Open |
| Software Requirement | SWR-XXX | Export and import shall be recorded in audit log | Open |
| Security Requirement | SEC-XXX | Backup bundles shall be signed to prevent tampering | Open |
| Security Requirement | SEC-XXX | Pepper key and TLS material shall be included in backup with explicit admin warning | Open |
| Architecture | ARCH-XXX | Config backup uses service-layer pattern (ConfigBackupService) | Open |
| Architecture | SWA-XXX | Bundle signing uses Ed25519 or RSA (TBD) | Open |
| Risk | RISK-XXX | Stale TLS certificates after restore may break camera trust | Open |
| Risk | RISK-XXX | Schema mismatch may cause data loss | Open |
| Risk Control | RC-XXX | Schema version check rejects incompatible bundles | Open |
| Risk Control | RC-XXX | Atomic import with rollback prevents partial/inconsistent restores | Open |
| Test Case | TC-XXX | Round-trip export and import succeeds on same system | Open |
| Test Case | TC-XXX | Tampered bundle is rejected with signature mismatch error | Open |
| Test Case | TC-XXX | Schema version mismatch is rejected | Open |

### Code Annotation Pattern

Every traceable file must include a `REQ:` annotation, e.g.:

```python
# REQ: SWR-0XX, SEC-0XX — bundle validation and signature check
def verify_bundle(bundle_data, public_key):
    ...
```

## Deployment Impact

### OTA and Update Path

- **No Yocto changes**: this is a server-side runtime feature.
- **No schema migration required** for existing tables (users, cameras, settings already exist; upsert is compatible).
- **New table or `/data` entry**: signing keypair must be generated on first system initialization or during first access to the export feature. Existing systems will generate the key on first import/export attempt.
- **No firmware or bootloader changes**.

### Rollout and Operator Guidance

- Feature is opt-in from the admin UI.
- Operator should be advised to:
  1. Export a backup soon after setup.
  2. Keep bundles secure (password-protected archive, air-gapped storage).
  3. Test restore on a development system before relying on it in production.
  4. Understand that webhook secrets and 2FA recovery codes are re-issued on restore.

### Runbook Additions

- "How to export configuration"
- "How to restore configuration"
- "What happens to secrets and API keys after restore"
- "Troubleshooting: bundle rejected (signature mismatch, schema version)"
- "Recovery: if restore went wrong, how to rollback"

## Open Questions

1. **Bundle format**: Should we use `.tar.gz` with manifest, or just a single signed JSON file? Trade-off: tar is extensible (future file types), JSON is simpler. Decision needed before implementation.

2. **Signing algorithm**: Ed25519 (recommended for simplicity and speed) or RSA (more common)? If Ed25519, we need a lightweight Rust or Python crypto library. If RSA, we use `cryptography` (already a dependency).

3. **Key generation and storage**: Should the signing keypair be generated:
   - On first system boot (Yocto recipe)?
   - On first access to the backup feature (lazy init)?
   - Both (strict generation, lazy as fallback)?

4. **Secret policy for "re-issue on restore"**: Do webhook secrets and recovery-code hashes get entirely new values, or do we generate them client-side with a seed? Current proposal: re-issue (new values). Confirm.

5. **UI "merge" mode for v2**: For now, import is "replace all" (delete users not in bundle). Should we support "merge" (update what's in the bundle, keep what's not)? Deferred to v2, but should be architected now to avoid redesign.

6. **Encrypted bundles for v2**: Should we support bundling with a password (AES-256)? Out of scope for v1, but should the bundle format be extensible?

---

## Implementation Readiness

This spec is ready for implementation. All acceptance criteria are testable, the threat model is documented, and the architecture fits the existing codebase. The open questions are non-blocking (they are design decisions, not blockers).

**Architect recommendation**: Move to `ready-for-implementation` and assign to Implementation role.
