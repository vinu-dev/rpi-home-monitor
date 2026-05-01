# Regulatory-Review-Style Gap Assessment

Status: Draft prepared to support expert regulatory review.
Date: 2026-05-01

This record is a regulatory-review-style engineering review of the draft
quality artifacts. It is not a certification, clearance, approval, or formal
regulatory opinion.

## Review Scope

Reviewed areas:

- Intended use and user needs: UN-001 through UN-022
- System requirements: SYS-001 through SYS-030
- Software requirements: SWR-001 through SWR-055
- Hardware requirements: HWR-001 through HWR-018
- Architecture: ARCH-001 through ARCH-015, SWA-001 through SWA-025, HWA-001 through HWA-012
- Safety risk: HAZ-001 through HAZ-022, RISK-001 through RISK-022, RC-001 through RC-022
- DFMEA: DFMEA-001 through DFMEA-021
- Cybersecurity: SEC-001 through SEC-016, THREAT-001 through THREAT-020, SC-001 through SC-021
- Verification: TC-001 through TC-047
- Traceability automation and code annotation baseline

## Review Findings

| Finding | Severity | Evidence | Required action | Status |
|---|---|---|---|---|
| The previous baseline was structurally valid but under-decomposed for the repository size. | Major | Original draft had 12 SYS and 20 SWR items for server, camera, Yocto, OTA, storage, security, UI, and CI surfaces. | Expand requirements and trace links across uncovered modules. | Addressed in draft expansion |
| Intended use still excludes medical diagnosis, therapy, life support, emergency response, and regulated patient monitoring. | Critical if marketed otherwise | `docs/intended-use/intended-use.md` and repository purpose. | Human owner must confirm market claims and deployment intent before relying on these artifacts. | REGULATORY REVIEW REQUIRED |
| Manual and hardware evidence is referenced but not attached as controlled execution records. | Major | TC-018, TC-024, TC-032, TC-043, TC-047 reference manual/hardware/release review. | Create release-specific executed reports with environment, operator, result, deviations, and approval. | OPEN QUESTION |
| Risk acceptability criteria are not formally defined. | Major | Risk files use draft severity/probability/residual levels. | Define risk matrix, acceptability thresholds, benefit-risk rationale, and approval role. | REGULATORY REVIEW REQUIRED |
| Rich motion notifications are represented as design controls but may not be fully implemented. | Major | ADR-0027 and SWR-041/TC-031 mark notification-media controls. | Confirm implementation status and disable or constrain unreleased behavior until verified. | OPEN QUESTION |
| Production versus development image controls need explicit executed evidence. | Major | SYS-024, SWR-049, SWR-050, TC-044. | Add production image checklist and artifact review before release. | OPEN QUESTION |
| Vulnerability response ownership and timelines are not approved. | Major | Cybersecurity open questions and SEC/SC records. | Assign owner, intake process, triage SLA, patch timelines, and disclosure process. | OPEN QUESTION |
| Capacity and environmental limits are not yet proven by hardware profile. | Major | HWR-017, RISK-022, TC-047. | Execute hardware profile tests for camera count, bitrate, retention, thermal, power, and WiFi limits. | OPEN QUESTION |
| Code annotations are selective and should be reviewed for density and placement. | Minor | Code annotation baseline exists but is not exhaustive. | Human reviewers should confirm annotations on safety/security/data/IO/update/config surfaces. | OPEN QUESTION |

## Completeness Opinion

The current draft is substantially more complete than the initial baseline for
engineering review. It covers core product operation, setup, user management,
storage, media handling, desired/observed camera state, rich motion
notification design, live transport proxying, OTA, release evidence,
cybersecurity, hardware assumptions, CI, and traceability.

It is not complete as approved quality-system evidence. Required next steps are
human approval of intended use, risk acceptability, cybersecurity residual
risk, verification protocols, hardware evidence, release evidence, and
production image controls.

## Required Human Review Areas

- REGULATORY REVIEW REQUIRED: Intended use, exclusions, product claims, and
  whether any deployment moves toward patient monitoring, elder-care
  monitoring, diagnosis, treatment, emergency response, or life-safety use.
- REGULATORY REVIEW REQUIRED: Risk acceptability matrix, residual risk
  acceptance, benefit-risk rationale, and risk-control verification evidence.
- REGULATORY REVIEW REQUIRED: Cybersecurity residual risk, vulnerability
  response plan, key-management procedure, and release-signing separation.
- REGULATORY REVIEW REQUIRED: Verification and validation protocols, manual
  hardware test evidence, deviations, and approval signatures.

Prepared to support expert regulatory review.
