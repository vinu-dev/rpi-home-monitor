# Feature Spec: Active-Sessions UI — List Logged-In Devices, Per-Session / Bulk Revoke

Tracking issue: #246. Branch: `feature/246-active-sessions-ui`.

## Title

Server-side session inventory with a Settings → Security UI that lists every
currently authenticated session on the account and lets the operator (or an
admin) revoke any single session or "sign out all other devices" from a
running browser.

## Goal

Users open Settings → Security and see every active session on their
account: when it logged in, when it was last active, the source IP, a
parsed user-agent (browser + OS), and a "this session" indicator on the
row representing the currently rendering browser. From that view they
revoke any single session or click "Sign out other devices" to revoke
every session except the one they are looking at. Admins additionally
see all users' sessions and can force-terminate any of them.

The user-visible promise is "I lost my phone / used a TOTP recovery
code / rotated an admin password — I can immediately confirm what is
logged in and force any device off." Today there is no such surface:
Flask's default signed-cookie session is stateless, so there is no
server-side index to enumerate or revoke. ADR-0011 explicitly scoped
this work as a follow-up to authentication hardening (see
`docs/history/adr/0011-auth-hardening.md` § Session Management); this
spec delivers the server-side store and the operator surface that
ADR-0011 already implies.

## Why this fits the mission

