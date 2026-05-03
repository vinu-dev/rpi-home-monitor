# Feature Spec: TOTP-Based 2FA For Admin And Remote Users

Tracking issue: #238. Branch: `feature/238-totp-2fa`.

## Title

TOTP-based two-factor authentication for admin and Tailscale-Funnel-remote
users.

## Goal

Restate of issue #238: any user — and admins in particular — can protect
their account with TOTP (RFC 6238) on top of the existing
username/password login. After enrolling from Settings, login requires a
six-digit code from any standard authenticator app (Google Authenticator,
Aegis, 1Password, Bitwarden, …). Recovery codes are issued at enrollment
to handle the lost-phone / SD-card-failure case. Admins can require 2FA
for sessions reaching the system through Tailscale Funnel while leaving
LAN-only sessions unaffected.

This closes the loop ADR-0011 explicitly deferred ("TOTP (Future)") and
matches Home Assistant's well-known UX precedent. It is a clean trust
differentiator vs. Frigate, which is password-only.

## Context

Existing code this feature must build on, not re-implement:

- `app/server/monitor/models.py:139` — `User` dataclass already carries
  `totp_secret: str = ""` (reserved by ADR-0011). Add `totp_enabled` and
  `recovery_code_hashes` here.
- `app/server/monitor/auth.py:251` — `POST /api/v1/auth/login`: rate
  limiting, lockout, session creation, audit logging. The 2FA challenge
  step plugs in *between* password verification and `session.clear() / set
  user_id`.
- `app/server/monitor/services/user_service.py` — service-layer pattern
  per ADR-0003. New TOTP business logic lives here (or a sibling
  `totp_service.py` if `user_service.py` grows past responsibility).
- `app/server/monitor/api/users.py` — thin HTTP adapters that delegate to
  the service. New endpoints land here (or a new auth-scoped blueprint).
- `app/server/monitor/services/audit.py` (`AuditLogger`) — already used
  for `LOGIN_SUCCESS` / `LOGIN_FAILED` / `PASSWORD_CHANGED`. Extend with
  TOTP events.
- `app/server/monitor/templates/login.html`, `settings.html` — the
  challenge step renders here; enrollment lives on settings.
- `app/server/monitor/services/tailscale_service.py` and the Tailscale
  config in `Settings` (`models.py:194`) — the "require 2FA for remote"
  check needs a reliable way to classify a request as Tailscale-Funnel
  vs. LAN. `tailscale_service` exposes the local Tailscale state; the
  request-side classifier is new.
- `app/server/monitor/__init__.py:119` — already runs behind a trusted
  proxy header so `request.remote_addr` is the real client IP. The
  Tailscale-vs-LAN classifier reuses that IP.
- ADR-0011 (`docs/history/adr/0011-auth-hardening.md`) — sets the wider
  auth-hardening shape this feature lives inside.

ADR-0007 keeps default dev credentials; on dev images, 2FA enrollment is
optional (must not block first-boot setup). ADR-0011 already names
`pyotp` as the chosen library.

## User-Facing Behavior

### Primary path — enrollment (any user, from Settings)

1. User opens Settings → Security → Two-factor authentication.
2. Page shows current state ("Off" or "On since 2026-05-03"). If off, an
   "Enable two-factor authentication" button is visible.
3. User clicks Enable. Server provisions a fresh TOTP secret (not yet
   persisted as enabled), renders a QR code (`otpauth://totp/...`) and
   the manual base32 secret string side-by-side.
4. User scans the QR (or types the secret) into their authenticator app.
5. User types the current six-digit code into the confirm box and submits.
6. Server verifies the code against the pending secret. On success:
   - persists `totp_secret` and sets `totp_enabled = true`
   - generates 10 single-use recovery codes (16 alphanumeric chars each,
     formatted in two `xxxx-xxxx-xxxx-xxxx` groups for readability)
   - stores **only bcrypt hashes** of the recovery codes; plaintext codes
     are returned exactly once on this response
   - shows recovery codes with "Download .txt" and "Print" affordances
     and a "I've saved my recovery codes" confirmation checkbox before
     leaving the page
