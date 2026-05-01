# Repository Assessment

Status: Draft prepared to support expert regulatory review.
Date: 2026-05-01

## Current Structure Summary

This repository is a self-hosted Raspberry Pi home monitoring system with two
runtime applications, Yocto image definitions, release tooling, and extensive
tests.

| Area | Summary | Existing evidence |
|---|---|---|
| Server source | Flask app, API blueprints, templates, services, models, JSON store, audit, OTA, recording, alerts. | `app/server/monitor/`, `app/server/tests/`, `docs/architecture.md` |
| Camera source | Python camera runtime, pairing, capture, stream, motion, OTA agent, status UI, WiFi setup, hardware overlay scripts. | `app/camera/camera_streamer/`, `app/camera/config/`, `app/camera/tests/` |
| Shared/release code | Shared release version reader and version consistency checks. | `app/shared/`, `VERSION`, `scripts/check_versioning_design.py` |
| Yocto and hardware | Custom distro, server/camera image recipes, machine configs, SWUpdate assets. | `meta-home-monitor/`, `config/`, `swupdate/`, `docs/build-setup.md` |
| Documentation | Requirements, architecture, ADRs, release docs, specs, hardware setup, testing guide, AI operating rules. | `docs/requirements.md`, `docs/architecture.md`, `docs/adr/`, `docs/specs/`, `docs/ai/` |
| CI/CD | PR CI, nightly, release validation, OTA signing checks, shell/workflow lint, coverage gates. | `.github/workflows/` |
| Security docs | Auth hardening, mTLS pairing, no-backdoor policy, OTA signing, key management, connectivity/privacy constraints. | `docs/adr/0009-camera-pairing-mtls.md`, `docs/adr/0011-auth-hardening.md`, `docs/adr/0022-no-backdoors.md`, `docs/ota-key-management.md` |
| Risk/safety docs | ADRs contain risk reasoning, but no unified hazard analysis, DFMEA, or risk-control verification matrix existed before this change. | `docs/adr/`, `docs/exec-plans/` |

## Useful Existing Artifacts

- Existing requirements baseline: `docs/requirements.md`.
- Existing architecture baseline: `docs/architecture.md` and `docs/adr/`.
- Existing validation baseline: `docs/testing-guide.md`, PR CI, coverage gates, contract tests, security tests.
- Existing security posture: no-backdoor rule, mTLS pairing, signed OTA, CSRF/session hardening, firewall configs, LUKS design.
- Existing release and update evidence: `RELEASE.md`, `docs/release-runbook.md`, `docs/ota-key-management.md`.
- Existing hardware and operator setup evidence: `docs/hardware-setup.md`, `docs/build-setup.md`.

## Gaps Found

- Requirements exist, but they were not decomposed into user, system,
  software, and hardware requirement IDs.
- Requirements were not linked end-to-end to architecture, safety risk,
  cybersecurity controls, code references, and tests.
- Safety risk analysis, DFMEA, and risk-control verification records were not
  present as maintained quality records.
- Cybersecurity material existed across ADRs and runbooks, but no consolidated
  threat model, security risk analysis, SBOM plan, or vulnerability management
  plan existed in the target structure.
- Code-level traceability annotations were not required by agent instructions
  and were not machine checked.
- CI did not include a dedicated traceability checker.
- Initial quality-record draft was too coarse for the current implementation
  surface. Expanded follow-up coverage added setup/provisioning, user
  management, settings/time/WiFi, USB/removable storage, media path safety,
  desired-vs-observed camera state, rich motion notification design, WebRTC
  proxying, release/SBOM controls, production/development separation, fault
  evidence, API contracts, and hardware/environment envelopes.

## Recommended Reorganization

- Keep the existing narrative docs and ADRs as historical and design evidence.
- Add the target quality-record structure under `docs/` without deleting or
  moving existing docs.
- Use `docs/quality-records/document-index.md` as the index between existing
  docs and the new quality-record structure.
- Use `docs/traceability/traceability-matrix.csv` as the machine-checkable
  source for trace links.
- Use `tools/traceability/check_traceability.py` and CI to prevent obvious
  orphan IDs, missing tests, and stale code annotations.
- Use `docs/quality-records/regulatory-review-gap-assessment.md` as the
  active queue of human review items before any draft artifact is treated as
  controlled quality-system evidence.

## Assumptions

- ASSUMPTION: The current product intent remains local home monitoring and is
  not a diagnostic, therapeutic, life-supporting, or emergency medical product.
- ASSUMPTION: The quality-record structure is being added to support expert
  review and disciplined engineering, not to assert regulatory compliance.
- ASSUMPTION: Existing tests provide implementation evidence for many draft
  test cases, but formal validation protocols require human approval.

## Open Questions

- OPEN QUESTION: Should the product ever be marketed or used for medical
  monitoring, elder care, fall detection, clinical surveillance, or emergency
  response?
- OPEN QUESTION: Which human role owns approval of user needs, hazards, risk
  acceptability, cybersecurity residual risk, and validation reports?
- OPEN QUESTION: What external standards, if any, should be formally mapped by
  a qualified regulatory expert?
- REGULATORY REVIEW REQUIRED: Confirm product classification, intended use,
  risk acceptability criteria, quality-system procedures, and release evidence
  requirements before treating these records as controlled quality evidence.
