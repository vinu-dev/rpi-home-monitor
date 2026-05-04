# Feature Spec: Export Audit Log To CSV/JSON For Retention And Forensic Review

Tracking issue: #247. Branch: `feature/247-export-audit-log`.

## Title

Admin-gated streaming export of the security audit log as CSV or JSON,
filterable by time range, event type, and actor.

## Goal

Restate of issue #247: an admin can download the full security audit
log — or a filtered subset by time range, event type, or actor — as
CSV or JSON for offline retention, compliance review, or post-incident
forensic analysis. Today `app/server/monitor/api/audit.py` exposes only
`GET /events?limit=1..200`, so after an incident the operator's only
options are scrolling the dashboard 200 rows at a time or SSH'ing onto
the box. This spec adds a streamed `GET /events/export` endpoint and
the corresponding controls on the `/logs` page so an admin can download
the audit history in a structured, machine-readable format.

This closes the operator-facing half of the medical-grade traceability
work landed in PRs #221 / #225 / #227, which leaned on audit
immutability as a control: making that audit trail exportable to
operators is what turns "we keep an immutable log" into a usable
compliance / forensic feature. It directly addresses the design-standards
goal that the product "feels like a real product, not a prototype".

## Context

Existing code this feature must build on, not re-implement:

- `app/server/monitor/api/audit.py` — the audit blueprint already
  enforces `@admin_required` on `GET /events` and `@csrf_protect` on
  `DELETE /events`. The new export endpoint reuses the same admin gate
  and the same `current_app.audit` service handle.
- `app/server/monitor/services/audit.py` (`AuditLogger`) — already owns
  the canonical schema (`timestamp`, `event`, `user`, `ip`, `detail`),
  the `/data/logs/audit.log` location, the `_lock` for thread-safe
  writes, and the `AUDIT_LOG_CLEARED` chain-of-custody sentinel pattern.
  The export adds a generator-based read path (`iter_events(filters)`)
  alongside the existing `get_events(limit, event_type)` reader; it does
  not replace it.
- `app/server/monitor/__init__.py:628` — registers `audit_bp` at
  `/api/v1/audit`; the new route lives under that same prefix as
  `/api/v1/audit/events/export`.
- `app/server/monitor/templates/logs.html` — already the home of
  audit-log viewing per ADR-0025 ("Security tab retired; audit-log
  management lives at `/logs`"). It already has admin-only filter
  chips, a free-text user filter, and a from/to date picker. The export
  controls land in the same toolbar as the existing "Clear all entries"
  admin button (`templates/logs.html:48-76`).
- `app/server/monitor/auth.py:72` (`_check_rate_limit`) — the IP-based
  two-tier rate-limit used on login is the pattern the export endpoint
  reuses (separate counter namespace) so a compromised admin session
  can't be used to slowly exfiltrate via repeated full exports.
- ADR-0011 (`docs/history/adr/0011-auth-hardening.md`) — sets the wider
  auth-hardening shape this feature builds inside. The export action is
  itself an auditable event.
- ADR-0018 Slice 3 — the audit-log surface used by the dashboard's
  recent-activity strip. Export must not affect that read path.
- Market backlog item #92 ("Support bundle / diagnostic export", P1 W2)
  in `docs/history/planning/market-feature-backlog-100.md` shares the
  same operator rationale; export-only is a tighter scope that does
  not require the bundle assembly logic.

## User-Facing Behavior

### Primary path — admin exports the full audit log

1. Admin opens `/logs` (the existing audit-log viewer; no Settings
   change since ADR-0025 retired the Security tab).
2. Above the existing filter row, a new admin-only "Export" toolbar is
   visible with:
   - "Format" toggle — `CSV` / `JSON` (radio; default CSV).
   - "Apply current filters" checkbox (default on). When on, the
     export uses the same `from`, `to`, user-filter, and category chip
     the operator already has set on screen. When off, a full export
     runs across the entire log.
   - "Export" button.
3. Admin clicks Export. The browser issues
   `GET /api/v1/audit/events/export?format=csv&start=…&end=…&event_type=…&actor=…`
   with the operator's session cookie + CSRF token. (CSRF protection is
   the same belt-and-braces pattern the `DELETE /events` route uses,
   even though export is a read.)
