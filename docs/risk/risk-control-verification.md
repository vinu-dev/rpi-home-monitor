# Risk Control Verification

Status: Draft prepared to support expert regulatory review.

| Control ID | Control statement | Linked risks | Linked requirements | Verification method | Linked tests | Evidence status |
|---|---|---|---|---|---|---|
| RC-001 | Surface camera/server health, heartbeat state, and hardware faults to reduce silent loss of monitoring. | RISK-001, RISK-005, RISK-008 | SYS-007, SWR-004, SWR-012 | Unit/integration tests and hardware smoke | TC-005, TC-014, TC-018 | Draft |
| RC-002 | Enforce authenticated operator access and authenticated machine trust boundaries. | RISK-002 | SYS-004, SYS-005, SWR-001, SWR-002, SWR-003 | Security, pairing, and contract tests | TC-004, TC-011, TC-012 | Draft |
| RC-003 | Manage recording storage with usage telemetry and FIFO cleanup. | RISK-003 | SYS-003, SWR-007 | Unit tests and manual storage validation | TC-003 | Draft |
| RC-004 | Verify OTA bundles, stage safely, reject concurrent update actions, and rely on A/B rollback design. | RISK-004 | SYS-009, SWR-010, SWR-016 | OTA tests and release validation | TC-009, TC-013 | Draft |
| RC-005 | Use signed heartbeat, offline detection, motion event validation, and alert center surfaces. | RISK-005, RISK-008 | SYS-007, SWR-004, SWR-008, SWR-017 | Unit/integration tests | TC-005, TC-014, TC-019 | Draft |
| RC-006 | Preserve encryption-at-rest design and hardware-mediated reset without software backdoor recovery. | RISK-006 | SYS-008, SWR-018, HWR-006 | Security review and factory reset tests | TC-015 | Draft |
| RC-007 | Validate camera capabilities and hardware encoder limits before persisting or applying stream configuration. | RISK-001, RISK-007 | SYS-006, SYS-011, SWR-011, HWR-007 | Camera service and board profile tests | TC-012, TC-018 | Draft |
| RC-008 | Monitor environmental/hardware indicators and alert on degraded conditions. | RISK-008 | SYS-007, SYS-011, SWR-017 | Health and alert tests, hardware smoke | TC-005, TC-014, TC-018 | Draft |
| RC-009 | Require traceability records, code annotations, automated checker, and CI workflow for meaningful changes. | RISK-009 | SYS-012, SWR-019 | Traceability checker | TC-020 | Draft |
