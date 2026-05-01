# Verification and Validation Test Plan

Status: Draft prepared to support expert regulatory review.

## Purpose

This plan defines how requirements, risk controls, security controls, and code
annotations are verified in this repository. Existing automated tests remain
the main engineering evidence. Formal validation protocols require human
approval.

## Test Levels

| Level | Scope | Examples |
|---|---|---|
| Unit | Service, model, auth, storage, motion, config, and helper functions. | `app/server/tests/unit/`, `app/camera/tests/unit/` |
| Integration | API routes, app factory, views, pairing, OTA, lifecycle, setup flows. | `app/server/tests/integration/`, `app/camera/tests/integration/` |
| Security | Auth, pairing, encryption, password policy, no-backdoor behavior. | `app/server/tests/security/`, `app/camera/tests/security/` |
| Contract | API contracts and architecture fitness checks. | `app/server/tests/contracts/`, `app/camera/tests/contracts/` |
| Workflow/static | CI, shell scripts, doc links, AI adapters, traceability. | `.github/workflows/`, `scripts/ai/`, `tools/traceability/` |
| Hardware/manual | Device smoke, Yocto parse/build, update, pairing, WiFi, camera sensors. | `scripts/smoke-test.sh`, `docs/hardware-setup.md` |
| Release/security evidence | Version consistency, SBOM, signing, production profile, service hardening, vulnerability process. | release workflows, `scripts/check_versioning_design.py`, `scripts/generate-sbom.sh` |
| Regulatory-review draft records | Intended use, requirements, risk, cybersecurity, architecture, traceability, open questions. | `docs/quality-records/regulatory-review-gap-assessment.md` |

## Entry Criteria

- Requirement, risk, security, and test IDs exist for the change.
- Traceability matrix rows exist for affected IDs.
- Code annotations reference existing IDs where meaningful.
- Test environment and secrets are prepared without weakening security.

## Exit Criteria

- Relevant automated tests pass.
- `python tools/traceability/check_traceability.py` passes.
- CI is green or documented with accepted blockers.
- Manual/hardware tests are recorded or explicitly deferred with
  `OPEN QUESTION:` or `REGULATORY REVIEW REQUIRED:`.
- Production/development profile, release artifact, SBOM, key-management, and
  hardware-envelope evidence is either attached to the release record or
  explicitly marked as not executed.
- Human reviewer confirms that any draft-only planned features, such as rich
  motion notification media, are not relied on as implemented risk controls
  until verified.

## Open Questions

- OPEN QUESTION: Define independent validation responsibilities and approval
  signatures.
- OPEN QUESTION: Define release-specific test report retention and storage.
- OPEN QUESTION: Define who may approve manual hardware evidence and residual
  safety/cybersecurity risk.