4. Server validates filters, opens the file under `_lock` only long
   enough to install a generator (no full-file buffering), and streams
   the response body using a chunked `Content-Disposition: attachment;
   filename="audit-<UTC-iso>.csv"` (or `.json`).
5. Browser saves the file. The operator opens it in Excel / `jq` / a
   SIEM ingest pipeline.
6. Server emits a single `AUDIT_LOG_EXPORTED` audit event recording the
   actor, IP, format, applied filters, and final row count emitted.
   This event is itself auditable — exports of the audit log leave
   their own trace, mirroring `AUDIT_LOG_CLEARED`.

### Primary path — admin exports a filtered slice for an incident

1. Admin sets `from=2026-04-01` / `to=2026-04-08` and category chip
   `LOGIN_FAILED` on `/logs`.
2. Admin clicks Export with "Apply current filters" on.
3. Server streams only the matching rows in the chosen format.
4. The `AUDIT_LOG_EXPORTED` audit event records the filter shape
   (`{"start":"2026-04-01T00:00:00Z","end":"2026-04-08T23:59:59Z","event_type":"LOGIN_FAILED","actor":""}`)
   and the final row count.

### Format details

- **CSV**: header row `timestamp,event,user,ip,detail`. Each subsequent
  row is one audit entry, RFC-4180-quoted. Free-text fields (`detail`,
  `user`) are double-quoted; embedded `"` is escaped to `""`; fields
  are emitted with `\r\n` row separators. Newlines inside `detail` are
  preserved inside the quoted field. UTF-8 with no BOM.
- **JSON**: a single top-level JSON array streamed element-by-element
  (`[`, then `{...}`, `,{...}`, …, then `]`). Each element keeps the
  original five-key shape — no schema rewrite, so a v1 export remains
  diff-able against `audit.log` itself. Trailing `\n` after the closing
  bracket. Implementer chooses NDJSON if benchmarks show streaming
  overhead — see Open Questions.

### Filter semantics

- `format` (required): `csv` | `json`. Anything else → `400`.
- `start`, `end` (optional, ISO-8601 Z): inclusive lower bound,
  inclusive upper bound. If only one is given, the other side is
  unbounded. Invalid timestamp → `400` with a clear error.
