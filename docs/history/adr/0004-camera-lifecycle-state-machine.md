# ADR-0004: Camera Lifecycle State Machine

## Status
Accepted

## Context
Camera `main.py` was a 243-line procedural script with sequential setup steps. Hard to test individual phases, difficult to see the overall flow, and error handling was inconsistent.

## Decision
Extract a `CameraLifecycle` class with explicit states:
```
INIT → SETUP → CONNECTING → VALIDATING → RUNNING → SHUTDOWN
```
Each state has a handler method that returns `True` (continue) or `False` (abort to shutdown).

## Rationale
- **Explicit states**: Clear progression visible in code and logs (`State → connecting`)
- **Testable**: Each state handler can be unit-tested independently
- **Fail-graceful**: Camera hardware failure in VALIDATING doesn't block RUNNING
- **Thin entry point**: `main.py` reduced to 55 lines (config + platform + lifecycle.run())
- **Constructor Injection**: config, platform, shutdown_event passed in — no globals

## Consequences
- `main.py` is no longer self-contained — requires understanding lifecycle.py
- State transitions are sequential (not event-driven) — sufficient for camera boot flow
- Adding new states (e.g., PAIRING for Phase 2 mTLS) is straightforward
