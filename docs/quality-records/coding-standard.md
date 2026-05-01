# Coding Standard

Status: Draft prepared to support expert regulatory review.

## Baseline Standards

- Python code uses Ruff formatting and linting as configured in
  `pyproject.toml`.
- Server code follows the Flask app-factory, thin-route, service-layer pattern.
- Camera code preserves the lifecycle, platform, and hardware abstraction
  patterns already present in `app/camera/camera_streamer/`.
- Shell scripts and workflow files are production code and are linted in CI.
- Generated AI adapters are rebuilt from `scripts/ai/build_instruction_files.py`.

## Traceability Annotation Standard

Use concise annotations for meaningful code paths:

```text
REQ: SWR-###
RISK: RISK-###
SEC: SC-###
TEST: TC-###
```

Annotations are required where meaningful for:

- authentication and authorization
- cryptography and certificate handling
- OTA/update verification and install state
- camera capture, motion detection, and stream control
- storage cleanup and retention
- safety or fault state transitions
- configuration validation
- hardware interfaces
- security logging and audit events

Do not annotate every line. Place annotations at the function, class, or logic
block that owns the traceable behavior.

## Review Rules

- Meaningful changes must update affected requirement, risk, security, test,
  and traceability records.
- New annotations must reference existing IDs.
- `python tools/traceability/check_traceability.py` must pass before merge.
- OPEN QUESTION items are allowed in draft records, but must not be hidden in
  code comments without a linked requirement or risk record.
