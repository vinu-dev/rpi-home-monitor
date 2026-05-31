# Update Roadmap and Current Status

Version: 1.0
Date: 2026-04-14

---

## 1. Purpose

This document is the source of truth for software update design and delivery status.

It separates:
- what is implemented on current dev hardware
- what is partially implemented or only lab-validated
- what is planned but not yet finished

If this file conflicts with older wording in the repo, this file wins.

---

## 2. Product Goal

The update system is designed to support all of the following through one consistent pipeline:

1. Full-system OS updates for server and camera
2. App-only updates without reflashing the whole OS
3. Automatic rollback on failed boot or failed health check
4. Multiple delivery modes:
   - dashboard upload
   - USB import
   - server-pushed camera updates
   - developer SCP/inbox flow
   - future repository polling
5. U-Boot-managed A/B slot switching for rootfs updates

The intended trust model is:
- source is never trust
- verification is shared
- signing is the trust anchor for production

---

## 3. Current Status Summary

### 3.1 Overall

| Area | Status | Notes |
|---|---|---|
| OTA API surface | Implemented | Server self-update, server-pushed camera update, and camera-direct upload are wired through the GUI/API and covered by tests |
| App-only hot deploy for development | Working | We use direct app sync in the lab today; this is practical but not the final signed OTA path |
| Full-system SWUpdate flow | Implemented / lab-validated | Dev hardware has exercised server and camera install/reboot success paths; production release validation still owns signed-artifact and rollback evidence |
| A/B rollback with U-Boot | Implemented / validation continuing | Bootlimit and health-confirmation wiring exist; release validation must continue to capture forced-failure rollback evidence |
| USB update flow | Implemented | USB import/storage flows use the same OTA staging/library model and root-helper storage boundary |
| Camera OTA push via server | Implemented / lab-validated | Server relays bundles over mTLS, camera owns activation reboot, and server confirms the reported target version after boot |
| Production signing flow | Implemented / release-gated | Signing design and artifact generation exist; each production release still needs signed install/reboot/rollback validation evidence |

### 3.2 Delivery Mode Status

| Delivery mode | Intended use | Status |
|---|---|---|
| Dashboard upload | Server/admin driven updates | Implemented |
| USB import | Offline/field updates | Implemented |
| Server push to camera | Production camera updates | Implemented |
| SCP to inbox | Dev/lab only | Working for development workflow |
| Repository polling (Suricatta) | Future managed updates | Planned, not implemented |

### 3.3 Artifact Status

| Artifact type | Status | Notes |
|---|---|---|
| `.swu` full-system bundle | Implemented | Intended production path; release validation records signed install/reboot/rollback evidence |
| `.tar.zst` app-only bundle | Partial | Design exists; repo still uses lab hot-deploy for day-to-day iteration |

---

## 4. Dev vs Production Policy

### 4.1 Dev Builds

Dev builds are intentionally optimized for iteration speed.

- `SWUPDATE_SIGNING = "0"` is the default for dev builds
- dev devices may accept unsigned OTA bundles
- developer SCP/inbox flows are allowed in the lab
- direct app hot-deploy is allowed in the lab

This is intentional. It avoids signing friction during normal development.

### 4.2 Production Builds

Production builds are intended to be stricter:

- self-hosted operators should generate and own their own OTA signing keypair
- production OTA bundles must be signed
- production devices should verify signatures before install
- production updates should go through the supported OTA pipeline, not manual file sync
- production rollback behavior must be validated on real hardware before being called release-ready

### 4.3 Important Current Limitation

The OTA implementation is no longer blocked on missing transport or activation
behavior. The remaining release gate is evidence quality: every production
release that claims OTA readiness must still run signed install/reboot checks
and capture rollback evidence for that artifact set.

That means:
- the implementation exists
- dev/lab success paths have been exercised on real devices
- production release notes should distinguish signed artifact generation from
  the specific signed install/reboot/rollback evidence captured for that release

---

## 5. Current Working Rules

Until a production release has current signed OTA evidence:

1. Use dev builds for software iteration and hardware debugging
2. Use direct app hot-deploy only for lab/dev devices
3. Do not describe unsigned dev OTA as production-ready
4. Do not describe production signing as field-proven yet
5. Treat USB/import/server-push flows as implemented paths that still require
   release-specific validation evidence

---

## 6. Execution Plan

### Phase 1: Truth and Documentation

- Align README, requirements, architecture, and development guide with actual status
- Mark production OTA/signing as release-gated by current hardware evidence
- Keep dev-signing bypass explicit and intentional

### Phase 2: Stabilize App-Only Update Path

- Replace ad-hoc manual hot deploy with a scripted app-only deploy flow
- Preserve ownership, permissions, service restart, and smoke validation
- Use that as the standard dev workflow

### Phase 3: Validate Full-System Updates

- Build signed prod bundles
- Validate server full-system update on real hardware
- Validate camera full-system update on real hardware
- Validate rollback after forced bad boot
- Validate post-update health confirmation

### Phase 4: Validate Delivery Modes

- USB import end-to-end
- dashboard upload end-to-end
- server-push camera update end-to-end
- downgrade/reject rules
- compatibility checks and history/audit behavior

### Phase 5: Production Readiness Gate

Production OTA should only be called fully implemented when all of the following are proven on hardware:

- signed server update succeeds
- signed camera update succeeds
- failed update rolls back automatically
- U-Boot slot state is correct before and after rollback
- health-check confirmation clears rollback flags
- USB path works
- dashboard upload path works
- camera push path works

---

## 7. References

- [ADR-0008](../adr/0008-swupdate-ab-rollback.md)
- [ADR-0014](../adr/0014-swupdate-signing-dev-prod.md)
- [Architecture](../baseline/architecture.md)
- [Requirements](../baseline/requirements.md)
- [Development Guide](../../guides/development-guide.md)
- [Release Operator Runbook](../../guides/release-runbook.md)
