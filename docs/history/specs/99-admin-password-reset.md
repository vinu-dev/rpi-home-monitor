# Feature Spec: Password Reset — Admin Resets Another User's Password (Close-Out of Issue #99)

Tracking issue: #99. Branch: `feature/99-admin-password-reset`.

## Title

Close out the admin-resets-another-user password-reset flow: confirm
all acceptance criteria pass on real hardware, finish the missing
integration / smoke / traceability rows, document why the
self-service "forgot password" reset-code path (Option A in the
issue body) is **deferred** rather than shipped here, and lift the
in-tree TODO markers (`# Issue #99 slice 1`) once the close-out PR
lands.

## Goal

When a user forgets their password on this device, an admin opens
**Settings → Users**, clicks **Reset password** on the user's row,
picks a temporary password (≥ 12 chars), hands it to the user
out-of-band, and is done. On the user's next login they are
immediately gated by the existing `must_change_password` interceptor
(`auth.py:263`) and forced to pick a new password before any other
endpoint becomes reachable. The admin who did the reset never
learns the user's final password; both events
(`PASSWORD_RESET_BY_ADMIN`, `PASSWORD_CHANGED`) land in `/logs`.

The user-visible promise is "if I forget my password, my admin can
get me back in within thirty seconds without seeing my new
password, without SSH, without a factory reset, and the device
keeps a record of the action." That promise is already largely
shipped (see Context below); this spec scopes the close-out PR
that flips the issue to done.

The lost-sole-admin case stays explicitly out of scope: per
`docs/ai/engineering-standards.md` "No Backdoors" and
`docs/guides/admin-recovery.md` Case 2, the only recovery path
when no admin is available is a hardware factory reset. This
spec does not weaken that boundary.

## Why this fits the mission