`docs/ai/mission-and-goals.md` says the product should be
"trustworthy ... feels like a real product, not a prototype." Once
TOTP (#238) lands the post-login lifecycle becomes the next-weakest
link: a *login* is checked but a long-lived authenticated cookie has
no enumerate-or-revoke control plane except clearing the user
database. Home Assistant ships an explicit refresh-token list with a
"Delete" action per session as the open-source precedent for this UX
(`docs/ai/repo-map.md` cites HA as a comparator throughout); Frigate
has no equivalent, which the issue body flags as a leapfrog
opportunity. The work is contained to `app/server/`: new server-side
session store, a new `session_service`, three new API endpoints, a
Settings → Security tab — no camera change, no Yocto rebuild, no new
external dependencies.

`docs/ai/design-standards.md` requires mutable runtime state on
`/data` (not in the source tree); the store ships `sessions.json`
alongside the existing `users.json` / `cameras.json` / `settings.json`
on `/data/config/`, atomic-write the same way (ADR-0002).

## Context

Existing code this feature must build on:

- `app/server/monitor/auth.py:33` — Blueprint `auth_bp` mounted at
  `/api/v1/auth`. Login (line 251) calls `session.clear()` then
  populates Flask's `session` dict with `user_id`, `username`, `role`,
  `created_at` (epoch), `last_active` (epoch),
  `must_change_password`. Today this rides Flask's default signed-
  cookie `SessionInterface` — *no server-side record exists*.
- `app/server/monitor/auth.py:129` — `_is_session_valid()` reads
  `last_active` / `created_at` from the session dict and enforces 60-
  minute idle + 24-hour absolute timeouts. Both decorators
  (`login_required` line 174, `admin_required` line 196) call it,
  then bump `session["last_active"] = time.time()`. This is the only
  place the session is mutated on every request — the new server-side
  store must be updated here too, otherwise revocation only sticks
  until the next request bumps the stale stamp.
- `app/server/monitor/auth.py:237` — `auth_check()` is the
  nginx-`auth_request` validation hook. **Hot path**: called on every
  HLS / WebRTC segment fetch. If revocation requires a JSON-store
  read on every segment we will saturate the I/O on a multi-camera
  deploy; the spec uses a small in-process cache (read-through with
  TTL bounded by idle-timeout granularity) to keep this cheap. See
  RISK-246-3.
- `app/server/monitor/auth.py:42` — ADR-0011 lockout thresholds.
  Revoking does **not** touch `failed_logins` / `locked_until` —
  they're independent guardrails (lockout = "this user can't log in
  right now"; revocation = "this *session* is dead"). The acceptance
  criteria pin both behave correctly when intermixed.
- `app/server/monitor/store.py:33` — `Store` class is the single
  thread-safe atomic-write JSON layer. It owns `self._lock` and the
  tmp-rename atomic write helper (line 59). The new
  `sessions.json` rides this store; no new persistence primitive.
- `app/server/monitor/store.py:22` — `_filter_known()` migration-
  tolerant loader drops unknown keys and lets dataclass defaults
  fill in missing ones. New `Session` dataclass loads via the same
  helper.
- `app/server/monitor/services/audit.py:8` — string-based event log.
  Existing `SESSION_EXPIRED` / `SESSION_LOGOUT` events are already
  emitted; this spec adds `SESSION_REVOKED`,
  `SESSION_OTHERS_REVOKED`, and `ADMIN_SESSION_REVOKED`.
- `app/server/monitor/api/users.py:50` — DELETE-with-`@admin_required`
  + `@csrf_protect` pattern. The new `DELETE /sessions/<id>` and
  `DELETE /sessions/others` routes follow this shape verbatim.
- `app/server/monitor/templates/settings.html:531` — table-with-per-
  row-action pattern (Alpine.js `<template x-for>` + `@click` handler
  + admin-gated visibility). The Sessions table is a copy of this
  layout under a new `'sessions'` tab key.
- `app/server/tests/conftest.py:55` — `data_dir` fixture +
  `users_json` (line 196) seed pattern. New fixture
  `sessions_json` follows the same shape so tests can pre-populate
  a known set of sessions.
- ADR-0011 (`docs/history/adr/0011-auth-hardening.md`) — already
  proposes a server-side token-hash store for "remember-me" with
  `Secure; HttpOnly; SameSite=Strict` cookie flags and `Regenerate
  session ID on login`. This spec satisfies the "session ID stored
  server-side for revocation" half of that ADR even before
  remember-me ships, so any future remember-me rides on the same
  store. AC-15 pins forward-compat.
- ADR-0002 (`docs/history/adr/0002-json-file-storage.md`) — JSON
  files on `/data` are the persistence pattern; no new database.

## User-Facing Behavior

### Primary path — view your active sessions

1. User opens Settings → **Security** (new tab inside the existing
   Settings page; visible to every authenticated user).
2. Page loads `GET /api/v1/sessions`. The response body is a list,
   one row per active session for *this user*. Each row carries:
   - `created_at` (ISO-8601 in user-locale display)
   - `last_active` (relative — "3 minutes ago", "yesterday")
   - `source_ip` (IPv4 / IPv6 string, no geo lookup)
   - `user_agent_parsed` (`{browser: "Firefox 134", os: "Windows
     11"}`) — derived server-side from the raw `User-Agent` header
     using the existing parser (see Open Questions OQ-1)
   - `is_current` (boolean) — true on the row representing the
     browser rendering the page, derived by comparing the response's
     own session id to the row's id. Server stamps this; clients
     do not compute it.
3. The "this session" row is rendered first, with a "(this device)"
   badge. Other rows follow, sorted by `last_active` descending.
4. Each non-current row has a **Sign out** button. The current-
   session row has no Sign out button (use the existing top-right
   logout for that — keeps the "log out the device you are using
   right now" flow untouched).
5. Below the table, a **Sign out other devices** button — disabled
   when the only row is the current session. Confirmation dialog
   ("Sign out N other sessions?") prevents misclicks.

### Primary path — admin sees all sessions

Admin users see an additional toggle on the Security tab: **All
users (admin)**. With it on, the table shows every session across
every user, with an extra `username` column and the "user" column
sortable. Each non-self row carries a Sign out button. The "Sign
out other devices" button still scopes to the *admin's own*
sessions; bulk-revoke for another user is intentionally out of scope
(see Non-Goals — it amplifies the consequences of an admin account
compromise; per-row revoke is the supported tool).

### Primary path — revoke one session

1. User clicks **Sign out** on a row.
2. Browser calls `DELETE /api/v1/sessions/<session_id>` with the
   CSRF token (existing `@csrf_protect` decorator).
3. Server validates the caller owns that session (or is an admin)
   per AC-7, removes the record from the store, emits
   `SESSION_REVOKED` to the audit log with `target_user`,
   `target_session_id_prefix` (first 8 chars only — see SEC-246-A),
   `actor_user`, and `actor_ip`.
4. UI optimistically removes the row; a 5xx response restores it
   with a toast.
5. The targeted browser, on its next request, gets a `401
   Authentication required` from `login_required` /
   `admin_required` (because the server-side lookup now returns
   "not found"). The frontend interceptor that already handles 401
   on stale-session redirects to `/login` and shows "Your session
   was ended on another device."

### Primary path — sign out every other device

1. User clicks **Sign out other devices**.
2. Confirmation dialog. On confirm, browser calls `DELETE
   /api/v1/sessions/others`.
3. Server enumerates the caller's sessions, removes every record
   *except* the caller's own session id, returns
   `{revoked_count: N}`.
4. Audit log gets one `SESSION_OTHERS_REVOKED` event with
   `revoked_count`. Per-session `SESSION_REVOKED` events are not
   emitted in addition (see HAZ-246-3 / RC-246-3 — log flooding).
5. The current browser stays signed in. All other tabs / devices
   for this user get 401 on their next request.

### Failure states

- **`sessions.json` missing on first run** (fresh install or after
  factory reset) → `Store.get_sessions()` returns `[]`; the next
  successful login creates the file. No bootstrap script needed.
- **`sessions.json` corrupt** (hand-edited / partial write recovered
  poorly) → existing pattern in `Store._read_json` returns `[]` on
  `JSONDecodeError`; this is fail-closed for revocation
  (every session looks revoked) which logs every active user out.
  Better than fail-open in a security primitive — documented in
  HAZ-246-7 with audit emission so an admin notices.
- **Disk full at session-write time** → atomic rename fails;
  login responds 500 with `audit:LOGIN_FAILED detail="session
  store write failed"`. Existing rate-limit + lockout do **not**
  fire (this is an infra fault, not a credential fault). See
  HAZ-246-8.
- **Admin revokes their own session by mistake** → the next request
  is 401, they re-login, no data loss. Expected.
- **Two browser tabs revoke each other in the same second** →
  store-level `_lock` serialises; whoever lost the race gets a 404
  on their second DELETE. Idempotent: the response shape is the
  same as a successful DELETE for a session that no longer exists,
  so the UI doesn't surface a confusing "already gone" error.
- **Session id collision** → 32 random bytes via `secrets.token_
  urlsafe(32)` makes this astronomically unlikely; if `Store.save_
  session` ever sees a duplicate id it returns 500 and the login
  retries with a fresh id (one extra round-trip; acceptable; see
  HAZ-246-6 / RC-246-6).
- **Existing signed-cookie session at deploy day** → handled by the
  dual-interface compatibility window (AC-14); the legacy session
  carries no server-side record so it appears in the table as a
  read-only "Legacy session — sign out to upgrade" row that, when
  revoked, simply clears the cookie name on the next request.
- **User-agent header missing or malicious** → the parser tolerates
  empty strings ("Unknown browser" / "Unknown OS"); UA bytes are
  HTML-escaped on render, never `innerHTML`-d, so a forged UA can't
  inject script (see SEC-246-B).
- **Idle session reaches the 60-minute timeout** → server sweeps it
  on the next read and emits `SESSION_EXPIRED`. The Sessions UI
  refresh (or the next page load) reflects the removal.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: A successful login creates a record in `sessions.json` with
  a server-generated opaque id (32 random URL-safe bytes), the user
  id and role, `created_at`, `last_active`, `source_ip` (request
  remote_addr), the raw `User-Agent` header (truncated to a fixed
  cap, see AC-12), and `expires_at` (created_at + 24 h absolute).
  The cookie body is the session id, not the user id; cookie flags
  remain `Secure; HttpOnly; SameSite=Strict`.
  **[unit on the new SessionInterface; integration on
  `POST /auth/login`]**
- AC-2: `GET /api/v1/sessions` returns the caller's own session
  rows (admin or viewer) sorted by `last_active` descending, each
  row tagged with `is_current: true|false`.
  **[contract test]**
- AC-3: An admin caller may set `?scope=all` on
  `GET /api/v1/sessions` and receive every active session across
  every user, with an additional `username` field per row. A non-
  admin who passes `?scope=all` receives the same shape as the
  default (own-only); the param is silently ignored, not 403'd
  (avoid leaking the existence of the admin scope).
  **[contract test for both roles]**
- AC-4: `DELETE /api/v1/sessions/<id>` removes the record and
  emits `SESSION_REVOKED` with `actor_user`, `actor_ip`,
  `target_user`, and the first eight characters of the target id
  (per SEC-246-A). The next request bearing the revoked cookie
  returns 401 from `login_required` / `admin_required`.
  **[unit + integration]**
- AC-5: A non-admin caller cannot revoke a session belonging to
  another user — `DELETE /sessions/<id>` returns 404 (not 403; see
  SEC-246-C, do not leak the existence of the id) and emits no
  audit event.
  **[integration with two seeded users]**
- AC-6: An admin caller can revoke any session, emitting
  `ADMIN_SESSION_REVOKED` (distinct from `SESSION_REVOKED`) so the
  audit trail can distinguish self-revoke from admin-force-out.
  **[integration]**
- AC-7: `DELETE /api/v1/sessions/others` removes every record for
  the caller *except* the caller's own session id, returns
  `{revoked_count: N}`, and emits one `SESSION_OTHERS_REVOKED`
  event with `revoked_count` and `actor_user`. No per-session
  `SESSION_REVOKED` rows are emitted (avoids audit-log flooding).
  **[integration with three sessions for one user]**
- AC-8: The 60-minute idle and 24-hour absolute timeouts already
  in `_is_session_valid()` (auth.py:129) continue to apply
  unchanged. An idle-expired session is swept on the next read
  and an `SESSION_EXPIRED` event is emitted exactly once per
  expiry (not on every read).
  **[unit with frozen time]**
- AC-9: The CSRF token continues to be regenerated on login and
  must accompany every revoke / bulk-revoke call (existing
  `@csrf_protect` decorator). A revoke without the token returns
  403.
  **[integration]**
- AC-10: Login regenerates the session id (ADR-0011 § "Regenerate
  session ID on login"). A pre-login id, if presented after
  login, looks up to no record and is treated as anonymous.
  **[unit asserting pre-/post-login ids differ]**
- AC-11: `last_active` is bumped on every successful authenticated
  request (login_required / admin_required / auth_check) and the
  bump is persisted to `sessions.json` via the in-process cache
  flush policy (see RISK-246-3). The Sessions UI shows the bumped
  value within ≤ 60 s of the request.
  **[unit on the cache; integration with two requests 30 s apart]**
- AC-12: The raw `User-Agent` header is truncated to 512 bytes
  before storage. The parsed `{browser, os}` shape uses the
  existing parser (or a stdlib regex if no parser is wired — see
  OQ-1) and never executes parsed strings as code in the UI
  (HTML-escaped on render).
  **[unit fuzz on UA payloads including
  `<script>`-bearing strings]**
- AC-13: `sessions.json` is byte-identical between two consecutive
  reads if no session changed (no spurious `last_active` updates
  for idle sessions; only authenticated requests bump it). This
  prevents the SD card from absorbing a write per HLS segment.
  **[unit asserting no write on a session that received zero
  authenticated requests in the window]**
- AC-14: **Backwards-compat window.** On the deploy day, existing
  Flask signed-cookie sessions remain valid until their natural
  60-min idle / 24-hr absolute expiry. They appear in
  `GET /api/v1/sessions` as `is_legacy: true` rows; `DELETE` on a
  legacy id sets a deny-list cookie (or, simpler, clears the
  cookie on the targeted client by issuing a `Set-Cookie` with an
  immediate expiry on the legacy cookie name when revoked through
  the *current* browser). Once every legacy session has expired
  naturally, the legacy code path is unreferenced and can be
  removed in a follow-up cleanup PR (out of scope here; we ship
  the dual-interface, not the eventual deletion).
  **[integration: deploy with one signed-cookie session and one
  server-side session simultaneously]**
- AC-15: A future "remember me" 30-day token (ADR-0011) lands in
  the same `sessions.json` with `is_remember_me: true` and a
  longer `expires_at`. v1 stores the field with default `false`;
  no UX exists yet but the schema is forward-compatible.
  **[unit on the schema]**
- AC-16: ADR-0011 account lockout (`failed_logins` /
  `locked_until` on the User model) and session revocation are
  independent. Revoking every session of user X does **not** clear
  X's failure counter; locking out X does **not** revoke their
  active sessions (a logged-in attacker stays in until the session
  itself ends). Each surface has its own remediation. The Sessions
  UI shows a "user is currently locked out" hint next to admin-
  scope rows when applicable, but does not couple the controls.
  **[integration: lock out user, verify their existing session
  still authenticates; revoke their session, verify
  `failed_logins` unchanged]**
- AC-17: `auth_check` (nginx auth_request hot path) reads the
  session id, consults the in-process cache, and falls through to
  the store on cache miss. The cache TTL is ≤ idle-timeout
  granularity so a revoked session is rejected within at most that
  window. (See RISK-246-3 for the exact TTL choice; recommendation
  is 10 s, pins the worst-case "revoked but still serving" window
  to single-digit seconds.)
  **[unit on cache TTL; integration measuring HLS segment
  rejection latency post-revoke]**
- AC-18: A revoked session triggers a one-shot `SESSION_REVOKED`
  audit row whose `actor_ip` is the IP of the *revoker*, not the
  IP of the revoked session. AC-4 already requires this; AC-18
  pins it as a regression test row because misreading either IP
  is a forensics-defeating bug.
  **[unit asserting payload]**
- AC-19: Default-empty `sessions.json` (no users have ever logged
  in since this feature shipped) → `GET /api/v1/sessions` returns
  `[]` for every user. No 500, no bootstrap-required.
  **[unit + integration]**
- AC-20: The Settings → Security tab is reachable with one click
  from the existing Settings nav, renders the table with the
  current session pinned to row one, and round-trips through
  `GET /sessions` + `DELETE /sessions/<id>` + `DELETE
  /sessions/others` without a full page reload (consistent with
  Settings → Users from #121).
  **[browser-level smoke + Playwright/manual checklist]**

## Non-Goals

- **Geo-IP enrichment of `source_ip`.** Self-hosted mission says no
  external dependencies; raw IP only. v2 may ship an opt-in offline
  GeoIP DB if community asks for it.
- **Device-name nicknaming** ("My MacBook"). v1 surfaces the
  parsed UA only; the operator can disambiguate by IP + last-active
  + browser. A future settings field per-session for a friendly
  name is non-controversial; not in scope.
- **Push notification on a new session being created** (e.g.,
  "you logged in from a new IP"). Builds on outbound webhooks
  (#239) — separate spec.
- **Re-authentication grace window** before revoking the *current*
  session. Any session can revoke itself — that's a feature
  (lost-laptop-but-can-borrow-a-friend's flow). Re-auth-before-
  destructive is a different policy (e.g., for password change)
  and is not coupled here.
- **Mass-revoke for a different user** from the admin Security
  tab. v1 is per-row revoke for cross-user actions to avoid
  amplifying the consequences of an admin account compromise. An
  admin who needs to log a user out completely can iterate the
  rows; if this becomes painful in practice (it won't, single-
  household appliance) we add it in v2 with a confirmation
  modal.
- **Cross-server session replication** (HA-style). The product is
  single-server self-hosted; ADR-0006 modular monolith is
  authoritative. No coordination required.
- **Migration script for the legacy signed-cookie sessions.** They
  expire naturally within 24 hours and the dual-interface drops
  out of the request path the day after. We do not write a
  migration tool; we ship the compatibility window.
- **Revocation of CSRF tokens independently.** CSRF tokens live
  inside the session — when the session is revoked, the token
  goes with it. No separate revocation surface.
- **Active-session count rate limiting** ("at most N concurrent
  sessions per user"). Not in scope — would surface a different
  failure mode (silent oldest-session eviction); operator might
  not know which device just got kicked off. Add later if
  abuse pattern emerges.
- **Session-bound MFA step-up** (re-prompt for TOTP before high-
  value actions). Coupled to #238 (TOTP); separate spec.

## Module / File Impact List

**New code:**

- `app/server/monitor/services/session_service.py` — service-layer
  module owning the lifecycle. Public surface:
  - `issue(user, request) -> Session` — generates id, captures
    IP / UA, persists, regenerates the cookie. Called by
    `auth.py` login.
  - `list_for_user(user_id, *, include_legacy=True) -> list[Session]`
  - `list_all() -> list[Session]` — admin-scope.
  - `revoke(session_id, *, actor) -> bool` — owner-or-admin
    enforced inside the service so route handlers stay thin.
  - `revoke_others(user_id, *, except_session_id) -> int`
  - `touch(session_id) -> None` — bumps `last_active` (write-
    behind cache; see RISK-246-3).
  - `sweep_expired() -> int` — sweep-on-read, returns count
    swept; emits `SESSION_EXPIRED` per id.
  - `get(session_id) -> Session | None` — primary lookup for
    `_is_session_valid()` replacement.
- `app/server/monitor/services/server_session_interface.py` — a
  custom Flask `SessionInterface` (subclass of
  `SecureCookieSessionInterface` for the legacy fallback
  shape). Reads the session id from a cookie named
  `rpihm_session` (or whatever the existing app cookie name
  is — see OQ-2), looks the record up in `session_service`,
  exposes a Flask `session` proxy that writes back to the
  store on response. Falls through to the legacy signed-
  cookie shape for cookies bearing the legacy name during
  the compatibility window.
- `app/server/monitor/api/sessions.py` — Blueprint mounted at
  `/api/v1/sessions`. Routes: `GET ""`, `DELETE "/<id>"`,
  `DELETE "/others"`. All require `@login_required` and
  `@csrf_protect`; `GET ?scope=all` is admin-gated inside the
  route (not `@admin_required`, because viewers may call the
  endpoint with the default scope).
- `app/server/monitor/templates/_settings_security_tab.html`
  (or inline section in `settings.html`) — the tab. Reuses the
  user-table layout pattern at settings.html:531.
- `app/server/tests/unit/test_session_service.py` — issue,
  list, revoke, revoke_others, sweep, owner-vs-admin
  enforcement, idempotent double-revoke.
- `app/server/tests/unit/test_server_session_interface.py` —
  cookie issuance, legacy fallback, session-fixation regen on
  login.
- `app/server/tests/unit/test_session_cache.py` — write-behind
  TTL on `last_active`, cache-vs-store consistency on
  revocation.
- `app/server/tests/integration/test_sessions_api.py` —
  end-to-end through the three new routes; covers AC-2 through
  AC-7 + AC-19.
- `app/server/tests/integration/test_session_revocation_e2e.py`
  — login, revoke, next request returns 401; admin force-out;
  legacy-session compatibility window; lockout independence.

**Modified code:**

- `app/server/monitor/auth.py`:
  - Login (line 251): replace direct `session[...] = ...`
    population with `session_service.issue(user, request)`. The
    Flask `session` proxy still works the same from the request
    handler's perspective; the new SessionInterface routes the
    writes to the store.
  - `_is_session_valid()` (line 129): replace with a thin
    wrapper that calls `session_service.get(session_id)` and
    checks `last_active`/`expires_at`. Behaviour
    indistinguishable from current.
  - `auth_check` (line 237): same path; explicitly cache-aware
    (AC-17, RISK-246-3).
  - `logout` (line 349): call `session_service.revoke(self_id,
    actor=self_user)` instead of just `session.clear()`. Audit
    event `SESSION_LOGOUT` (existing) continues to fire.
  - Login id regeneration (AC-10): explicit `session.clear()`
    + new id generation already happens implicitly when
    `session_service.issue` runs; the spec just pins it as a
    test-enforced invariant.
- `app/server/monitor/store.py`:
  - Add `Session` dataclass loader / writer pair: `get_sessions`,
    `save_session`, `delete_session`, `delete_sessions_for_user`.
    Uses the same `_filter_known` migration-tolerant pattern.
- `app/server/monitor/models.py`:
  - New `Session` dataclass: `id: str`, `user_id: str`,
    `username: str`, `role: str`, `created_at: float`,
    `last_active: float`, `expires_at: float`, `source_ip: str`,
    `user_agent: str` (truncated 512), `is_remember_me: bool =
    False`, `must_change_password: bool = False`.
- `app/server/monitor/services/audit.py`:
  - Document `SESSION_REVOKED`, `SESSION_OTHERS_REVOKED`,
    `ADMIN_SESSION_REVOKED` in the event-name docstring catalogue.
    Code path is unchanged — `log_event` is generic.
- `app/server/monitor/templates/settings.html`:
  - New `'security'` tab key (between Users and Audit per the
    existing nav). Table mirrors the Users-tab markup pattern at
    line 531.
- `app/server/monitor/static/js/settings.js`:
  - New Alpine state slice for the Sessions tab (load, revoke,
    revoke_others, refresh-on-focus). The "is_current" badge
    rendering keys off `is_current` from the response, never
    derived client-side from cookies.
- `app/server/monitor/__init__.py` (app factory):
  - Wire the new SessionInterface as `app.session_interface =
    ServerSessionInterface(...)`. Order: replace Flask's default,
    keep secret key intact for the legacy fallback shape.
  - Register `sessions_bp` Blueprint at `/api/v1/sessions`.
- `app/server/tests/conftest.py`:
  - New `sessions_json` fixture seeding a known three-session
    inventory; new `authed_session` fixture that issues a real
    session via the server-side store rather than the legacy
    signed cookie.
- `docs/history/adr/0011-auth-hardening.md`:
  - Add a one-paragraph "Follow-up: server-side session
    enumeration shipped in spec 246-active-sessions-ui.md"
    note. ADR status stays as-is; no counter-decision.

**Out of scope of this spec (touched only if a clean import
demands it):**

- `app/server/monitor/services/user_service.py`: only if the
  delete-user path needs to also nuke that user's sessions
  (recommended; otherwise a deleted-user's stale sessions
  loiter until 24 h expiry). Implementer's call: a one-line
  `session_service.delete_sessions_for_user(user.id)` on the
  delete path is correct; if it churns too many tests, defer
  to a follow-up.

**Dependencies:**

- No new external Python deps. `secrets`, `time`,
  `dataclasses`, `json` — all stdlib. UA parsing uses a stdlib
  regex unless the project already imports a parser somewhere
  (see OQ-1).

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v --cov-fail-under=85`, `ruff check .`, `ruff format --check .` |
| Security-sensitive path | full server suite + the new integration suite + smoke; `python tools/traceability/check_traceability.py` |
| API contract | `/api/v1/sessions` GET/DELETE × scope and ownership matrix; `/api/v1/auth/login` cookie-shape regression |
| Frontend / templates | browser-level check on Settings → Security: list, revoke one, revoke others, current-row badge |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Hardware behavior | deploy + `scripts/smoke-test.sh` row covering "log in from a second device, revoke from the first, second device gets 401 within ≤ 10 s" |

Smoke-test additions (Implementer wires concretely):

- "Operator logs in from a laptop and a phone; from the laptop
  revokes the phone's session; the phone's next request renders
  the login page within at most cache-TTL seconds."
- "Admin logs in, opens Security with `scope=all`, force-revokes
  a viewer's session; viewer's next request 401s; admin's audit
  log shows `ADMIN_SESSION_REVOKED`."
- "Operator clicks Sign out other devices on a 4-session
  account; only the current session survives; audit log shows
  one `SESSION_OTHERS_REVOKED revoked_count=3` row, not three
  individual `SESSION_REVOKED` rows."
- "Existing pre-deploy signed-cookie session continues to work
  for ≤ 60 idle minutes after the upgrade; appears in the table
  as a legacy row; can be revoked from the table to clear."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-246-1 | Mass-logout at deploy: the new SessionInterface fails to recognise the legacy signed-cookie format and every active user sees 401 the moment the new image boots. Operator + admin both locked out simultaneously → no remote remediation possible until physical password reset. | Critical (operational) | Low–medium | RC-246-1: dual-interface compatibility window (AC-14); legacy signed-cookie path remains a first-class read until natural expiry; integration test seeds one legacy + one server-side cookie and asserts both authenticate. CI gate before any image build. |
| HAZ-246-2 | Self-DoS: an admin clicks Sign out other devices on a single-session account and gets logged out (because the "except current" filter misidentified the current id), losing remote access. | Major (operational) | Low | RC-246-2: server stamps `is_current` on every row from the *server-side* session id (not from a header / IP heuristic that a proxy could rewrite); "Sign out other devices" filters by that same server-stamped id; integration test seeds a single session and asserts revoke_others returns `revoked_count=0` and the session still authenticates. |
| HAZ-246-3 | I/O storm: `auth_check` runs on every HLS / WebRTC segment; reading `sessions.json` per call saturates SD-card I/O on a multi-camera deploy. | Moderate (operational) | High (without control) | RC-246-3: in-process read-through cache keyed by session id with a short TTL (recommendation: 10 s, capped by idle-timeout granularity). Cache is a dict guarded by the existing store lock; invalidated on any revoke / sweep / login. AC-17 pins; perf test in the smoke set. |
| HAZ-246-4 | Stale-revocation window: a revoked session continues to serve segments for up to one cache-TTL because the cache hasn't expired. An attacker who already has the cookie keeps watching for that interval. | Moderate (security) | Low | RC-246-4: revoke path explicitly invalidates the cache for that id immediately (synchronous); cache-TTL is the *miss* TTL, not a refresh interval. Worst-case lag is one in-flight request that already passed cache lookup. AC-17 measures it. |
| HAZ-246-5 | Information disclosure: `source_ip` of every session is visible to admins; for a multi-user household this exposes one user's location patterns to the admin (the admin role is also the household admin in single-user installs but not necessarily so). | Minor (privacy) | Medium | RC-246-5: documented in the user-facing docs ("admin can see source IPs of all users' sessions"); the operator chooses who gets admin role. SEC-246-D pins the disclosure scope. |
| HAZ-246-6 | Session id collision: `secrets.token_urlsafe(32)` produces a duplicate id and the new login overwrites a victim's record, hijacking the victim's session for the new user. | Critical (security) | Astronomically low | RC-246-6: `Store.save_session` is a check-then-insert under the store lock; a duplicate id raises and login retries with a fresh id (AC-1 covers issuance, this is an internal invariant). 32-byte randomness is 256 bits of entropy; collision probability ≈ 2⁻¹²⁸ at one billion live sessions. Sufficient. |
| HAZ-246-7 | Corrupt `sessions.json` → fail-closed mass logout (every session looks revoked). Less bad than fail-open (would let stale ids in) but still a Critical operational impact. | Critical (operational) | Low | RC-246-7: atomic rename writes (existing pattern) prevent partial-write corruption in the normal path; on every read, a JSONDecodeError emits a `SESSION_STORE_CORRUPT` audit event so an admin notices instead of silently re-logging in everyone. Documented. Recovery: delete the file and let everyone re-login (existing factory-reset playbook covers this shape). |
| HAZ-246-8 | Disk-full at session-write time → login 500 → operator can't log in even with correct credentials. | Moderate (operational) | Low | RC-246-8: storage-low alert (existing #r1-storage-retention-alerts.md) already fires before disk full in normal operation; login-write failure emits a distinct audit event. The operator can clear space via the storage UI without logging in (the storage UI doesn't gate on disk-write — it reads). |
| HAZ-246-9 | Audit-log flood from `SESSION_OTHERS_REVOKED` on a noisy account (1000s of stale sessions accumulated over a year): one click writes 1000 audit rows. | Minor (operational) | Low | RC-246-9: AC-7 emits one `SESSION_OTHERS_REVOKED` row with `revoked_count=N`, not N individual `SESSION_REVOKED` rows. Audit growth bounded to one row per bulk action. |
| HAZ-246-10 | Cookie-flag regression: the new SessionInterface accidentally drops `Secure`, `HttpOnly`, or `SameSite=Strict`, opening the session cookie to JS / mixed-content theft. | Critical (security) | Low | RC-246-10: regression test asserts the response `Set-Cookie` header carries all three flags on every login + every CSRF-rotation response (AC-1 covers; the test reads the raw header). |

Reference `docs/risk/` for the existing architecture risk register;
this spec adds rows for HAZ-246-1 through -10.

## Security

Threat-model deltas (Implementer fills concrete `THREAT-` /
`SC-` IDs in the traceability matrix):

- **Sensitive path touched: `**/auth/**`.** This spec rewrites the
  Flask SessionInterface and the login session-creation path. Per
  the architect-role guidance, this requires extra design scrutiny;
  the design has been written to:
  - preserve the cookie flags `Secure; HttpOnly; SameSite=Strict`
    (AC-1, HAZ-246-10),
  - regenerate the session id on login (AC-10, ADR-0011),
  - keep CSRF tokens session-scoped (AC-9), and
  - keep the failed-login lockout independent of session
    revocation (AC-16).
- **No new external surface.** All three new endpoints
  (`GET /sessions`, `DELETE /sessions/<id>`, `DELETE
  /sessions/others`) are mounted under the existing
  `@login_required` + `@csrf_protect` posture and inherit the
  rate-limit + lockout protections by virtue of being only
  reachable post-login.
- **No new persisted secret material.** Session ids are
  high-entropy random tokens but are not secrets in the
  ADR-0011-pepper sense — they are bearer tokens, the same
  semantic as today's signed-cookie session, just stored
  server-side now so they can be revoked. They live in
  `/data/config/sessions.json` (mode 0600 already enforced on
  `/data/config` per ADR-0010 LUKS encryption).
- **SEC-246-A — audit information disclosure scope (id
  truncation).** `SESSION_REVOKED` payloads include only the
  first 8 characters of the target session id, never the full
  bearer token. Reason: full ids in the audit log = full bearer
  tokens in any admin's audit-export download. The first 8 chars
  are enough to disambiguate human-readable rows in the UI but
  are too short to guess (and the corresponding session is dead
  by the time the row is logged anyway). AC-4 and AC-18 pin
  this.
- **SEC-246-B — UA-rendered XSS surface.** The browser displays
  the parsed `User-Agent` + `source_ip` of each session. The
  raw UA is attacker-controlled (any logged-in client sets
  it). The renderer must `textContent`-set, never
  `innerHTML`-set; the parser must not return HTML. AC-12 fuzz
  test pins it.
- **SEC-246-C — non-existent-id discrimination.** Revoking a
  session id that does not exist returns 404. Revoking a
  session that exists but belongs to another user (and the
  caller is not admin) **also** returns 404 (not 403). Reason:
  a 403 confirms the id exists and belongs to someone, which
  is an oracle for enumerating session ids. AC-5 pins.
- **SEC-246-D — admin-sees-all source-IP disclosure.** The
  Sessions tab in admin scope shows other users' session
  source IPs. Documented in the user-facing docs and in
  HAZ-246-5. The admin role is privileged by design (ADR-0011);
  IP visibility is consistent with the admin's other powers
  (delete user, force password reset).
- **SEC-246-E — defence-in-depth: revocation-vs-cache TTL.**
  The cache invalidation on revoke is *synchronous* (the
  revoke path holds the lock long enough to evict). A
  revoked-but-still-cached id is impossible by construction;
  the only window is one in-flight request that already
  finished cache lookup before the revoke. RC-246-4 / AC-17
  pin.
- **SEC-246-F — defence-in-depth: factory-reset compatibility.**
  Factory reset (per ADR-0010) wipes `/data`, including
  `sessions.json`. After factory reset every session looks
  revoked, every cookie is rejected. This is the correct
  behaviour. The existing factory_reset_service does not need
  to know about sessions specifically; it gets the right
  behaviour for free.
- **Sensitive paths NOT touched:** `**/secrets/**`,
  `**/.github/workflows/**`, pairing / OTA / certificate flow
  code. The spec is contained to `app/server/`.
- **Default-deny preserved:** a request without a recognised
  session id (or with an id that has no record) goes through
  the existing 401 path. Adding the server-side index does not
  change the default-deny posture; it adds a positive-list
  enforcement on top of the existing signed-cookie negative
  check.
- **TOTP forward-compat:** when #238 (TOTP) lands, the
  `Session` dataclass gains a `totp_verified_at: float` field
  with a default of 0; the login path sets it after the TOTP
  step succeeds. No coupling required in this spec.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-246` — User need: "I want to see every device logged into
  my account and force any of them off without re-installing the
  whole appliance."
- `SYS-246` — System requirement: "The system shall maintain a
  server-side index of authenticated sessions, expose a UI for
  the owning user (and any admin) to enumerate them, and provide
  per-session and bulk revocation primitives that take effect
  within at most one cache-TTL on every authenticated request
  path."
- `SWR-246-A` — Software requirement: server-side `Session`
  record with the schema in AC-1.
- `SWR-246-B` — Software requirement: custom Flask
  SessionInterface that reads/writes the server-side record and
  preserves `Secure; HttpOnly; SameSite=Strict` cookie flags.
- `SWR-246-C` — Software requirement: API surface for own + admin
  enumeration and revocation (per AC-2 through AC-7).
- `SWR-246-D` — Software requirement: Settings → Security tab
  with the listed UX (per AC-20).
- `SWR-246-E` — Software requirement: dual-interface
  compatibility window (AC-14).
- `SWR-246-F` — Software requirement: idempotent revocation, audit
  emission, no log flooding (AC-4, AC-7, HAZ-246-9).
- `SWR-246-G` — Software requirement: in-process cache for
  hot-path validation (AC-17, RISK-246-3).
- `SWA-246` — Software architecture item: "`session_service`
  (service layer per ADR-0003) owns the lifecycle; routes are
  thin under `api/sessions.py`; persistence rides `Store`
  (ADR-0002) into `/data/config/sessions.json`; new SessionInter
  face on the Flask app factory."
- `HAZ-246-1` … `HAZ-246-10` — listed above.
- `RISK-246-1` … `RISK-246-10` — one per hazard.
- `RC-246-1` … `RC-246-10` — one per risk control.
- `SEC-246-A` (audit id-truncation), `SEC-246-B` (UA XSS escape),
  `SEC-246-C` (404-vs-403 enumeration oracle), `SEC-246-D`
  (admin-IP disclosure), `SEC-246-E` (synchronous cache
  invalidation), `SEC-246-F` (factory-reset compatibility).
- `THREAT-246-1` (stolen session cookie — revocation is the
  recovery), `THREAT-246-2` (legacy-cookie regression at deploy
  → mass logout), `THREAT-246-3` (audit-log info disclosure of
  schedule / IP / id material), `THREAT-246-4` (UA injection /
  XSS in the table renderer), `THREAT-246-5` (session-id
  enumeration via 403-vs-404 oracle).
- `SC-246-1` … `SC-246-N` — controls mapping to the threats above.
- `TC-246-AC-1` … `TC-246-AC-20` — one test case per acceptance
  criterion above.

Code-annotation examples (Implementer adds these):

```python
# REQ: SWR-246-A, SWR-246-B; RISK: RISK-246-1, RISK-246-10;
# SEC: SC-246-1; TEST: TC-246-AC-1
class ServerSessionInterface(SecureCookieSessionInterface):
    ...
```

```python
# REQ: SWR-246-C, SWR-246-F; RISK: RISK-246-9; SEC: SC-246-A;
# TEST: TC-246-AC-7
def revoke_others(self, user_id: str, *, except_session_id: str) -> int:
    ...
```

## Deployment Impact

- Yocto rebuild needed: **no** (no new external dependencies; the
  Python stdlib alone covers `secrets`, `dataclasses`, `json`).
- OTA path: standard server-image OTA. On first request after
  the new image boots:
  - Existing client cookies (legacy signed-cookie sessions) keep
    working until natural 60-min idle / 24-hr absolute expiry —
    the dual SessionInterface accepts them (AC-14).
  - The next successful login of every user transitions that
    user to the new server-side session shape.
  - Within 24 hours of the OTA, every legacy cookie is gone by
    expiry; the dual-interface code path is unreferenced.
  - A follow-up cleanup PR (out of scope) deletes the legacy
    fallback once telemetry confirms zero hits over a 7-day
    window.
- Hardware verification: yes — required. `scripts/smoke-test.sh`
  gains the four bullets in the smoke-test additions section
  above; the laptop+phone+revoke flow has to be exercised on
  real hardware because the cookie flags + cache-TTL behaviour
  is hard to assert end-to-end in CI alone.
- Default state on upgrade: `sessions.json` does not exist; first
  login creates it; behaviour is byte-identical to today *for the
  remainder of the existing signed-cookie session* and switches
  to server-side on the next login. No operator-visible action
  required at upgrade time.
- Rollback: a downgrade past this image leaves an orphaned
  `sessions.json` on disk (ignored by the older code path) plus
  legacy cookies that the older signed-cookie interface still
  understands. Safe.

## Open Questions

(None of these are blocking; design proceeds. Implementer
captures answers in PR description.)

- OQ-1: User-agent parsing. Stdlib regex (good-enough for
  Firefox/Chrome/Safari/Edge + iOS/Android/Win/macOS/Linux on
  the dominant patterns), or pull a tiny pure-Python parser?
  **Recommendation:** stdlib regex with a fall-back of "Unknown
  browser / Unknown OS" and the raw UA still rendered as a
  tooltip. Keeps the dependency surface minimal; the parsed
  values are display-only, never security-relevant.
- OQ-2: Cookie name. Today Flask uses the default `session`
  name; for the new interface we may want a distinguishable name
  (e.g., `rpihm_sid`) so the dual-interface can route on cookie
  name rather than payload-shape sniffing.
  **Recommendation:** keep the existing cookie name to avoid
  the dual-cookie-during-window confusion; route on payload
  shape (the legacy signed cookie has a JWT-like dotted shape;
  the new id is a 43-char URL-safe random). Add the cookie-name
  switch as an explicit follow-up if the disambiguation becomes
  brittle.
- OQ-3: Cache TTL value. Spec recommends 10 s; AC-17 makes the
  exact number a tunable. Lower = lower stale-revocation
  window, higher I/O. Higher = the opposite.
  **Recommendation:** 10 s. Single-digit-seconds worst-case
  stale window; modest write amplification (one write per 10 s
  per active session) which is well within SD-card budget for
  the design's expected concurrent-user count (≤ 10).
- OQ-4: Should the Sessions UI show the *count* of legacy-shape
  sessions even when `?scope=all` is off (so a viewer knows to
  re-login to upgrade)? Or stay silent because that's an admin
  concern?
  **Recommendation:** show the legacy row to its owning user
  with a "Sign out to upgrade your session" action. It's
  self-service and the viewer is the only one who can fix it.
- OQ-5: Auto-delete sessions of a deleted user? Spec mentions
  this in the Module/File-Impact "out of scope" caveat but
  the right answer is probably yes — orphaned sessions are
  zombie auth tokens.
  **Recommendation:** wire `session_service.delete_sessions_for_
  user(user.id)` into the existing
  `user_service.delete_user` path; one-line addition; integration
  test added.
- OQ-6: Log a UI hint when the operator's *current* session is
  about to time out (≤ 5 min remaining)? The Sessions UI now
  has the data to do this elegantly (it knows `expires_at`);
  silently letting the user lose work mid-edit is the kind of
  thing this whole feature is supposed to prevent.
  **Recommendation:** out of scope for v1 (separate UX
  consideration that touches every page, not just Settings),
  but pin it as a candidate v1.x polish issue.
- OQ-7: Should we emit a *second* audit event when a session is
  *first observed from a new IP* on the same id (e.g., user moves
  from home Wi-Fi to 4G mid-session)? Today the IP is captured
  at issue-time only.
  **Recommendation:** no in v1. Mid-session IP changes are
  legitimate (carrier handoff, VPN toggles); flagging them
  surfaces noise. Re-evaluate when remember-me ships and the
  session lifetime gets long enough that legitimate IP churn
  is rarer than attack churn.

## Implementation Guardrails

- Preserve the service-layer pattern (ADR-0003): the new logic
  lives in `session_service` (with `Session` as a pure
  dataclass); routes under `api/sessions.py` stay thin.
- Preserve the modular monolith (ADR-0006): no new daemon, no
  new threads. Sweeping is read-time, the cache is in-process,
  the SessionInterface runs inside the existing Flask request
  handler.
- `/data` is the only place mutable runtime state lives —
  `sessions.json` rides on the existing `/data/config/`
  partition with the same atomic-write discipline as
  `users.json` (ADR-0002).
- Cookie flags `Secure; HttpOnly; SameSite=Strict` are
  non-negotiable; AC-1 + HAZ-246-10 + integration test pin.
- `_is_session_valid()` semantics (60 min idle, 24 h absolute,
  must_change_password gate) are preserved verbatim — this spec
  changes *where* the session lives, not *how* a session is
  validated.
- ADR-0011 lockout state and session revocation are independent
  surfaces (AC-16). Do not couple them.
- The new SessionInterface MUST NOT regress the
  signed-cookie-session compatibility window (AC-14).
- Tests + docs + ADR follow-up note ship in the same PR as
  code, per `engineering-standards`.
- No backwards-compatibility hacks beyond the AC-14 dual-
  interface; no `// removed` markers; no schema version bump
  (the dataclass-default loader handles forward additions).
