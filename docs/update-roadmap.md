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
| OTA API surface | Partial | Server API endpoints exist, but not every documented flow is proven end-to-end on hardware |
| App-only hot deploy for development | Working | We use direct app sync in the lab today; this is practical but not the final signed OTA path |
| Full-system SWUpdate flow | Partial | Design is strong; signed bundle creation is validated in the clean VM, but end-to-end production-grade hardware validation is still incomplete |
| A/B rollback with U-Boot | Partial | Planned and partially wired in Yocto/docs; not yet fully validated across real upgrade/rollback cycles |
| USB update flow | Partial | Inbox/import model is designed; must be validated end-to-end on hardware |
| Camera OTA push via server | Partial | Agent/service pieces exist; needs stronger end-to-end validation and release hardening |
| Production signing flow | Partial / not field-validated | Signing design exists, but production-signing workflow is not yet fully tested on real hardware |

### 3.2 Delivery Mode Status

| Delivery mode | Intended use | Status |
|---|---|---|
| Dashboard upload | Server/admin driven updates | Partial |
| USB import | Offline/field updates | Partial |
| Server push to camera | Production camera updates | Partial |
| SCP to inbox | Dev/lab only | Working for development workflow |
| Repository polling (Suricatta) | Future managed updates | Planned, not implemented |

### 3.3 Artifact Status

| Artifact type | Status | Notes |
|---|---|---|
| `.swu` full-system bundle | Partial | Intended final production path |
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

- production OTA bundles must be signed
- production devices should verify signatures before install
- production updates should go through the supported OTA pipeline, not manual file sync
- production rollback behavior must be validated on real hardware before being called release-ready

### 4.3 Important Current Limitation

Production OTA signing and the full production update pipeline are **not yet fully tested on real hardware**.
The clean VM proves signed `.swu` generation works, but install/reboot/rollback still need device validation.

That means:
- the design exists
- some implementation exists
- the repo should not claim the production OTA/signing path is fully validated today

---

## 5. Current Working Rules

Until the production OTA path is fully validated:

1. Use dev builds for software iteration and hardware debugging
2. Use direct app hot-deploy only for lab/dev devices
3. Do not describe unsigned dev OTA as production-ready
4. Do not describe production signing as field-proven yet
5. Treat USB/import/server-push flows as release work that still requires dedicated validation

---

## 6. Execution Plan

### Phase 1: Truth and Documentation

- Align README, requirements, architecture, and development guide with actual status
- Mark production OTA/signing as not yet fully hardware-validated
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

- [ADR-0008](./adr/0008-swupdate-ab-rollback.md)
- [ADR-0014](./adr/0014-swupdate-signing-dev-prod.md)
- [Architecture](./architecture.md)
- [Requirements](./requirements.md)
- [Development Guide](./development-guide.md)
