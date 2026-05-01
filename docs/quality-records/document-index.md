# Document Index

Status: Draft prepared to support expert regulatory review.

## Quality Record Structure

| New record | Purpose | Existing material reused |
|---|---|---|
| `docs/intended-use/intended-use.md` | Intended use, exclusions, users, environment. | `README.md`, `docs/requirements.md`, `docs/connectivity-and-privacy-constraints.md` |
| `docs/intended-use/user-needs.md` | User need IDs. | `docs/requirements.md` section 3 |
| `docs/requirements/system-requirements.md` | System requirement IDs linked to user needs. | `docs/requirements.md`, `docs/specs/`, `docs/releases/` |
| `docs/requirements/software-requirements.md` | Software requirement IDs linked to system requirements. | `docs/architecture.md`, ADRs, tests |
| `docs/requirements/hardware-requirements.md` | Hardware requirement IDs linked to system requirements. | `docs/hardware-setup.md`, Yocto machine/image configs |
| `docs/architecture/system-architecture.md` | System architecture items. | `docs/architecture.md`, ADR index |
| `docs/architecture/software-architecture.md` | Software architecture items. | `docs/architecture.md`, `docs/adr/` |
| `docs/architecture/hardware-architecture.md` | Hardware architecture items. | `docs/hardware-setup.md`, `meta-home-monitor/` |
| `docs/risk/*` | Risk plan, hazard analysis, DFMEA, control verification. | ADR risk notes, release docs, smoke-test docs |
| `docs/cybersecurity/*` | Security plan, threat model, security risk, SBOM and vulnerability management. | ADRs 0009, 0011, 0014, 0022; `docs/ota-key-management.md` |
| `docs/verification-validation/*` | Test plan, test cases, report template. | `docs/testing-guide.md`, CI workflows, test suites |
| `docs/traceability/*` | End-to-end traceability matrix. | New machine-checkable matrix |
| `docs/quality-records/regulatory-review-gap-assessment.md` | Regulatory-review-style gap assessment and human review queue. | New review record linked to expanded draft artifacts |

## Existing Docs Retained

No useful existing document was deleted or archived in this change. Existing
docs remain as supporting design history and implementation detail. This index
maps them into the new quality-record structure.

| Existing doc | Keep as | Quality-record relation |
|---|---|---|
| `docs/requirements.md` | Historical product requirements baseline. | Source for `UN-*`, `SYS-*`, `SWR-*`, `HWR-*` |
| `docs/architecture.md` | Main system/software architecture narrative. | Source for `ARCH-*`, `SWA-*`, `HWA-*` |
| `docs/adr/` | Decision records. | Supporting rationale for architecture, risk, and security controls |
| `docs/specs/` | Feature specs. | Source for acceptance criteria and future requirement updates |
| `docs/testing-guide.md` | Test execution guide. | Source for `TC-*` automation mapping |
| `docs/hardware-setup.md` | Operator hardware setup. | Source for `HWR-*` and manual validation |
| `docs/release-runbook.md` | Release/update runbook. | Source for OTA verification controls |
| `docs/ota-key-management.md` | OTA key handling. | Source for `SC-003`, `SC-005`, vulnerability management |
| `docs/adr/0026-desired-vs-observed-state-reconciliation.md` | Desired-vs-observed camera state design. | Source for `SYS-017`, `SWR-025`, `SWR-026`, `RISK-015` |
| `docs/adr/0027-rich-motion-notifications.md` | Rich motion notification design. | Source for `SYS-018`, `SWR-033`, `SWR-040`, `SWR-041`, `RISK-016` |

## Archive Decision

No documents were archived because the existing docs are still useful. If a
future record becomes stale, archive it under `docs/archive/` with a pointer
from this index instead of deleting it.
