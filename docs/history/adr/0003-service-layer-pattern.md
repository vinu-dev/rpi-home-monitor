# ADR-0003: Service Layer Pattern for Business Logic

## Status
Accepted

## Context
Flask API routes were accumulating orchestration logic — coordinating store, streaming, audit, and USB operations directly. This made routes hard to test and created tight coupling.

## Decision
Extract business logic into service classes:
- `CameraService` — camera CRUD, lifecycle, streaming coordination
- `StorageService` — USB select, format, eject, status
- Routes become thin HTTP adapters: parse request → call service → return response

## Rationale
- **Testability**: Services can be unit-tested with mock dependencies (no Flask app needed)
- **Single Responsibility**: Routes handle HTTP, services handle business logic
- **Reusability**: Services can be called from CLI tools or background jobs, not just HTTP
- **Constructor Injection**: Dependencies (store, streaming, audit) passed via `__init__()`, no globals

## Consequences
- Additional indirection layer between routes and data
- Service methods return tuples `(result, error, status)` — consistent but verbose
- Existing API contract (HTTP endpoints, request/response format) unchanged
- Audit logging wrapped in fail-silent pattern — audit failures never break operations