`docs/ai/mission-and-goals.md` calls out "trustworthy ... feels
like a real product, not a prototype." Today a locked-out user has
no recovery short of factory reset / SSH access — that is the
opposite of "real product." The shipped slice-1 admin-reset flow
closes the common-case recovery gap. `docs/ai/design-standards.md`
("Setup, provisioning, login, status, update, and recovery flows
matter as much as the happy-path dashboard") puts recovery flows
on the same product-quality bar as the dashboard; this spec brings
that bar to the admin-reset surface specifically.

`docs/ai/engineering-standards.md` § "Security: No Backdoors" is
authoritative on what we *won't* build:

- "No documented command, script, or endpoint that bypasses the
  primary auth mechanism."
- "Pre-auth surfaces never disclose internals."
- "Lost-access recovery is a hardware concern."
- "Admin-assisted recovery is fine when audited."

Slice 1 (admin-resets-another-user) maps cleanly onto the
"admin-assisted recovery is fine when audited" carve-out: the
target is force-rotated on next login, both writes hit `/logs`, no
new pre-auth surface is introduced. Slice 2 of the issue body
(Option A "admin-issued reset code, used at the login screen") is
*not* a backdoor either — it is a single-use bearer credential
materially equivalent to a TOTP recovery code — but it adds a new
pre-auth UX path and token store for a problem the operator is
already solving with slice 1, and is therefore deferred (see
Non-Goals).

## Context

What is **already shipped** on `main` and must be preserved by
this spec — not rebuilt:

- `app/server/monitor/services/user_service.py:137` —
  `UserService.change_password` accepts
  `force_change_next_login: bool` and, when an admin calls it
  against another user with the flag set, marks the target's
  `must_change_password = True` and emits
  `PASSWORD_RESET_BY_ADMIN` (line 192) instead of the normal
  `PASSWORD_CHANGED`. Self-resets silently ignore the flag (an
  admin can never loop themselves into a forced-change state).
- `app/server/monitor/services/user_service.py:183` —
  last-admin guard: refuses an admin-reset against a target who
  is the only admin, returning `400 Cannot force-change the only
  admin`. Defensive — under current routing the case is
  unreachable (admin can't admin-reset themselves; admin-reset
  against another admin requires ≥ 2 admins to exist) — but the
  guard is correct and remains in place.
- `app/server/monitor/api/users.py:69` —
  `PUT /api/v1/users/<id>/password` accepts `force_change` in
  the JSON body, threads it into `change_password`, and clears
  the in-session `must_change_password` flag if the caller just
  cleared their own (line 94) so the change-screen drops out of
  the way on the same request.
- `app/server/monitor/auth.py:263` — `_must_change_block()`
  intercepts every `login_required` / `admin_required`
  endpoint with `403 { must_change_password: true }` until the
  flag clears. The allow-list is pinned to
  `users.change_password`, `auth.logout`, `auth.me` — this
  spec must not widen it.
- `app/server/monitor/auth.py:186` — `_begin_user_session`
  carries `must_change_password` into the session dict at
  login so a stale-cookie API client cannot bypass the gate
  by ignoring the login response body.
- `app/server/monitor/templates/settings.html:803` — the **Reset
  password** button on every non-self user row in
  Settings → Users (admin only); hidden on the admin's own row
  (`currentUser && currentUser.id !== u.id`). Out of reach for
  viewers; nothing to do for them.
- `app/server/monitor/templates/settings.html:830` — the
  **Reset password modal** with copy ("you don't need to know
  the final password they pick"), a 12-character minimum on the
  temp password, and disabled Submit until that minimum is hit.
- `app/server/monitor/templates/settings.html:3199` — the
  Alpine handlers `openResetPassword` / `submitResetPassword`
  / `closeResetPassword` calling
  `PUT /api/v1/users/<id>/password` with
  `{new_password, force_change: true}` and surfacing a toast on
  success or failure.
- `app/server/monitor/templates/login.html:56` — the pre-auth
  hint `Contact your administrator if you can't sign in.`
  satisfies AC-5 (issue body's "Login page has a 'Forgot
  password?' hint"). The comment on lines 51–55 already pins
  the no-pre-auth-leak rule that tracks
  `engineering-standards.md` § "Security: No Backdoors". This
  spec must not alter that line in a way that names recovery
  commands, script paths, or internal URLs.
- `app/server/monitor/services/audit.py:8` — the event-name
  catalogue lists `PASSWORD_CHANGED`, `USER_CREATED`,
  `USER_DELETED`, etc. The new event
  `PASSWORD_RESET_BY_ADMIN` is fired but **not yet listed in
  the docstring catalogue** — close-out fix (see AC-7).
- `app/server/monitor/models.py:201` — `User.must_change_password:
  bool = False` exists on the dataclass. No schema change here.
- `docs/guides/admin-recovery.md` — Case 1 already documents
  the slice-1 flow with the exact `PASSWORD_RESET_BY_ADMIN +
  PASSWORD_CHANGED` audit pair. Case 2 documents the
  hardware-reset boundary. This spec preserves both.
- `app/server/tests/unit/test_user_service.py:381` — unit
  coverage for the admin-reset path:
  - `test_force_change_sets_must_change_flag`
  - `test_admin_reset_logs_specific_audit_event`
  - `test_self_reset_silently_ignores_force_change`
  - `test_refuses_to_force_change_the_only_admin`
  - `test_admin_reset_persists_through_save_user_round_trip`
- `app/server/monitor/api/auth_totp.py` (admin reset of a
  target user's 2FA) is the parallel surface used when the
  forgotten-password user *also* has TOTP enrolled. Reset
  password and Reset 2FA are independent buttons in
  Settings → Users; an admin recovering a TOTP-enabled user
  will typically need both. Not new work.

What is **missing** today and is this spec's concrete delivery:

- **Integration test** that round-trips the forced-change gate
  end-to-end: admin reset → target user logs in → target user's
  next request to any non-allow-listed endpoint returns 403
  with `{must_change_password: true}` → target user calls
  `PUT /users/<self>/password` (no `force_change`) → next
  request succeeds. No such test exists in
  `app/server/tests/integration/test_api_users.py` today.
- **Smoke-test row** in `scripts/smoke-test.sh` (or the row
  list it consumes) that exercises the recovery flow on real
  hardware. The unit + integration suites can fake bcrypt /
  CSRF, but the cookie-flag and post-revocation behaviour
  ride on the real Flask runtime; we want one row that
  actually does it.
- **Audit-event catalogue update** in
  `app/server/monitor/services/audit.py` — add
  `PASSWORD_RESET_BY_ADMIN` to the docstring event list
  (line ~26) so the audit-export schema reviewer (#247) sees
  every event name. The event itself is already emitted; this
  is a doc-only fix that prevents downstream confusion.
- **Traceability rows** for `UN-099`, `SYS-099`, `SWR-099`,
  `RISK-099`, `THREAT-099`, `SC-099`, `TC-099-AC-{1..n}`
  populated in `docs/traceability/traceability-matrix.csv`
  with the existing files annotated by IDs.
- **Code annotations**: `user_service.change_password`,
  `api/users.change_password`, the Settings → Users template
  block, and the new integration test all carry the
  appropriate `REQ:` / `RISK:` / `SEC:` / `TEST:` headers per
  `medical-traceability.md`. Today they carry generic SWR-023
  / RISK-011 / SC-011 annotations; the close-out PR adds the
  new `SWR-099` row alongside.
- **Tracking-comment lift**: the in-tree `# Issue #99 slice 1`
  comments at
  `app/server/monitor/api/users.py:75`,
  `app/server/monitor/services/user_service.py:155`,
  `app/server/monitor/templates/settings.html:802` /
  `:830` / `:832` / `:3196` reference the open issue. Once the
  close-out PR lands, those comments stop being TODO markers
  and should be removed (the audit log + admin-recovery.md
  carry the durable record). This is a small cleanup, not a
  semantic change.

ADRs that frame the work:

- ADR-0011 (auth hardening) — bcrypt cost, session timeouts,
  lockout thresholds. Untouched by this spec.
- ADR-0027 (per-user notification preferences) — irrelevant.
- `docs/archive/exec-plans/auth-recovery.md` — existing
  rejected-on-review record for backdoors / sudo scripts /
  pre-auth surface leaks. This spec extends that record by
  documenting the deferral of Option A reset-codes (see
  Non-Goals "Slice 2 — admin-issued reset codes").

## User-Facing Behavior

### Primary path — admin resets a forgotten-password user

1. Admin signs in to the dashboard. Account already has admin
   role.
2. Admin opens **Settings → Users**.
3. Admin clicks **Reset password** on the row of the
   locked-out user. (Button is hidden on the admin's own row;
   admin uses **Settings → Change Password** for self-reset.)
4. The Reset Password modal opens with copy:
   *"Pick a temporary password. \<username\> will be forced to
   change it on their next login — you don't need to know the
   final password they pick."*
5. Admin enters a temporary password ≥ 12 characters (Submit
   stays disabled until that minimum is hit) and clicks
   **Reset & force change**.
6. Browser calls
   `PUT /api/v1/users/<target_id>/password` with the existing
   CSRF header and JSON body
   `{new_password: <temp>, force_change: true}`.
7. Server bcrypt-hashes the new password, sets
   `must_change_password = true`, persists, emits
   `PASSWORD_RESET_BY_ADMIN` to the audit log with
   `actor_user`, `actor_ip`, `target user_id`, and
   `must_change_password=true`. Returns `200 {message: "Password
   updated"}`.
8. UI toasts `Password reset — <username> must change it on
   next login` and refreshes the user list.
9. Admin tells the target user the temporary password
   out-of-band (phone call / SMS / face-to-face). The choice of
   channel is an operator concern; the device does not provide
   one.

### Primary path — target user re-enters with a temp password

1. Target user opens the login page.
2. Target user signs in with `username + temp password`.
3. Login succeeds. The session is created with
   `must_change_password = true` carried into both the cookie
   session and the server-side session row (`auth.py:186`).
   Login response includes
   `must_change_password: true` so the client renders the
   change-password screen instead of `/dashboard`.
4. Every authenticated request the target makes returns
   `403 { must_change_password: true }` from
   `_must_change_block()` (`auth.py:263`) — except
   `users.change_password`, `auth.logout`, and `auth.me`. The
   gate is endpoint-name-keyed, not URL-keyed (rewrites
   can't slip past).
5. Target user picks a new password ≥ 12 chars, calls
   `PUT /api/v1/users/<self_id>/password` with
   `{new_password: <new>}` (no `force_change`).
6. Server clears `must_change_password = false`, emits
   `PASSWORD_CHANGED` to audit, returns 200.
7. Same request also clears the session-level gate
   (`api/users.py:94`) so the target's next request goes
   straight through. No re-login required.
8. Target proceeds to the dashboard.

### Failure states

- **Admin clicks Reset password on the only admin row.**
  Impossible by construction — the button is hidden on the
  admin's own row, and the admin-reset path requires a
  different `requesting_user_id` from the target. The backend
  still rejects with `400 Cannot force-change the only admin`
  if anything ever reaches it (defence in depth).
- **Admin types a temp password < 12 chars.** Submit button
  stays disabled (UI guard). If the API is hit directly, the
  password policy validator (`monitor.password_policy.
  validate_password`) rejects with the existing 400 message;
  no audit event.
- **Target user logs in with the temp password from a fresh
  IP / device.** Same flow — the must-change gate is per-user,
  not per-session. Idle / absolute timeouts are unchanged.
- **Target user has TOTP enabled.** They still need their
  TOTP secret (or a recovery code) to complete login after
  the temp password. If they have lost both, the admin uses
  **Reset 2FA** (existing button on the same row) before or
  after the password reset; the two surfaces are independent.
  Document this in the admin-recovery guide if not already
  pinned (see AC-9).
- **Target user is currently locked out** (failed-logins
  exceeded). Resetting the password does **not** clear the
  lockout counter (`failed_logins`) or `locked_until` —
  those guard against credential-stuffing and are
  independent of "the credential is now different." The
  target waits out the lockout window (max 30 min per
  ADR-0011) and then signs in with the temp password.
  Documented as an Open Question (OQ-3): if operators flag
  this in practice, a follow-up issue can decide whether
  the admin-reset path also clears `failed_logins`. Not in
  scope here.
- **Audit logger unavailable** (e.g. disk full on `/data/logs`
  or the audit thread crashed). `_log_audit` swallows the
  exception (`user_service.py:209`); the password rotation
  still succeeds. Better than refusing the recovery action;
  HAZ-099-3 risk-controls this trade-off.
- **CSRF token missing or stale.** The `@csrf_protect`
  decorator returns 403 before any password rotation.
  Existing behaviour; no change.
- **Two admins hit Reset password on the same target in the
  same second.** Both succeed; `Store.save_user` is atomic
  (ADR-0002 atomic-rename); the second write wins. Both
  audit events fire. The operational risk is one admin
  using the wrong temp password to communicate to the
  user — non-technical; documented in admin-recovery.md
  (see AC-9 "Coordinate before issuing a reset").
- **Target user is mid-session when the admin resets.**
  Their existing session keeps working until idle / absolute
  timeout (this spec does NOT couple admin-reset to session
  revocation — that conflates two surfaces, see AC-8). The
  gate fires on their *next* login, not retroactively. An
  admin who wants to also kick the target off the device
  uses **Settings → Security → Sessions → Sign out** on
  that user's session row (#246, already shipped).

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: An admin calling `PUT /api/v1/users/<other_id>/password`
  with `{new_password: <≥12 chars>, force_change: true}` rotates
  the bcrypt hash, sets `must_change_password = true` on the
  target, and returns 200.
  **[unit on `UserService.change_password`; integration via
  api/users]**
- AC-2: The same call emits exactly one
  `PASSWORD_RESET_BY_ADMIN` audit event whose payload includes
  `actor_user`, `actor_ip`, and the target `user_id` (no
  passwords or hashes). No `PASSWORD_CHANGED` event is also
  emitted (the two events are mutually exclusive — the same
  rotation fires one or the other based on `force_change`
  + admin + target ≠ self, never both).
  **[unit asserting audit payload]**
- AC-3: Self-rotation against `<self_id>` with `force_change:
  true` silently ignores the flag and emits `PASSWORD_CHANGED`
  (not `PASSWORD_RESET_BY_ADMIN`). An admin cannot loop
  themselves into a forced-change state by mistake.
  **[unit; already covered by
  test_self_reset_silently_ignores_force_change]**
- AC-4: An admin attempting an admin-reset against the *only
  admin* on the system gets `400 Cannot force-change the
  only admin` and no rotation occurs. (Defensive — see
  Context.)
  **[unit; already covered]**
- AC-5: After a successful admin reset, on the target user's
  next login (a) the login response body carries
  `must_change_password: true`, (b) the server-side session row
  carries the flag (`auth.py:186`), and (c) every
  `login_required` / `admin_required` endpoint *except* the
  three on the allow-list returns
  `403 { must_change_password: true }` until the target
  rotates their own password.
  **[integration: end-to-end through `POST /auth/login` →
  arbitrary `GET` returns 403 → `PUT /users/<self>/password`
  succeeds → same `GET` returns 200]**
- AC-6: The same `PUT /users/<self>/password` call from AC-5
  clears the session-level gate so the *next* request goes
  through without a re-login (`api/users.py:94`). The cookie
  is unchanged; no session-id rotation is required for this
  transition.
  **[integration: two requests on the same session,
  before-and-after the password change]**
- AC-7: The audit-event docstring catalogue at
  `app/server/monitor/services/audit.py:8` lists
  `PASSWORD_RESET_BY_ADMIN` alongside `USER_CREATED`,
  `USER_DELETED`, `PASSWORD_CHANGED`. (Doc-only fix; runtime
  behaviour unchanged.)
  **[grep test in the audit suite asserting the event name
  is in the docstring; the event is also exported by name
  through the audit-export endpoint #247]**
- AC-8: Admin-reset does **not** revoke the target's
  in-flight authenticated sessions, does **not** clear the
  target's `failed_logins` / `locked_until` lockout state,
  and does **not** alter the target's TOTP enrolment. Each
  surface has its own remediation (Sessions tab, lockout
  expiry, Reset 2FA). Coupling them here would conflate
  recovery with revocation and create surprise.
  **[integration: seed target with one active session +
  failed_logins=3 + totp_enabled=True, perform admin reset,
  assert session still authenticates / failed_logins
  unchanged / totp_enabled unchanged]**
- AC-9: `docs/guides/admin-recovery.md` Case 1 documents
  the recovery flow including (a) the cross-link to **Reset
  2FA** when the target also has TOTP, (b) a one-line
  warning about coordinating before issuing the reset
  (avoid two admins resetting in parallel), and (c) the
  audit-event names operators can grep for. Case 2 (sole
  admin lost) keeps its hardware-factory-reset framing
  unchanged.
  **[doc-link check; manual review during PR]**
- AC-10: The login page (`templates/login.html:56`) carries
  the line *"Contact your administrator if you can't sign
  in."* and **does not** name any recovery command, script
  path, internal URL, SSH procedure, or specific admin
  username. The line is on the pre-auth surface and is the
  only recovery-related text visible there.
  **[unit / template snapshot; security-review checklist
  row]**
- AC-11: The Settings → Users **Reset password** button is
  hidden on the admin's own row (`x-show="currentUser &&
  currentUser.id !== u.id"`) and on every row when the
  current user is a viewer (the entire Users tab is
  admin-gated by `isAdmin`). A viewer who manually navigates
  the Settings template sees neither the tab nor the button.
  **[browser-level smoke / Playwright row + unit on the
  api/users delete-user authorisation pattern that the
  modal mirrors]**
- AC-12: The Reset Password modal does not retain the temp
  password after Cancel or after a successful submit
  (`closeResetPassword` clears `resetTempPassword`). A second
  open of the modal starts with an empty input.
  **[unit on the Alpine state slice; manual]**
- AC-13: The temp password is sent over HTTPS with the
  `Secure; HttpOnly; SameSite=Strict` session cookie
  posture preserved (existing — this spec does not alter
  the cookie flags or the CSRF posture).
  **[regression test asserting Set-Cookie flags on the
  rotation response are unchanged]**
- AC-14: Smoke-test row exercises end-to-end on real
  hardware: "Admin signs in, opens Settings → Users, resets
  user `viewer1`'s password to a known value, signs out;
  signs in as `viewer1` with the temp password; every
  attempted nav off the change-password screen returns 403;
  rotates to a fresh password; lands on the dashboard;
  audit log on `/logs` shows one `PASSWORD_RESET_BY_ADMIN`
  followed by one `PASSWORD_CHANGED`."
  **[`scripts/smoke-test.sh` row addition]**
- AC-15: The traceability matrix
  (`docs/traceability/traceability-matrix.csv`) gains rows
  linking `UN-099 → SYS-099 → SWR-099` to the existing
  code annotations (REQ-stamping `user_service.py:137`,
  `api/users.py:69`, `templates/settings.html:803-861`,
  the new integration tests, and the smoke row). The
  Markdown summary
  (`docs/traceability/traceability-matrix.md`) gets a
  one-line entry under the "Software requirements" cell.
  `python tools/traceability/check_traceability.py` passes.
  **[traceability checker]**
- AC-16: The in-tree `# Issue #99 slice 1` comments are
  removed from
  `app/server/monitor/api/users.py`,
  `app/server/monitor/services/user_service.py`, and the
  five sites in `app/server/monitor/templates/settings.html`.
  The audit log + admin-recovery.md carry the durable
  record; the open-issue marker is no longer correct once
  this PR merges.
  **[grep test in CI: `git grep "Issue #99 slice 1"`
  returns zero hits in the close-out PR diff]**

## Non-Goals

- **Slice 2 — admin-issued one-time reset codes (Option A
  in the issue body).** A second-slice path where the admin
  generates a short-lived single-use code that the user
  presents at the login screen. Rationale for deferring:
  (a) slice 1 already solves the operational problem the
  issue raised — locked-out user has a recovery path that
  takes ≤ 30 s and does not require SSH or factory-reset;
  (b) slice 2 adds a new pre-auth UX path, a token-store
  schema, an expiry sweep, single-use enforcement, and an
  audit-event family for an incremental security gain (admin
  doesn't see the final password — already true in slice 1
  via `must_change_password`); (c) the issue body itself
  flags Option B as "simpler to build first"; (d) every
  added pre-auth surface is a new attack target and we
  prefer to grow that surface only when an unmet need
  forces it. Track as a follow-up issue ("Self-service
  reset codes — admin-issued single-use"); not opened by
  this spec but the close-out PR description should propose
  it.
- **Email-based password-reset links.** The product is
  local-LAN-first; no SMTP server is assumed. Out of scope
  for the device permanently. If a deployment adds an MTA
  via the webhooks path (#239), that is an integration
  concern, not a device feature.
- **SMS / phone-number-based resets.** Same reason; no
  cellular surface on the device.
- **Auto-revocation of the target's existing sessions on
  admin reset.** Couples recovery to revocation; conflicts
  with AC-8. If operators want to also kick the target off,
  they use Settings → Security → Sessions (already
  shipped, #246).
- **Auto-clear of the target's `failed_logins` / `locked_until`
  on admin reset.** Same conflation argument. Lockout is a
  credential-stuffing defence; the credential being changed
  doesn't retroactively make the previous bad attempts
  innocent. (Tracked as OQ-3; can be reconsidered if
  operators report friction.)
- **Auto-disable of the target's TOTP on admin reset.**
  TOTP is a separate factor with its own admin-reset
  surface (Reset 2FA button). A user who has lost both
  password and TOTP gets two admin-reset actions; that is
  correct, not a missing feature.
- **Self-service "forgot password" link from the login page
  pointing to a documented recovery URL or path.** The
  pre-auth surface deliberately stays minimal per
  `engineering-standards.md` § "No Backdoors". The shipped
  line is the maximum the login screen ever says.
- **Bulk admin reset** ("reset every viewer's password
  now"). No legitimate operator workflow needs this; if
  one emerges, it builds on slice 1 via a thin loop, not a
  new endpoint.
- **Schema versioning of `users.json`.** No new fields are
  introduced; `must_change_password` already lives on the
  dataclass.
- **Hardware factory-reset implementation.** Tracked
  separately under the hardware-refresh work; this spec
  does not block on it (the close-out succeeds with the
  current SD-reflash transitional path documented in
  admin-recovery.md Case 2).

## Module / File Impact List

**No new files** required for the close-out (slice 1 is
already implemented). Modifications only.

**Modified code:**

- `app/server/monitor/services/audit.py`:
  - Add `PASSWORD_RESET_BY_ADMIN` to the docstring event
    list near line 26 (sits next to `PASSWORD_CHANGED`).
  - Optional but recommended: export the literal string
    constant `PASSWORD_RESET_BY_ADMIN = "PASSWORD_RESET_BY_ADMIN"`
    near the existing `CLIP_TIMESTAMP_*` constants so
    callers and tests stop using the bare string. Defer if
    it churns too many sites; doc-only fix is sufficient
    for AC-7.
- `app/server/monitor/api/users.py`:
  - Update the function-level annotation header to add
    `SWR-099` alongside the existing `SWR-023`.
  - Remove the `# Issue #99 slice 1` tracking comment in
    the `change_password` route docstring (line ~75).
- `app/server/monitor/services/user_service.py`:
  - Update the `change_password` docstring to drop
    "(issue #99 slice 1)" and replace with "Admin-assisted
    recovery; see `docs/guides/admin-recovery.md`."
  - Add `# REQ: SWR-099, SWR-023; RISK: RISK-099, RISK-011;
    SEC: SC-099, SC-011; TEST: TC-099-AC-1, TC-099-AC-2`
    annotation block above the `change_password` method.
- `app/server/monitor/templates/settings.html`:
  - Remove the five `# Issue #99 slice 1` references in
    the comments (lines 802, 832, 3196, plus the two
    in the modal copy block) — keep the explanatory text
    that doesn't reference the issue number, drop the
    "(issue #99 slice 1)" trailing parenthetical.
  - Update the `{# REQ ... #}` template header at line 1
    to add `SWR-099`, `RISK-099`, `SC-099`, `TC-099-AC-{5,6,11,12}`.

**New tests:**

- `app/server/tests/integration/test_admin_password_reset_e2e.py`
  (new file) — covers AC-5, AC-6, AC-8, AC-13:
  - `test_admin_reset_then_target_login_blocks_until_change`
    (AC-5): admin resets viewer's password; viewer logs in;
    arbitrary `GET /api/v1/cameras` returns 403 with
    `must_change_password: true`; viewer rotates password;
    same `GET` returns 200.
  - `test_post_change_request_unblocks_session_immediately`
    (AC-6): two `GET`s on the same session, before-and-after
    the password change, no re-login.
  - `test_admin_reset_does_not_disturb_lockout_or_session_or_totp`
    (AC-8): seed target with `failed_logins=3`,
    `totp_enabled=True`, an active server-side session;
    perform admin reset; assert session row still resolves,
    `failed_logins` unchanged, `totp_enabled` unchanged.
  - `test_set_cookie_flags_unchanged_on_admin_reset_response`
    (AC-13): assert `Secure`, `HttpOnly`, `SameSite=Strict`
    on the response cookie of the admin-reset call.
- `app/server/tests/integration/test_login_page_pre_auth_surface.py`
  (new file or extend an existing template-rendering test) —
  covers AC-10:
  - `test_login_page_says_only_contact_your_administrator`:
    render `/login`, assert the page contains the exact
    "Contact your administrator if you can't sign in."
    string and contains none of the deny-list strings
    `"sudo"`, `"reset-admin"`, `"SSH"`, `"factory reset"`,
    `"/opt/monitor"`, `"recovery code"`, `"reset code"`.

**Modified docs:**

- `docs/guides/admin-recovery.md` Case 1:
  - Add a one-line note: *"If the user also lost their TOTP
    factor, also click Reset 2FA on their row before they
    log in — the two are independent."*
  - Add a one-line note: *"Coordinate before issuing a
    reset — two admins resetting the same user in parallel
    will leave the user with whichever temp password landed
    second, which is a comms hazard, not a security one."*
  - Add a "What hits the audit log" subsection naming
    `PASSWORD_RESET_BY_ADMIN` and `PASSWORD_CHANGED` and a
    `grep -F` example operators can run on `/logs/audit.log`.
  - Case 2 stays as-is.
- `docs/cybersecurity/threat-model.md`:
  - Add a row for THREAT-099 (admin compromise →
    privilege-escalation by mass-resetting other users)
    with the existing audit-log + admin-rate-limit + mTLS
    controls referenced. No new control implied.
- `docs/traceability/traceability-matrix.md`:
  - One-line entry under the "Software requirements" row
    (already says `SWR-001 through SWR-067`; bump to
    include SWR-099 explicitly).
- `docs/traceability/traceability-matrix.csv`:
  - New rows for `UN-099`, `SYS-099`, `SWR-099`, `RISK-099`,
    `THREAT-099`, `SC-099`, `TC-099-AC-{1..16}` linking
    code, doc, and test artefacts. Concrete IDs picked by
    the implementer to avoid clashing with in-flight work
    (#246 reserves `*-246-*`; #99 is unused).
- `docs/risk/dfmea.md`:
  - Add HAZ-099-{1..3} with severity / probability / RC
    columns matching the rows below.
- `docs/risk/risk-control-verification.md`:
  - Add RC-099-{1..3} verification rows pointing at the
    integration tests above.
- `scripts/smoke-test.sh` (or whichever row list the
  hardware smoke runner consumes):
  - Add the AC-14 row.

**Out of scope of this spec (touch only if a clean import
demands it):**

- `app/server/monitor/templates/login.html` is read-only
  for this spec except for the AC-10 snapshot test that
  asserts existing content — the line stays exactly as
  shipped.
- `app/server/monitor/auth.py` `_must_change_block`
  allow-list — this spec must NOT widen it. Any temptation
  to include another endpoint (e.g. settings reads) is
  out of scope and should be a separate, security-reviewed
  PR.
- `app/server/monitor/api/auth_totp.py` Reset 2FA path —
  unchanged; the spec only cross-links it from
  admin-recovery.md (AC-9).

**Dependencies:**

- No new external Python or JS deps. All stdlib + bcrypt
  (already pulled in by the existing auth path).

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v --cov-fail-under=85`, `ruff check .`, `ruff format --check .` |
| Security-sensitive path (`**/auth/**` indirect via `users.py`/`user_service.py`) | full server suite + the two new integration test files; security-review checklist row covering AC-10 |
| API contract | `PUT /api/v1/users/<id>/password` with `force_change` flag — admin scope, self-scope, only-admin guard, payload shape regression |
| Frontend / templates | browser-level check on Settings → Users: open modal, type < 12 chars (Submit disabled), type ≥ 12, submit, verify toast and table refresh; reload and re-open modal (state cleared per AC-12) |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py`, `python tools/docs/check_doc_map.py` |
| Hardware behavior | `scripts/smoke-test.sh` row from AC-14; deploy + run end-to-end on real hardware with two admins + one viewer seeded |
| Repository governance | `python -m pre_commit run --all-files`, `python scripts/ai/validate_repo_ai_setup.py`, `python scripts/ai/check_doc_links.py`, `python scripts/ai/check_shell_scripts.py`, `python scripts/check_version_consistency.py`, `python scripts/check_versioning_design.py` |

Smoke-test additions (Implementer wires concretely):

- AC-14 row: full slice-1 round-trip on real hardware
  including the `/logs/audit.log` grep for the two
  expected event names.
- Negative row: viewer signs in to Settings, the Users tab
  is **not visible**; Reset password button does not
  exist anywhere in the rendered DOM. (Verifies the
  admin-gating in AC-11 doesn't regress with the
  template-header REQ updates.)

## Risk

ISO 14971-lite framing. Hazards specific to this close-out
PR (the slice-1 hazards already in the existing risk
register stay in place):

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-099-1 | Forced-change-gate regression: a future PR widens `_MUST_CHANGE_ALLOWED_ENDPOINTS` (auth.py:254) and a target user reaches a sensitive surface without rotating. The whole slice-1 promise (admin doesn't know the user's final password) collapses. | Major (security) | Low | RC-099-1: AC-5 integration test exercises the gate against an arbitrary endpoint (`GET /api/v1/cameras`); a regression in the allow-list breaks the test. The test's endpoint choice is deliberately one that a careless allow-list edit might add ("read-only, surely safe?"). |
| HAZ-099-2 | Pre-auth surface leak: a future PR adds a "Forgot password?" link or page to `templates/login.html` that names a recovery command, script path, or internal URL. Anyone on the LAN reads it; anyone who can read the rendered HTML can map the device's recovery surface. | Critical (security) | Low | RC-099-2: AC-10 snapshot test pins the exact pre-auth string and a deny-list of forbidden substrings (`sudo`, `reset-admin`, `SSH`, `factory reset`, `/opt/monitor`, `recovery code`, `reset code`). Any change to the login page touches the test; a security-review-checklist row is the second backstop. |
| HAZ-099-3 | Audit-emission failure (disk full / log thread crashed) silently passes the password rotation: an admin reset happens, the user is force-rotated on next login, but no `PASSWORD_RESET_BY_ADMIN` row exists in `/logs`. Forensics defeated. | Moderate (operational + audit) | Low | RC-099-3: existing storage-low alert (#r1-storage-retention-alerts.md) fires before disk full in normal operation; the audit-export schema (#247) flags missing event types in its summary; this spec adds `PASSWORD_RESET_BY_ADMIN` to the catalogue (AC-7) so its absence is detectable rather than a silent gap. The rotation succeeding even when audit fails is the deliberate trade-off (recovery > forensics under partial failure); documented here. |
| HAZ-099-4 | Admin-confused recovery: two admins independently issue a reset for the same user within a few seconds; the user gets a temp password from one of them but the other admin's password is what landed. The user's first attempt fails; the second admin's audit row is the canonical truth. Operationally annoying, not a security regression. | Minor (operational) | Low | RC-099-4: AC-9 docs add a one-line "Coordinate before issuing a reset" note in admin-recovery.md. The audit log carries both events with timestamps so the operators can untangle which password landed. |
| HAZ-099-5 | TOTP-enrolled user gets reset, doesn't realise they still need TOTP, support-loop ensues. | Minor (operational) | Medium | RC-099-5: AC-9 docs cross-link to Reset 2FA in the same recovery flow. The login page already reaches the TOTP step after the password step, so the user discovers the requirement immediately on retry; the docs just shorten the support cycle. |
| HAZ-099-6 | Slice-2 (Option A reset codes) gets implemented later by a different agent without a security review of the new pre-auth surface. The token store, single-use enforcement, expiry sweep, or rate-limiting could all introduce regressions in `engineering-standards.md` § "No Backdoors". | Major (security) | Low (because deferred-not-rejected, but humans forget) | RC-099-6: this spec's Non-Goals section pins the deferral with rationale; a follow-up issue ("Self-service reset codes — admin-issued single-use") is opened with a label gating it on a fresh architect-role spec. The exec-plans archive entry (`docs/archive/exec-plans/auth-recovery.md`) gets a one-line cross-reference to this spec's Non-Goals so the reasoning survives. |

Reference `docs/risk/` for the existing architecture risk
register; this spec adds rows HAZ-099-1 through HAZ-099-6.

## Security

Threat-model deltas (Implementer fills concrete
`THREAT-` / `SC-` IDs in the traceability matrix):

- **Sensitive paths touched:** `app/server/monitor/api/users.py`,
  `app/server/monitor/services/user_service.py`, indirect on
  `app/server/monitor/auth.py` (the `_must_change_block`
  contract is asserted but not modified). The change is
  narrowly scoped to documentation, traceability, and test
  rows; no production code path is rewritten.
- **No new external surface.** The endpoints exercised
  (`PUT /users/<id>/password`, `POST /auth/login`) are
  already shipped. The integration tests round-trip them
  via the existing test-client fixture.
- **No new persisted secret material.** Temp passwords are
  hashed with bcrypt cost 12 (existing `hash_password`
  helper in `auth.py:49`); plaintext passwords are never
  persisted. The temp password lives in transit (HTTPS),
  in the admin's clipboard / phone briefly, and in the
  user's brain — never on disk in cleartext.
- **SEC-099-A — pre-auth surface lock.** The login page
  carries exactly one recovery-related sentence: "Contact
  your administrator if you can't sign in." It must not
  acquire URLs, command names, script paths, the `admin`
  username, the location of `/data`, the existence of
  `audit.log`, or any other internal detail.
  AC-10 + RC-099-2 pin.
- **SEC-099-B — admin-action audit invariant.** Every
  admin-side state change to another user's auth state
  (password reset, TOTP reset, session revoke, role
  change, delete) emits exactly one named event with
  `actor_user`, `actor_ip`, `target user_id`, and a
  detail string sufficient to reconstruct what happened.
  `PASSWORD_RESET_BY_ADMIN` already complies; AC-2 +
  AC-7 pin its discoverability.
- **SEC-099-C — non-self-reset boundary.** The
  admin-reset path requires `requesting_user_id !=
  user_id`; self-resets follow the normal
  `PASSWORD_CHANGED` flow with no `must_change_password`
  side effect. Already enforced (`user_service.py:163`);
  AC-3 pins it as a regression test row.
- **SEC-099-D — last-admin defence in depth.** The
  back-end refuses to force-change the only admin
  (`user_service.py:183`). The button is hidden on the
  admin's own row in the UI. Dual-layer defence; AC-4 +
  AC-11 pin both.
- **SEC-099-E — admin compromise threat.** A compromised
  admin can mass-reset every other user's password and
  lock the household out of their own device until they
  factory-reset. This is the same threat as a compromised
  admin in any auth system; the audit log + the existing
  per-IP login lockout + the operator's awareness of
  *who has admin* are the controls. THREAT-099 captures
  the residual risk; no new control is introduced
  (the threat is intrinsic to the admin role, not to
  this feature).
- **SEC-099-F — slice-2 deferral as a security choice.**
  Not building Option A reset codes is itself a security
  posture: every deferred pre-auth path is one we can't
  introduce a vulnerability into. AC-9 + Non-Goals make
  the rationale durable so a future agent who is asked
  "why didn't we just ship the reset code?" finds the
  answer in the spec rather than re-deriving it.
- **Sensitive paths NOT touched:** `**/secrets/**`,
  `**/.github/workflows/**`, certificate / TLS / pairing /
  OTA flow code, `app/camera/`. The spec is contained to
  the server's user-management surface.
- **Default-deny preserved:** every endpoint exercised
  goes through `@login_required` + `@admin_required`
  (where applicable) + `@csrf_protect`. The forced-change
  gate is layered on top of those; this spec asserts the
  layering is intact (AC-5).

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.csv`):

- `UN-099` — User need: "When I forget my password on this
  device, my admin must be able to get me back in within
  thirty seconds without seeing my new password and
  without SSH or factory reset."
- `SYS-099` — System requirement: "The system shall
  permit any admin to rotate any non-self user's password
  to a temporary value, mark the target as
  `must_change_password`, force the target to rotate
  again on next login before any other endpoint is
  reachable, and emit a discoverable audit row for both
  the reset and the subsequent change."
- `SWR-099-A` — Software requirement: admin-callable
  password rotation with `force_change=true` payload (per
  AC-1, AC-2).
- `SWR-099-B` — Software requirement: forced-change gate
  semantics on every `login_required` /
  `admin_required` endpoint except the three on the
  allow-list (per AC-5; preserves the existing
  `_must_change_block` contract).
- `SWR-099-C` — Software requirement: in-session gate
  release on self-rotation (per AC-6;
  preserves `api/users.py:94`).
- `SWR-099-D` — Software requirement: independence of
  admin-reset from session revocation, lockout state,
  and TOTP enrolment (per AC-8).
- `SWR-099-E` — Software requirement: pre-auth surface
  carries no recovery internals (per AC-10).
- `SWR-099-F` — Software requirement: admin-only
  Settings → Users surface; viewer cannot see the Reset
  password button (per AC-11).
- `SWA-099` — Software architecture item: `UserService.
  change_password` (service layer per ADR-0003) owns the
  admin-reset semantics; routes under `api/users.py`
  stay thin; persistence rides `Store` (ADR-0002) into
  `/data/config/users.json`; the audit logger emits
  `PASSWORD_RESET_BY_ADMIN`; the
  `_must_change_block` interceptor in `auth.py` enforces
  the post-login gate.
- `HAZ-099-1` … `HAZ-099-6` — listed above.
- `RISK-099-1` … `RISK-099-6` — one per hazard.
- `RC-099-1` … `RC-099-6` — one per risk control.
- `SEC-099-A` (pre-auth lock), `SEC-099-B` (admin-action
  audit invariant), `SEC-099-C` (non-self-reset
  boundary), `SEC-099-D` (last-admin defence in depth),
  `SEC-099-E` (admin compromise threat),
  `SEC-099-F` (slice-2 deferral as posture).
- `THREAT-099-1` (compromised admin mass-rotation),
  `THREAT-099-2` (pre-auth surface leak via future
  "Forgot password?" UX), `THREAT-099-3` (audit emission
  silently dropped under disk-full).
- `SC-099-1` … `SC-099-N` — controls mapping to the
  threats above.
- `TC-099-AC-1` … `TC-099-AC-16` — one test case per
  acceptance criterion above. Many are already covered
  by existing unit tests in `test_user_service.py`; the
  implementer maps existing test names onto the new TC
  IDs rather than re-writing them.

Code-annotation examples (Implementer adds these):

```python
# REQ: SWR-099-A, SWR-023; RISK: RISK-099-1, RISK-011;
# SEC: SC-099-A, SC-011; TEST: TC-099-AC-1, TC-099-AC-2
def change_password(
    self,
    user_id: str,
    new_password: str,
    *,
    force_change_next_login: bool = False,
    ...
) -> tuple[str, int]:
    ...
```

```python
# REQ: SWR-099-B; RISK: RISK-099-1; SEC: SC-099-A;
# TEST: TC-099-AC-5
def _must_change_block() -> bool:
    ...
```

## Deployment Impact

- Yocto rebuild needed: **no** (no new dependencies, no
  recipe / packagegroup edits, no kernel module).
- OTA path: standard server-image OTA. The close-out PR
  ships only docstring / comment / annotation updates, two
  new integration test files, and one smoke-test row;
  there is no behaviour change on the device. Operators on
  older images already enjoy the slice-1 functionality.
- Hardware verification: yes — required for the AC-14
  smoke row. The slice-1 path itself is already deployed,
  so this is a regression-prevention smoke, not a
  first-time validation.
- Default state on upgrade: identical to today. No
  migration. The `must_change_password` field already
  exists on the User dataclass.
- Rollback: trivial. The PR adds tests and docs; rolling
  back leaves them on disk in `/var/lib/...` (unused) and
  the runtime is unchanged.
- Audit-log compatibility: `PASSWORD_RESET_BY_ADMIN` is
  already emitted by deployed devices; the close-out PR
  only adds it to the docstring catalogue and to the
  audit-export (#247) summary. No log-format change.

## Open Questions

(None of these are blocking; design proceeds. Implementer
captures answers in PR description.)

- OQ-1: Should the close-out PR ALSO export
  `PASSWORD_RESET_BY_ADMIN` as a string constant in
  `audit.py` next to the `CLIP_TIMESTAMP_*` constants, or
  leave it as a bare-string literal?
  **Recommendation:** export the constant. It costs four
  lines and removes a stringly-typed footgun, and the
  existing `CLIP_TIMESTAMP_*` constants are precedent. If
  the diff churns more than a handful of test sites,
  defer to a separate "audit constants normalisation"
  follow-up.
- OQ-2: Should the AC-14 smoke row also exercise the
  *failure* case (admin types `< 12` chars; Submit stays
  disabled)? Or is that adequately covered by the unit /
  Playwright row?
  **Recommendation:** unit / DOM check is sufficient. The
  smoke row is hardware-runtime-specific (cookie flags,
  audit log on real `/data`); the disabled-button case
  doesn't need a hardware run.
- OQ-3: Should admin-reset clear the target's
  `failed_logins` / `locked_until` so a user who is
  locked out *and* forgot their password can immediately
  sign in with the temp password? Default: no (independence
  of surfaces, AC-8). Operators can manually clear via the
  user JSON if they really need to, or wait the lockout
  out (max 30 min).
  **Recommendation:** keep independent in v1; revisit if
  the smoke-test flow surfaces friction.
- OQ-4: Should the close-out PR remove the dead `len(admins)
  <= 1` defensive guard in `user_service.py:183`?
  **Recommendation:** **no** — keep it. It is dead under
  current routing but is correct, cheap, and would catch a
  future API exposure that allows admin-resetting yourself.
  Defensive code at security boundaries is a feature.
- OQ-5: Should admin-recovery.md grow a third "Case 1.5"
  for "the admin who needs to reset has TOTP but hasn't
  enrolled it on the device they're using"?
  **Recommendation:** out of scope; that's an
  admin-self-recovery question (different from this
  spec's user-recovery scope), and the existing TOTP
  enrolment + recovery-code flow already addresses it.

## Implementation Guardrails

- Preserve the service-layer pattern (ADR-0003): all
  business logic stays in `UserService.change_password`;
  `api/users.py` remains a thin HTTP adapter.
- Preserve `_must_change_block`'s allow-list discipline
  (`auth.py:254`): only `users.change_password`,
  `auth.logout`, `auth.me` may be reachable while the
  flag is set. Widening it is a separate, security-
  reviewed PR.
- Preserve the cookie posture: `Secure; HttpOnly;
  SameSite=Strict` on every response (AC-13). The
  close-out adds a regression test row but must not
  alter cookie issuance.
- Preserve the pre-auth surface: the login page says
  "Contact your administrator if you can't sign in." and
  no more (AC-10). Any temptation to add a "Forgot
  password?" link is out of scope and *would* be a
  security review by definition.
- Preserve the audit catalogue invariant: every
  admin-side auth-state mutation emits a named event
  (SEC-099-B). New events get added to the docstring
  catalogue at the same time the emitter lands.
- Tests + docs + traceability ship in the same PR as the
  code changes (per `engineering-standards.md` and
  `medical-traceability.md`).
- No new external Python or JavaScript dependencies; the
  spec scope is doc-and-tests-and-cleanup, not feature.
- Slice-2 (Option A reset codes) is **deferred, not
  rejected**; the close-out PR description should propose
  a follow-up issue with a one-paragraph rationale
  pointing at this spec's Non-Goals.
- The in-tree `# Issue #99 slice 1` markers are
  removed in this PR (AC-16). The audit log + the
  `docs/guides/admin-recovery.md` Case 1 entry are the
  durable record.
