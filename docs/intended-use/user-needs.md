# User Needs

Status: Draft prepared to support expert regulatory review.

| ID | Statement | Rationale | Source | Verification method | Linked tests | Status |
|---|---|---|---|---|---|---|
| UN-001 | The operator needs to view live camera video locally. | Home monitoring depends on timely visibility. | `docs/requirements.md` UN-01 | System test, browser smoke | TC-001, TC-006 | Draft |
| UN-002 | The operator needs to review recorded clips by camera and time. | Recording evidence supports after-the-fact review. | `docs/requirements.md` UN-02 | Integration and UI tests | TC-002, TC-007 | Draft |
| UN-003 | The operator needs automatic storage management. | Manual deletion is unreliable for unattended recording. | `docs/requirements.md` UN-03 | Unit and integration tests | TC-003 | Draft |
| UN-004 | The operator needs protected access to live and recorded video. | Home video is sensitive personal data. | `docs/requirements.md` UN-04 | Security tests | TC-004, TC-011 | Draft |
| UN-005 | The operator needs health and fault visibility for server and cameras. | Silent failures can create false confidence. | `docs/requirements.md` UN-05 | Unit and integration tests | TC-005, TC-014 | Draft |
| UN-006 | The operator needs easy setup, pairing, and discovery. | Self-hosted hardware must be installable by non-specialists. | `docs/requirements.md` UN-06, UN-07 | Integration and manual setup validation | TC-008, TC-012 | Draft |
| UN-007 | The operator needs updates that preserve device integrity. | Security and reliability fixes must reach devices safely. | `docs/requirements.md` UN-08 | OTA tests and release validation | TC-009, TC-013 | Draft |
| UN-008 | The operator needs local-first privacy with optional remote access. | The product differentiator is no mandatory cloud dependency. | `docs/connectivity-and-privacy-constraints.md` | Architecture review, security tests | TC-010, TC-016 | Draft |
| UN-009 | The operator needs actionable local alerts for camera, storage, and system issues. | Alerts reduce time to detect failures. | ADR-0024 | Unit and integration tests | TC-014 | Draft |
| UN-010 | Maintainers need traceable engineering records for meaningful changes. | Traceability supports safer review and release decisions. | User request 2026-05-01 | Traceability checker, PR review | TC-020 | Draft |