- `event_type` (optional): exact match against the audit `event` field.
  Empty string == no filter (matches today's `GET /events` semantics).
- `actor` (optional): exact match against the audit `user` field.
  Empty string == no filter. Substring match is **not** supported
  (forensic exports want exact attribution).
- Filters are AND-combined.
- Result ordering is **oldest-first** (chronological), opposite of the
  on-screen `GET /events` view, because that is what every downstream
  CSV / SIEM tool expects.

### Failure states (designed, not just unit-tested)

- Non-admin session → `401` (matches existing `@admin_required`).
- CSRF token missing or invalid → `403` (matches `DELETE /events`).
- Invalid `format` → `400 {"error":"format must be csv or json"}`. No
  partial body is streamed.
- Invalid `start` or `end` ISO-8601 → `400` with a clear error.
- `start > end` → `400 {"error":"start must be <= end"}`.
- `audit.log` missing or unreadable → `200` with an empty CSV header
  row / empty JSON `[]`. The export is still emitted as an
  `AUDIT_LOG_EXPORTED` event with `row_count: 0`.
- Client disconnects mid-stream → the server-side generator stops on
  the next yield (Flask / Werkzeug raises `BrokenPipeError` /
  `ClientDisconnected`); the file handle is closed in a `finally:`
  block and an `AUDIT_LOG_EXPORTED` event is still emitted with
  `truncated: true` and the row count actually flushed before the
  disconnect.
- Disk read error mid-stream → the generator logs the underlying
  `OSError`, stops emitting rows, and the response body simply ends.
  An `AUDIT_LOG_EXPORTED` event is emitted with `truncated: true,
  reason: "io_error"`.
- Rate-limit exceeded (more than `EXPORT_RATE_LIMIT_MAX` exports per
  admin per `EXPORT_RATE_LIMIT_WINDOW`) → `429` with a `Retry-After`
  header. The rejected attempt is itself logged as
  `AUDIT_LOG_EXPORT_DENIED`.
- Concurrent `DELETE /events` (clear) running while export streams →
  acceptable: the export captured a snapshot via the line iterator;
  `AUDIT_LOG_CLEARED` will appear in the *next* export but not this
  one. No file corruption because the writer truncates atomically
  under `_lock`. Document this in the spec, do not engineer around it.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: A logged-in admin can call
  `GET /api/v1/audit/events/export?format=csv` and receive a
  `text/csv; charset=utf-8` response with `Content-Disposition:
  attachment` whose filename includes the UTC timestamp. **[unit:
  `app/server/tests/unit/test_api_audit_export.py`]**
- AC-2: A logged-in admin can call
  `GET /api/v1/audit/events/export?format=json` and receive an
  `application/json` response that parses as a JSON array. **[unit]**
- AC-3: A non-admin session is rejected with `401` and no body is
  streamed. **[contract: `app/server/tests/contracts/test_api_contracts.py`]**
- AC-4: Missing or invalid CSRF on the export route is rejected with
  `403`. **[security: `app/server/tests/security/test_security.py`]**
- AC-5: `format` other than `csv` / `json` returns `400`. Invalid
  `start`, `end`, or `start > end` returns `400` with a clear error
  message. **[unit]**
- AC-6: With no filters, the export contains every entry currently in
  `audit.log`. **[unit, fixture log of N rows]**
- AC-7: With `start` / `end` set, only entries whose `timestamp` falls
  in `[start, end]` (inclusive) are emitted. **[unit, hand-rolled
  fixture spanning the boundary]**
- AC-8: With `event_type=LOGIN_FAILED`, only matching rows are
  emitted; with `actor=admin`, only matching rows are emitted; with
  both, AND semantics apply. **[unit]**
- AC-9: CSV output is RFC-4180 compliant: header row present, fields
  containing `,`, `"`, or `\n` are double-quoted, and embedded `"`
  is escaped to `""`. **[unit, dedicated escape-cases test]**
- AC-10: JSON output is a single valid JSON array; piping the body
  to `json.loads` round-trips every row. **[unit]**
- AC-11: Export is streamed: the server does not load the full audit
  log into memory before sending the first byte. Verified by reading
  a 50 MB synthetic audit fixture and asserting peak Python heap
  during export stays under a fixed bound (e.g., < 8 MB above
  baseline). **[integration: `app/server/tests/integration/test_audit_export_streaming.py`]**
- AC-12: When the HTTP client disconnects mid-stream, the server's
  file handle is closed promptly and an `AUDIT_LOG_EXPORTED` audit
  event is written with `truncated: true` and the row count actually
  flushed. **[integration with simulated disconnect]**
- AC-13: Every successful export emits exactly one
  `AUDIT_LOG_EXPORTED` audit event whose `detail` carries
  `format`, applied filters, and `row_count`. **[unit]**
- AC-14: The export endpoint is rate-limited per admin user (and per
  IP) using the same two-tier window pattern as
  `auth._check_rate_limit`. Beyond the hard limit, the response is
  `429` and an `AUDIT_LOG_EXPORT_DENIED` event is written. **[unit]**
- AC-15: Result rows are ordered oldest-first regardless of how
  events are stored on disk. **[unit]**
- AC-16: The `/logs` page renders the new "Export" toolbar only when
  `isAdmin === true`; viewers do not see it. **[browser smoke +
  template review]**
- AC-17: Clicking Export with "Apply current filters" on issues a
  request that carries the same `from`, `to`, category chip, and
  user filter currently shown on screen. **[browser smoke]**
- AC-18: Concurrent `DELETE /events` while an export is streaming
  does not corrupt the export body or the on-disk log; the streamed
  export reflects the snapshot taken at the start of streaming.
  **[integration]**

## Non-Goals

- Scheduled / automated export. v1 is on-demand only. Cron-style
  delivery layers onto the future #239 webhook channel if needed.
- Signed manifests or evidence-grade integrity proofs. See market
  backlog item #97 ("End-to-end signed evidence manifests").
- Redaction or scrubbing of operator IPs / usernames before export.
  The export is full-fidelity; the operator owns the resulting file.
- Cross-instance audit aggregation across multiple servers.
- Push to S3, rclone, or any offsite delivery channel. That is the
  scope of #243 (offsite-backup channel); this issue is the
  on-demand-download path only.
- Server-side compression (gzip) of the export body. Out of scope for
  v1; the browser can request gzip via `Accept-Encoding` and Werkzeug
  may apply it, but this spec does not require it.
- New audit event types beyond `AUDIT_LOG_EXPORTED` and
  `AUDIT_LOG_EXPORT_DENIED`.

## Module / File Impact List

**New code:**

- `app/server/monitor/api/audit.py` — add `GET /events/export` route.
  Admin-gated, CSRF-protected, streams via Flask `Response(generator,
  mimetype=…, headers=…)`. Validates `format`, `start`, `end`,
  `event_type`, `actor`. Calls `current_app.audit.iter_events(filters)`
  to produce the row generator. Wraps emission in a try/finally that
  always writes one `AUDIT_LOG_EXPORTED` (or `AUDIT_LOG_EXPORT_DENIED`)
  event.
- `app/server/tests/unit/test_api_audit_export.py` — covers
  AC-1 through AC-10, AC-13, AC-14, AC-15.
- `app/server/tests/integration/test_audit_export_streaming.py` —
  covers AC-11, AC-12, AC-18 (50 MB fixture, simulated disconnect,
  concurrent clear).

**Modified code:**

- `app/server/monitor/services/audit.py` — add `iter_events(start=None,
  end=None, event_type="", actor="")` returning a generator over the
  audit-log file. Implementation reads line-by-line (no `.read()`),
  parses each line as JSON, applies the filters, and yields the entry
  dict. Comparable to `get_events()` but never materializes a list and
  walks oldest-first by default. Adds new event-type constants
  `AUDIT_LOG_EXPORTED` and `AUDIT_LOG_EXPORT_DENIED` to the docstring
  enum.
- `app/server/monitor/templates/logs.html` — add admin-only "Export"
  toolbar (format radio, "Apply current filters" checkbox, Export
  button) above the existing filter row. Wire the click handler to
  build the query string from the page's current Alpine state and
  trigger a `window.location.assign(…)` so the browser handles the
  download. Add a small flash message for `429` responses ("Export
  rate-limited; try again in N seconds").
- `app/server/monitor/static/css/style.css` — minor additions for the
  toolbar layout (or reuse existing chip / inline-form classes; no new
  CSS preferred where possible).
- `app/server/tests/unit/test_svc_audit.py` — extend with
  `iter_events` cases (filter correctness, oldest-first ordering,
  generator behavior, malformed-line skipping mirrors `get_events`).

**Dependencies:**

- No new external dependencies. CSV emission uses stdlib `csv` against
  an `io.StringIO` per chunk (or `csv.writer` against a `_StreamingBuf`
  pattern). JSON emission uses stdlib `json.dumps(separators=(",",":"))`
  per row, matching the existing on-disk format.

**Out-of-tree:**

- No camera-side change.
- No Yocto recipe change.
- No new `meta-home-monitor/` work.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | new contract assertions in `test_api_contracts.py` for `GET /api/v1/audit/events/export` (admin gate, CSRF, format validation, content-type) |
| Security-sensitive path | `pytest app/server/tests/security/ -v` — admin gating, CSRF, rate limit, CSV-injection escape, audit self-emission |
| Frontend / templates | manual browser check on `/logs` Export toolbar (admin and viewer, both formats, filter pass-through, 429 toast) |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Repository governance | `python tools/docs/check_doc_map.py`, `python scripts/ai/validate_repo_ai_setup.py`, `pre-commit run --all-files` |
| Coverage | server `--cov-fail-under=85` must hold after the new code lands |
| Hardware behavior | one row added to `scripts/smoke-test.sh`: "admin downloads CSV export; file opens in Excel; row count matches `wc -l` of `/data/logs/audit.log` minus 1 for header" |
| Shell script hygiene | `bash -n scripts/smoke-test.sh` and `shellcheck scripts/smoke-test.sh` stay clean after touching the smoke test |

The Implementer must include the depot-rule validation evidence block
listed in `docs/ai/validation-and-release.md` "Depot Rule Gate".

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-247-1 | Streaming a multi-MB audit log buffers the whole file in memory and OOMs the gunicorn worker, killing the dashboard. | Moderate (operational) | Medium without control / Low with | RC-247-1: `iter_events` is a generator; the route emits via Flask `Response(stream)` and never calls `.read()`. AC-11 enforces a fixed memory bound on a 50 MB fixture. |
| HAZ-247-2 | An authenticated-but-compromised admin session is used to slowly exfiltrate the audit log via repeated full exports. | Major (security) | Low | RC-247-2: per-admin + per-IP two-tier rate limit reusing the `auth._check_rate_limit` pattern (separate counter namespace, e.g., 5 / hour soft, 10 / hour hard). Hard-limit hits emit `AUDIT_LOG_EXPORT_DENIED`. AC-14 enforces. |
| HAZ-247-3 | CSV free-text fields (`detail`) contain a leading `=`, `+`, `-`, `@`, `\t`, or `\r` and a downstream Excel user opens the file → CSV-injection / formula execution. | Moderate (security on the operator's workstation) | Medium | RC-247-3: every CSV cell whose first character is in `{=, +, -, @, \t, \r}` is prefixed with a single quote (`'`) before quoting, per OWASP "CSV Injection" guidance. Documented in code; AC-9 covers escape correctness, plus a dedicated regression test for each lead character. |
| HAZ-247-4 | The export endpoint is invoked without CSRF and a malicious cross-site link triggers a download containing all audit history → information disclosure if the operator's browser leaks the response. | Major (security) | Low | RC-247-4: route is `@csrf_protect` (same as `DELETE /events`). AC-4 enforces. |
| HAZ-247-5 | Export hits a malformed line in `audit.log` and the generator raises an unhandled `JSONDecodeError`, terminating the stream and confusing the admin. | Minor (operational) | Low | RC-247-5: `iter_events` mirrors `get_events`'s defensive parse — `try/except json.JSONDecodeError: continue`. Malformed lines are skipped; they appear instead as an `AUDIT_LOG_EXPORT_TRUNCATED` (informational) only when the number skipped is non-zero. |
| HAZ-247-6 | The export self-event (`AUDIT_LOG_EXPORTED`) is forgotten on the disconnect / error path → traceability gap (an export ran but the operator can't see who ran it). | Major (compliance) | Low | RC-247-6: route emits the event in a `finally:` block with `truncated`, `reason`, and `row_count`. AC-12, AC-13 enforce both happy and disconnect paths. |
| HAZ-247-7 | Concurrent `DELETE /events` truncates the on-disk file mid-export → corrupted CSV / JSON in the operator's download. | Minor (operational) | Low | RC-247-7: the export reads the file via a single `open()` opened *before* the first yield; on Linux the open file descriptor still references the original inode after truncation, so the streamed content reflects the snapshot at open time. AC-18 enforces. Documented in spec as expected behavior, not a bug. |
| HAZ-247-8 | Filter validation is loose and a crafted `start`/`end` causes the route to spend CPU comparing every line against an unparseable timestamp. | Minor (DoS) | Low | RC-247-8: parse `start` / `end` once at route entry; reject with `400` on any `ValueError`. AC-5 enforces. |

Reference `docs/risk/` for the existing register; this spec adds the
above rows.

## Security

Threat-model deltas (Implementer fills concrete `THREAT-` / `SC-` IDs):

- **Adds** a new admin-gated read endpoint over the audit log. The
  audit log already contains LAN IPs, usernames, login-failure
  patterns, OTA outcomes, certificate events, and pairing events. The
  threat is information disclosure / exfiltration through that
  endpoint.
- **Sensitive paths touched**: `**/auth/**` (no behavioral change to
  auth, but the admin gate plus rate limit live here), `**/secrets/**`
  (no — no secret material is exported). The export does not touch
  pairing, OTA, or certificate code paths. Per `docs/ai/roles/architect.md`
  these are the paths needing extra scrutiny — flagged here.
- **Authorization**: `@admin_required` is reused, no new role.
- **CSRF**: `@csrf_protect` is applied to a `GET` route on purpose
  (belt-and-braces). Implementer ensures the CSRF cookie / token
  pattern works for `GET` (today it is enforced on state-changing
  verbs); if the existing decorator is verb-aware, add an
  `enforce_on_get=True` flag rather than weakening it elsewhere.
- **Rate limiting**: per-admin + per-IP two-tier limiter is required.
  Hard-limit hits return `429` and emit `AUDIT_LOG_EXPORT_DENIED`. The
  limiter uses a separate counter namespace from
  `_login_attempts` so login lockouts are not affected.
- **CSV injection**: explicitly mitigated per RC-247-3 (prefix `'` for
  rows beginning with formula triggers). Document in the route's
  docstring with a link to OWASP CSV Injection.
- **Audit self-emission**: every export attempt — successful, denied,
  truncated — emits exactly one `AUDIT_LOG_EXPORTED` /
  `AUDIT_LOG_EXPORT_DENIED` event. This is the only way to detect
  exfiltration after the fact, so its completeness is mandatory
  (covered by AC-12, AC-13, AC-14).
- **No new secret storage**, no new TLS surface, no new outbound
  network call. The export does not introduce a new attack surface
  beyond the existing audit-read path; it lifts the cap from 200 rows
  to all rows under the same admin gate.
- **Out of scope**: redaction of IPs / usernames; signed manifests;
  encryption of the response body. The download is over the existing
  TLS surface; `/data` is LUKS-encrypted at rest.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-247` — User need: "As an admin, I want to download the security
  audit log (filtered or full) so I can retain it for compliance and
  perform forensic review after an incident, without SSH'ing onto the
  device."
- `SYS-247` — System requirement: "The system shall provide an
  admin-gated, rate-limited, streaming export of the security audit
  log in CSV or JSON, filterable by time range, event type, and actor,
  and shall record each export attempt as an audit event."
- `SWR-247-A` … `SWR-247-F` — Software requirements (one per
  functional area: route + admin gate, filter validation, streaming
  read, format emission, audit self-emission, rate limit).
- `SWA-247` — Software architecture item: "Generator-based
  `AuditLogger.iter_events` consumed by Flask `Response(stream, …)` in
  the existing `audit_bp`; reuses `@admin_required`, `@csrf_protect`,
  and the auth rate-limit pattern."
- `HAZ-247-1` … `HAZ-247-8` — listed above.
- `RISK-247-1` … `RISK-247-8` — one per hazard.
- `RC-247-1` … `RC-247-8` — one per risk control.
- `SEC-247-A` (admin gate completeness), `SEC-247-B` (CSRF on GET),
  `SEC-247-C` (rate limit), `SEC-247-D` (CSV-injection escape),
  `SEC-247-E` (audit self-emission completeness),
  `SEC-247-F` (no secret material in response).
- `THREAT-247-1` (slow exfiltration via repeated exports),
  `THREAT-247-2` (CSV injection on operator workstation),
  `THREAT-247-3` (CSRF-driven download leakage),
  `THREAT-247-4` (audit self-event omitted on error path).
- `SC-247-1` … `SC-247-N` — controls mapping the threats above.
- `TC-247-AC-1` … `TC-247-AC-18` — one test case per acceptance
  criterion above.
- Trace headers in new files follow the existing convention
  (`# REQ: …; RISK: …; SEC: …; TEST: …`); reuse `SWR-009`, `RISK-020`,
  `SC-008`, `SC-020`, `TC-017` from the existing audit module so the
  matrix update is additive.

## Deployment Impact

- Yocto rebuild needed: **no**. No new external dependencies, no
  changes to `meta-home-monitor/`, no new system service.
- OTA path: standard server image OTA. No data migration —
  `audit.log` schema is unchanged. The new endpoint is dormant on
  upgrade until an admin clicks Export.
- Hardware verification: yes — required, but lightweight. Add one row
  to `scripts/smoke-test.sh`: an admin downloads a CSV export; the
  resulting file opens in Excel / `python -c "import csv; …"`; the
  row count equals `wc -l /data/logs/audit.log` minus the header. No
  new device or harness needed.
- Default state on upgrade: no behavioral change visible to operators
  who never click the new button. Viewers see no new control.
- Rollback: pure server change; reverting the image rolls back the
  endpoint with no on-disk artefacts to clean up.

## Open Questions

(None blocking; design proceeds. Implementer captures answers in PR
description.)

- OQ-1: Should JSON output be a single top-level array (`[ {…}, {…} ]`)
  or NDJSON (one JSON object per line)? NDJSON is friendlier for
  streaming and matches how `audit.log` is already shaped on disk;
  a single array is friendlier for `jq` / Excel-via-Power-Query.
  **Recommendation**: ship the top-level array (it stays directly
  diff-able against `get_events`'s response shape and avoids two
  formats); revisit if a SIEM consumer asks for NDJSON.
- OQ-2: What rate-limit numbers should we pick? `auth._check_rate_limit`
  uses 5 (soft) / 10 (hard) per IP per ~minute. Exports are bigger
  but rarer.
  **Recommendation**: 3 (soft, log a warning) / 6 (hard, `429`) per
  admin per hour, with the same per-IP fallback.
- OQ-3: Should the export include the in-memory tail of any audit
  events that have not yet been flushed to disk? `AuditLogger.log_event`
  flushes synchronously today, so this is a no-op in practice.
  **Recommendation**: stream from disk only; document that the export
  reflects the file at the moment of `open()`.
- OQ-4: Should we cap the upper bound on row count emitted (e.g.,
  refuse to stream more than 1 M rows)? A truly huge audit log
  signals a different problem (rotation broken).
  **Recommendation**: no hard cap in v1; the streaming design makes
  the size question irrelevant. Add a knob in `Settings` only if a
  field operator hits a real ceiling.
- OQ-5: Should the export route accept `POST` with a JSON body
  instead of `GET` with query strings, to avoid filter parameters
  showing up in proxy access logs? `GET` is more browser-friendly for
  a downloadable file; query parameters carry no secret data.
  **Recommendation**: `GET` with query string for v1; document that
  filter parameters appear in access logs (which is already true for
  `GET /events`).

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): the new generator lives
  on `AuditLogger`; the route is a thin HTTP adapter that does
  validation, emits the response, and writes the self-event.
- Preserve modular monolith (ADR-0006): no new daemon, no new queue,
  no new background worker. Export runs synchronously inside the
  request thread; streaming keeps memory bounded.
- `/data/logs/audit.log` is the only source of truth — do not
  duplicate audit data into a side-table for the export.
- Audit self-emission must run in a `finally:` block; missing it is a
  compliance gap, not a minor bug. Tests must cover both happy and
  disconnect paths (AC-12, AC-13).
- Reuse `@admin_required`, `@csrf_protect`, and the
  `auth._check_rate_limit` pattern; do not invent new auth or
  limiting primitives.
- CSV escaping must be RFC-4180 *and* OWASP-CSV-injection-safe; both
  matter and they don't overlap fully (RFC-4180 alone does not
  prevent `=cmd|...` from running in Excel).
- Route logs are not a substitute for audit events; both happen.
- Tests + docs ship in the same PR as code, per `engineering-standards`.