7. Audit event `TOTP_ENROLLED` written with user + IP.

### Primary path — login when 2FA is enabled

1. User submits username + password to `POST /api/v1/auth/login`.
2. Server validates credentials *and* lockout state exactly as today.
3. If the user has `totp_enabled = true`, the server does NOT create the
   logged-in session yet. It returns `200` with body `{ "challenge":
   "totp", "challenge_token": "<short-lived signed token>" }` and writes
   audit `LOGIN_PASSWORD_OK_2FA_REQUIRED`.
4. Browser renders the TOTP challenge view (six-digit input + "Use a
   recovery code instead" link). The challenge token is the only
   server-side handle that links the password step to the second step;
   it is signed (HMAC over user id + issued-at, 5-minute TTL) and stored
   in a short-lived `totp_challenge` cookie (HttpOnly, Secure,
   SameSite=Strict). It is **not** a logged-in session.
5. User submits the six-digit code (or a recovery code) to `POST
   /api/v1/auth/totp/verify` along with the challenge token.
6. Server verifies: HMAC + TTL, then either TOTP code (current step ±1
   for clock drift) or one recovery-code hash.
7. On success: real session is created (same path as today's
   `auth.py:314`), `LOGIN_SUCCESS` audit fires, recovery code (if used)
   is removed from `recovery_code_hashes` and the *count remaining* is
   shown to the user with a soft warning when ≤ 3 left.
8. On failure: `LOGIN_2FA_FAILED` audit, increment `failed_logins` on
   the user (same lockout behavior as the password step), 401 response.
   Replay of the same code is rejected even within the 30 s step window
   by remembering the last-accepted step number per user.

### Primary path — admin policy: require 2FA for remote sessions

1. Admin opens Settings → Security → "Require 2FA for remote
   (Tailscale Funnel) sessions" toggle. Off by default.
2. When on:
   - LAN sessions are unchanged.
   - Sessions whose request IP matches Tailscale Funnel space (the
     classifier described below) MUST present a TOTP challenge after
     password, even for users who have not enabled TOTP themselves.
   - Users who have not enrolled cannot log in remotely. The challenge
     view shows: "Two-factor authentication is required for remote
     access. Please enroll on the local network first." with a clear
     pointer back to Settings.
3. The toggle itself is admin-only and is one of the audit events
   (`POLICY_REMOTE_2FA_ENABLED` / `_DISABLED`).

### Primary path — disable / manage

- Disable 2FA from the same Settings card. Re-prompts for password and a
  current TOTP code (or recovery code). On success:
  `totp_enabled = false`, `totp_secret = ""`, `recovery_code_hashes =
  []`. Audit `TOTP_DISABLED`.
- Regenerate recovery codes (admin or self): re-prompt for password +
  current TOTP, generate a fresh set, invalidate the old hashes,
  show-once. Audit `TOTP_RECOVERY_CODES_REGENERATED`.
- Admin reset for a locked-out user: an admin can clear
  `totp_enabled` / `totp_secret` / `recovery_code_hashes` for *another*
  user (not self) from the existing user-management page. This is
  guarded by admin role + a confirmation step + audit event
  `TOTP_RESET_BY_ADMIN`. The admin never sees the user's secret.

### Failure states (must be designed, not just unit-tested)

- Six-digit code entered wrong → "That code didn't match. Try again."
  Failure count increments.
- Code entered correctly but ±2 steps out of drift window → same error
  copy. (We don't tell the attacker their code was almost right.)
- Code reuse (same step number replayed) → same error copy.
- Challenge token expired (>5 min between password and code) → "Your
  sign-in expired. Please sign in again." Bounce back to login.
- All recovery codes used and authenticator app gone → user is locked
  out by design; only an admin reset clears it. Settings page warns at
  ≤ 3 codes remaining and at 0 codes remaining.
- Admin enables "require 2FA for remote" and then *they themselves*
  haven't enrolled → toggle is refused server-side with a clear message
  ("You must enroll yourself first to avoid locking out remote admin
  access"). This avoids a self-bricking foot-gun.
- Last-admin guard: as with password reset (`user_service.py:176`), an
  admin cannot reset their own TOTP from the admin reset path; they
  must use the self-disable flow.
- Server clock drift > 30 s → ops issue, surfaced via existing
  `health.py` / time-sync (ADR-0019). Not a 2FA-specific failure mode,
  but the spec must reference it because TOTP correctness depends on it.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.
`scripts/smoke-test.sh` rows are listed in the Validation plan section.

- AC-1: A user with `totp_enabled = false` logs in with username +
  password as today; no challenge is shown.
  **[unit: `app/server/tests/test_auth.py`]**
- AC-2: Enrollment generates a fresh secret on each open of the enroll
  flow and only persists it on confirm-with-valid-code.
  **[unit: new `test_totp_service.py`]**
- AC-3: After enrollment a wrong code rejects with 401 and increments
  `failed_logins`.
  **[unit + integration]**
- AC-4: After enrollment a correct code creates the session and resets
  `failed_logins`.
  **[unit + integration]**
- AC-5: ±1 step (≈30 s) drift accepted; ±2 rejected; same step replay
  rejected.
  **[unit, with frozen time]**
- AC-6: Recovery code consumed once succeeds; the same recovery code
  presented a second time fails.
  **[unit]**
- AC-7: Challenge token expired (>5 min) is rejected and leaves no
  session.
  **[unit]**
- AC-8: Disabling 2FA requires both password and a current TOTP code (or
  recovery code) and clears all three persisted fields.
  **[unit]**
- AC-9: Rate limiting (`auth.py:_check_rate_limit`) and the lockout
  ladder (`auth.py:_get_lockout_duration`) apply to the TOTP step the
  same way they apply to the password step — combined attempts in either
  step share the same counter for the same source IP and the same user.
  **[integration]**
- AC-10: With "require 2FA for remote" ON, a request that the classifier
  marks Tailscale-Funnel must complete TOTP regardless of
  `user.totp_enabled`. A request marked LAN follows the user-level
  setting.
  **[integration with mocked classifier]**
- AC-11: A non-enrolled user cannot complete a remote login while
  "require 2FA for remote" is ON; the response steers them to LAN
  enrollment.
  **[integration]**
- AC-12: Admin-resets-another-user's-TOTP clears the three fields,
  writes `TOTP_RESET_BY_ADMIN`, refuses self-reset, and respects the
  last-admin guard.
  **[unit]**
- AC-13: All TOTP audit events appear in `/data/logs/auth.log` (per
  ADR-0011 audit path) with user, IP, and outcome.
  **[unit on `audit.py`, integration end-to-end]**
- AC-14: Plaintext recovery codes appear in exactly one HTTP response —
  the enroll-confirm response — and never in logs, never in the DB.
  **[contract test asserting log scrubbing + DB inspection]**
- AC-15: Hardware: on a deployed image, an enrolled admin can log in
  over LAN and over Tailscale, and the "require 2FA for remote"
  toggle behaves as specified.
  **[hardware verification + smoke-test row]**

## Non-Goals

- WebAuthn / hardware security keys (separate, larger track; out of
  scope here per issue #238).
- SMS or email OTP (requires cloud relay; conflicts with the
  no-internet-by-default mission).
- Forcing 2FA on every LAN session — opt-in by default; only the
  remote-session policy is enforceable in v1.
- Per-user policy admin UI beyond a single "require 2FA for remote
  sessions" toggle.
- Forced re-authentication of *existing* sessions when 2FA is enabled.
  Sessions naturally re-authenticate on idle timeout.
- Backup of TOTP secrets across users / export. The seed lives only on
  the user's authenticator app; recovery is via recovery codes or admin
  reset.

## Module / File Impact List

New code:

- `app/server/monitor/services/totp_service.py` — secret generation,
  QR/otpauth URI, code verification with anti-replay, recovery-code
  generation/verification, challenge-token sign/verify. Pure business
  logic, no Flask imports.
- `app/server/monitor/api/auth_totp.py` (or extend
  `auth.py` blueprint) — endpoints:
  - `POST /api/v1/auth/totp/enroll/start` → returns secret + otpauth URI
  - `POST /api/v1/auth/totp/enroll/confirm` → confirms code, persists,
    returns recovery codes once
  - `POST /api/v1/auth/totp/verify` → consumes a challenge token + code
  - `POST /api/v1/auth/totp/disable`
  - `POST /api/v1/auth/totp/recovery-codes/regenerate`
  - `POST /api/v1/users/<id>/totp/reset` (admin)
- `app/server/monitor/services/request_origin.py` — small classifier
  returning `"lan" | "tailscale_funnel"` for a Flask request, used by
  the policy enforcement. Default-to-LAN on ambiguity (fail-safe is
  *not* fail-open: an unclassifiable request that the admin marked
  remote-required must still succeed only with TOTP, so the safer side
  is "treat as remote" — see Open Questions).

Modified code:

- `app/server/monitor/models.py` — add to `User`:
  - `totp_enabled: bool = False`
  - `recovery_code_hashes: list[str] = field(default_factory=list)`
  - `last_totp_step: int = 0`  *(anti-replay)*
  Add to `Settings`:
  - `require_2fa_for_remote: bool = False`
- `app/server/monitor/auth.py` — split `login()` into
  `_verify_password()` + 2FA branch. Reuse rate-limit / lockout state
  for the TOTP step.
- `app/server/monitor/services/user_service.py` — extend
  `change_password` admin guards' spirit to TOTP reset (last-admin
  guard); or move both into a sibling guard module.
- `app/server/monitor/services/audit.py` — new event constants:
  `TOTP_ENROLLED`, `TOTP_DISABLED`, `TOTP_VERIFIED`,
  `LOGIN_PASSWORD_OK_2FA_REQUIRED`, `LOGIN_2FA_FAILED`,
  `TOTP_RECOVERY_CODES_REGENERATED`, `TOTP_RECOVERY_USED`,
  `TOTP_RESET_BY_ADMIN`, `POLICY_REMOTE_2FA_ENABLED`,
  `POLICY_REMOTE_2FA_DISABLED`.
- `app/server/monitor/templates/login.html` — second step (TOTP /
  recovery-code switch view).
- `app/server/monitor/templates/settings.html` — Security → Two-factor
  authentication card; admin-only "require 2FA for remote" toggle.
- `app/server/monitor/static/css/style.css` — small additions for the
  recovery-codes display + QR.
- `app/server/monitor/__init__.py` — wire `TotpService` into the
  app-factory, register new blueprint.

Tests (new):

- `app/server/tests/test_totp_service.py`
- `app/server/tests/test_auth_totp_endpoints.py`
- `app/server/tests/test_login_2fa_flow.py`
- `app/server/tests/test_remote_2fa_policy.py`
- `app/server/tests/test_audit_totp.py`

Dependency:

- Add `pyotp` to `app/server/requirements.txt` (already named in
  ADR-0011). Add `qrcode[pil]` for server-side QR PNG; or render the
  otpauth URI as a `data:` SVG client-side to avoid a Pillow dep —
  decide in Implementer review.

Out-of-tree:

- No camera-side change.
- No Yocto recipe change beyond pulling the new Python deps into the
  server packagegroup if not transitively present (`recipes-python`).

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| Auth or security | full relevant suite + smoke |
| API contract | new contract tests for `/api/v1/auth/totp/*` |
| Frontend / templates | browser-level check on `/login` (challenge view) and `/settings` (enroll card) |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Hardware behavior | deploy + `scripts/smoke-test.sh` row that covers an enrolled-user login and a remote-policy toggle |

Smoke-test additions (Implementer to wire concretely):

- "enrolled admin logs in over LAN with TOTP"
- "remote-required policy refuses non-enrolled user via Tailscale"
- "admin resets a locked-out user's TOTP"

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-238-1 | Admin enables remote-2FA policy without enrolling and locks themselves out of remote access. | Major (operational, not safety) | Medium | RC-238-1: server-side guard refuses the toggle until the requesting admin has `totp_enabled = true`. Settings UI surfaces the same. |
| HAZ-238-2 | User loses authenticator app *and* recovery codes → permanently locked out of own account. | Moderate | Medium | RC-238-2: documented admin-reset path; UI nag at ≤ 3 recovery codes; recovery codes are 10-of, single-use. |
| HAZ-238-3 | Replay of a six-digit code within its 30 s step window. | Moderate (security) | Low | RC-238-3: `last_totp_step` per user; reject equal-or-lower step values. |
| HAZ-238-4 | Plaintext recovery codes leak via logs or audit detail string. | Major (security) | Low | RC-238-4: codes never passed to `audit.log_event`'s `detail`; only "remaining: N" recorded. Contract test enforces it. |
| HAZ-238-5 | Server clock drift > 90 s → all valid codes rejected, mass lockout. | Major (operational) | Low | RC-238-5: existing time-sync per ADR-0019; spec adds a health check that surfaces drift > 60 s in `/api/v1/system/health`. |
| HAZ-238-6 | Tailscale-vs-LAN classifier misclassifies a LAN session as remote and forces TOTP unexpectedly. | Minor | Medium | RC-238-6: classifier defaults to LAN when ambiguous; explicit allowlist of LAN CIDRs from `Settings`; integration tests cover both polarities. |
| HAZ-238-7 | Old session cookies survive 2FA enable and bypass the new requirement. | Moderate (security) | Low | Documented Non-Goal in v1: existing sessions stay valid until idle timeout. Operator-facing release note explains. (Out-of-scope per issue but called out so the user can decide.) |

Reference `docs/risk/` for the existing auth-domain risk register; this
spec adds rows; it does not redefine risk policy.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs):

- **Adds** an authentication factor on the trust boundary that matters
  most: Tailscale-Funnel-exposed sessions. Mitigates the residual risk
  in ADR-0011 around password-only login over the public internet.
- **Adds** new persisted secret material: `User.totp_secret` (base32
  shared secret) and `User.recovery_code_hashes`. The shared secret is
  *not* a hash — it must be reversible to the same value the user's
  authenticator computes. Treatment:
  - Stored in the existing user store under `/data/users.json`. That
    file is on the LUKS-encrypted `/data` (ADR-0010) and not in the
    source tree.
  - Encrypted at rest at the field level using the same pepper /
    server-side key infrastructure ADR-0011 introduces (`/data/secrets/
    pepper.key` derivation), under a distinct sub-key derived via HKDF
    so the password pepper and the TOTP-secret key cannot be cross-used.
    **OPEN QUESTION:** confirm whether ADR-0011's `pepper.key` is
    landed in code yet; if not, this spec must not depend on it — fall
    back to LUKS-only and document residual risk.
  - Recovery codes stored as bcrypt hashes (cost matches password hash
    cost), never plaintext.
- **Adds** a short-lived signed challenge token. HMAC key reuses the
  Flask `SECRET_KEY` namespace via a constant-derived sub-key; never
  reuses session cookie key directly.
- **Sensitive paths touched:** `**/auth/**` (yes), `**/secrets/**` (yes,
  new on-disk material), `app/server/monitor/templates/login.html`
  (yes). Per `docs/ai/roles/architect.md` these need extra design
  scrutiny — flagged here. No `.github/workflows/**`, no certificate /
  pairing / OTA flow change. No camera-side change.
- **Audit:** every TOTP state transition is auditable (events listed in
  Module Impact). Audit must NEVER carry plaintext codes or secrets.
- **Rate limit / lockout:** the TOTP step shares the password step's
  rate limit and lockout counter for the same IP and user. A successful
  TOTP verify resets `failed_logins`. A failed TOTP verify increments
  it.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-238` — User need: "I want my admin account safe even if my
  password leaks, especially when reachable from outside my home."
- `SYS-238` — System requirement: "The system shall support TOTP-based
  second-factor authentication for the web admin and shall allow
  admins to require it for sessions originating from Tailscale
  Funnel."
- `SWR-238-A` … `SWR-238-F` — Software requirements (one per AC group:
  enroll, verify, recovery, disable, admin-reset, remote-policy).
- `SWA-238` — Software architecture item: "TOTP service in
  service-layer; Flask blueprint for endpoints; classifier service for
  request origin; persisted state in `User` and `Settings`."
- `HAZ-238-1` … `HAZ-238-7` — listed above.
- `RISK-238-1` … `RISK-238-7` — one per hazard.
- `RC-238-1` … `RC-238-7` — one per risk control.
- `SEC-238-A` (TOTP secret confidentiality), `SEC-238-B` (recovery-code
  hashing), `SEC-238-C` (challenge-token integrity + TTL), `SEC-238-D`
  (audit completeness).
- `THREAT-238-1` (credential stuffing on remote interface),
  `THREAT-238-2` (TOTP replay), `THREAT-238-3` (recovery-code leak via
  logs), `THREAT-238-4` (admin self-lockout via remote policy).
- `SC-238-1` … `SC-238-N` — controls mapping to the threats above.
- `TC-238-AC-1` … `TC-238-AC-15` — one test case per acceptance
  criterion above.

## Deployment Impact

- Yocto rebuild needed: **only if** `pyotp` (and optionally
  `qrcode[pil]`) is not already in the server packagegroup. Confirm in
  `meta-home-monitor/recipes-python`. If absent, this is a recipe
  addition — not a layer / image-class change.
- OTA path: standard server image OTA. No bootloader / partition change.
  Migration on first boot of the new image: existing `User` records
  load with the new defaults (`totp_enabled = False`,
  `recovery_code_hashes = []`, `last_totp_step = 0`) — dataclass
  defaults handle this; the store deserializer must tolerate missing
  fields (it already does for older `notification_rule` fields per
  `models.py:127`). Verify with a test that loads a pre-feature
  `users.json` fixture.
- Hardware verification: yes — required. LAN login with TOTP, remote
  login with TOTP, admin reset path. Add to `scripts/smoke-test.sh`.
- Default state on upgrade: 2FA disabled for every user; remote-policy
  off. No user is locked out by the upgrade itself.

## Open Questions

(None of these are blocking; design proceeds. Implementer captures
answers in PR description.)

- OQ-1: Is the ADR-0011 `pepper.key` infrastructure landed in code yet?
  Decides whether TOTP secrets get field-level encryption-at-rest now or
  in a follow-up. **If unresolved at implementation time, the
  Implementer MUST document residual risk in the PR and not block on
  this.**
- OQ-2: Server-side QR rendering (`qrcode[pil]`, adds Pillow) vs.
  client-side from the otpauth URI (no extra dep, but renders in
  browser). Recommendation: client-side via a small embedded JS QR
  generator already permitted by CSP; revisit if CSP forbids inline
  scripts.
- OQ-3: Tailscale-Funnel classifier source of truth — does the
  Tailscale daemon expose a "this peer arrived via Funnel" signal we
  can read locally, or do we infer from IP / `Tailscale-User-Login`
  headers? Implementer to verify against `tailscale_service.py` and
  Tailscale's documented behavior at implementation time.
- OQ-4: Should existing logged-in sessions be force-invalidated when a
  user enables 2FA? Spec marks this Non-Goal v1. Confirm with operator
  feedback after first release.

(No question is blocking; if OQ-3 turns out to require Tailscale-side
config we don't control on a stock install, the remote-policy AC may
ship behind a feature flag rather than block the rest.)

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): routes thin, business in
  service.
- Preserve modular monolith (ADR-0006): no new long-lived daemon, no new
  process.
- `/data` is the only place mutable runtime state lives.
- Do not weaken the existing rate-limit / lockout model — extend it.
- Don't expand admin powers beyond the single remote-policy toggle and
  the per-user TOTP reset.
- Tests + docs ship in the same PR as code, per `engineering-standards`.
