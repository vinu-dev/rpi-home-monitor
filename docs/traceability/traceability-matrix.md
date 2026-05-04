# Traceability Matrix

Status: Draft prepared to support expert regulatory review.

The machine-checkable matrix is `docs/traceability/traceability-matrix.csv`.
This Markdown file summarizes the current coverage.

## Coverage Summary

| Area | Coverage status |
|---|---|
| User needs | UN-001 through UN-023 are linked to system requirements and tests. |
| System requirements | SYS-001 through SYS-032 are linked to user needs, lower-level requirements, architecture, risks, controls, and tests. |
| Software requirements | SWR-001 through SWR-064 each have at least one linked test case. |
| Hardware requirements | HWR-001 through HWR-018 are linked to architecture and hardware/manual or automated verification. |
| Architecture | ARCH-001 through ARCH-016, SWA-001 through SWA-026, and HWA-001 through HWA-012 are linked into the matrix. |
| Safety risk | RISK-001 through RISK-025 each have at least one risk control and verification. |
| Cybersecurity | THREAT-001 through THREAT-022 and SC-001 through SC-024 are linked to requirements and tests. |
| Code references | The checker requires every traceable code, test, workflow, build, script, configuration, and hardware-interface file to carry at least one `REQ:` annotation. Current validation in this update covers 344 of 344 traceable files, excluding vendored minified browser libraries. |
| Tests | TC-001 through TC-053 have linked requirements. |

## Matrix Source

The CSV columns are:

1. User Need
2. System Requirement
3. Software Requirement
4. Hardware Requirement
5. Architecture
6. Risk
7. Risk Control
8. Security Asset
9. Security Threat
10. Security Control
11. DFMEA
12. Code Reference
13. Test Case
14. Test Result/Status

Run:

```bash
python tools/traceability/check_traceability.py
```

## Remaining Gaps

- OPEN QUESTION: Test-result status is currently draft-level, not a signed
  formal validation result.
- OPEN QUESTION: Manual hardware smoke evidence is referenced but not attached
  as a release-specific controlled record.
- OPEN QUESTION: Several rich motion notification controls are currently
  design-intent draft records and need implementation acceptance before
  release reliance.
- OPEN QUESTION: Capacity/performance limits remain draft until hardware
  profile testing defines camera count, bitrate, retention, and thermal
  envelopes.
- REGULATORY REVIEW REQUIRED: Human approval is required before this matrix is
  used as quality-system evidence.
