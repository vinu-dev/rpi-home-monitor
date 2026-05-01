# Hardware Requirements

Status: Draft prepared to support expert regulatory review.

Hardware exists and is implied by the repository. Hardware requirements are
therefore applicable.

| ID | Statement | Rationale | Source | Parent links | Risk/security links | Verification method | Linked tests | Status |
|---|---|---|---|---|---|---|---|---|
| HWR-001 | The server hardware shall be Raspberry Pi 4B or documented equivalent with sufficient RAM for server, recording, and UI services. | Server capacity is central to recording reliability. | `docs/hardware-setup.md` | SYS-011 | RISK-001, RISK-008 | Hardware smoke/manual review | TC-018 | Draft |
| HWR-002 | Camera hardware shall use Raspberry Pi Zero 2W or documented equivalent with supported camera module and hardware encoder constraints. | Camera capture limits drive safe mode selection. | `docs/hardware-setup.md`, `sensor_info.py` | SYS-011 | RISK-001, RISK-007 | Unit guardrails and hardware smoke | TC-018 | Draft |
| HWR-003 | Server and camera nodes shall provide WiFi/Ethernet network interfaces sufficient for local streaming and management. | Network availability is required for live view, recording, pairing, and OTA. | `docs/hardware-setup.md` | SYS-001, SYS-006 | RISK-005, SC-004 | Integration and smoke tests | TC-006, TC-018 | Draft |
| HWR-004 | Server storage shall include SD storage and support configured external USB storage for recordings. | Recording retention depends on writable storage. | `docs/requirements.md` | SYS-002, SYS-003 | RISK-003 | Storage tests, manual storage validation | TC-003, TC-018 | Draft |
| HWR-005 | Devices shall be powered by stable supplies appropriate for the Raspberry Pi models used. | Brownouts can cause data loss and false offline states. | `docs/hardware-setup.md` | SYS-011 | RISK-008 | Manual hardware validation | TC-018 | Draft |
| HWR-006 | Server and camera images shall support hardware-mediated reset or reflash recovery rather than software backdoor reset. | Recovery must not undermine authentication. | ADR-0022 | SYS-008 | RISK-006, SC-006 | Factory reset tests/manual review | TC-015, TC-018 | Draft |
| HWR-007 | Camera overlays and sensor detection shall support the documented sensor set and avoid exposing unsupported encoder modes. | Prevents camera boot or stream failure after configuration. | ADR-0023 | SYS-006, SYS-011 | RISK-007 | Board/profile tests | TC-012, TC-018 | Draft |
| HWR-008 | System time shall be synchronized through the local server/camera time-sync design. | Correct timestamps are required for clips, events, audit logs, and updates. | ADR-0019 | SYS-002, SYS-010 | RISK-005 | Version/time design tests and review | TC-017, TC-018 | Draft |
