# ADR-0002: JSON File Storage Instead of Database

## Status
Accepted

## Context
The server needs to persist camera configs, user accounts, and system settings. Options:
1. SQLite database
2. JSON files with atomic writes
3. Key-value store (Redis, LevelDB)

## Decision
Use JSON files (`cameras.json`, `users.json`, `settings.json`) with thread-safe atomic writes via the `Store` class.

## Rationale
- **Simplicity**: No database engine to install, configure, or maintain
- **Debuggability**: `cat cameras.json` shows all state — no query tools needed
- **Embedded target**: RPi 4B has limited resources; JSON files are zero-overhead
- **Scale**: Phase 1 supports 1-4 cameras, <10 users — JSON handles this trivially
- **Atomic writes**: `tempfile` + `os.replace()` prevents corruption on power loss
- **LUKS encrypted /data**: All JSON files encrypted at rest

## Consequences
- No SQL queries — all filtering is in Python
- No concurrent write safety beyond thread locks (single-process only)
- If scale reaches 50+ cameras or 100+ users, migration to SQLite may be needed
- No schema enforcement at storage layer (validated in application code)
