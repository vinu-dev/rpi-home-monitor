# ADR-0006: Modular Monolith Architecture

## Status
Accepted

## Context
External reviewers suggested various architectures: microservices, plugin systems, event sourcing. We need to balance contributor-friendliness with embedded system constraints.

## Decision
Adopt a **modular monolith** — one deployable per device, feature-shaped modules with clear boundaries, no microservices.

### Module Structure
```
Server: monitor/
├── api/          # HTTP routes (thin adapters)
├── services/     # Business logic (camera, storage, streaming, health)
├── models.py     # Data structures
├── store.py      # JSON persistence (Repository pattern)
└── auth.py       # Authentication + session management

Camera: camera_streamer/
├── lifecycle.py  # State machine (orchestration)
├── stream.py     # FFmpeg RTSP streaming
├── capture.py    # V4L2 camera hardware
├── health.py     # Health monitoring
└── platform.py   # Hardware abstraction
```

## Rationale
- **Embedded constraints**: RPi 4B (1GB RAM) and Zero 2W (512MB RAM) can't run multiple services
- **Single deployable**: One `systemctl restart` deploys everything — no orchestrator needed
- **Feature boundaries**: Services encapsulate domain logic; routes are thin HTTP shells
- **Contributor-friendly**: Clear module ownership, no cross-cutting framework magic
- **No premature distribution**: Microservices add network latency, partial failure modes, deployment complexity — none of which help a single-board computer

## Consequences
- All code runs in one process per device — shared memory, no RPC
- Module boundaries are conventions (not enforced by runtime)
- If a service crashes, the whole process restarts (systemd handles this)
- Migration to separate services is possible later by extracting modules (unlikely needed)
