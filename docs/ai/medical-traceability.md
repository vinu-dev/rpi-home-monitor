# Medical-Grade Traceability Rules

This repository is managed with medical-device-grade engineering discipline.
That phrase describes the rigor expected for requirements, risk, security,
verification, and traceability records. It does not claim regulatory
compliance, certification, FDA clearance, CE marking, or approval.

Human review is required before AI-generated work is treated as quality-system
evidence.

## Required Change Review

No meaningful code, test, configuration, architecture, dependency,
hardware-interface, security, or behavior change may be made without
considering:

- user needs
- system requirements
- software requirements
- hardware requirements when applicable
- architecture
- safety risk
- cybersecurity risk
- tests
- traceability

Every meaningful change must update or explicitly confirm:

- relevant requirement IDs
- architecture links
- risk links
- security links
- test links
- traceability matrix entries

Future agents must not make untraced changes.

## Identifier System

Use these ID families:

- `UN-###`: user need
- `SYS-###`: system requirement
- `SWR-###`: software requirement
- `HWR-###`: hardware requirement
- `ARCH-###`: system architecture item
- `SWA-###`: software architecture item
- `HWA-###`: hardware architecture item
- `HAZ-###`: hazard
- `RISK-###`: safety risk
- `RC-###`: risk control
- `DFMEA-###`: design failure mode and effects analysis item
- `SEC-###`: security requirement or asset
- `THREAT-###`: cybersecurity threat
- `SC-###`: security control
- `TC-###`: verification or validation test case

## Code-Level Annotations

Every traceable code, test, workflow, build, script, configuration, and
hardware-interface file must contain at least one concise `REQ:` annotation.
Those requirements must be present in the traceability matrix and must trace
back to at least one user need, one system requirement, and one architecture
item. The automated checker enforces this for the traceable repository roots
declared in `tools/traceability/check_traceability.py`.

Use concise annotations:

- `REQ: SWR-###`
- `RISK: RISK-###`
- `SEC: SEC-###`
- `TEST: TC-###`

Annotate safety-critical logic, security-critical logic, data processing,
I/O, state machines, alarms, fault handling, authentication, authorization,
cryptography, update mechanisms, configuration handling, and hardware
interfaces. Do not annotate every line, and do not use annotations as a
substitute for updating the controlled requirement, architecture, risk,
security, verification, and matrix records.

## Pull Request Expectations

Every PR must:

- update affected docs
- update traceability
- add or update tests
- run traceability checks
- document assumptions and open questions

Do not merge if `python tools/traceability/check_traceability.py` fails.

If an existing repository rule conflicts with this discipline, preserve
safety, avoid behavior breakage, document the conflict, and add:

- `ASSUMPTION:`
- `OPEN QUESTION:`
- `REGULATORY REVIEW REQUIRED:`

Never claim regulatory compliance. Use "Prepared to support expert regulatory
review" when describing the purpose of these artifacts.
