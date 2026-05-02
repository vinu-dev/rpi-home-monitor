# Exec Plan — Authentication Recovery

**Status:** Accepted. Slice 1 (admin resets other user) shipped. The originally-planned CLI admin-recovery script was removed on review — any documented command that bypasses the admin password is a backdoor regardless of its permission envelope. Admin-forgot recovery is now a hardware factory reset tracked as hardware-refresh work (see [`docs/guides/admin-recovery.md`](../../guides/admin-recovery.md)).
**Date:** 2026-04-20 (initial) → 2026-04-20 (revised — removed CLI path)
**Owner:** vinu-dev
**Closes:** #99 (slice 1). Slice 2 (self-service reset token) tracked in #99; secrets-at-rest tracked in #101. #100 reframed as hardware factory reset, deferred to hardware work.

## Why

Three related gaps in the current auth story, surfaced as issues #99 / #100 / #101:

1. **Locked-out user** — an admin has no in-app way to reset another user's password. Today the admin deletes + recreates the account, losing the user's audit trail.
2. **Locked-out admin** — if the *only* admin forgets their password, recovery is intentionally a hardware reset / reflash. The earlier software-recovery idea was rejected as a backdoor.
3. **Stolen SD card** — everything on `/data/config/` is plain text: Flask secret key, pairing secrets, mTLS private keys. Physical access = full compromise.

The first is an urgent usability bug. The second is a deliberate product constraint after review. The third is an architectural layer (LUKS / TPM / secret wrapping) that needs its own ADR and hardware work.

## Design principles

1. **One forced-change gate already works.** `auth.py:_must_change_block` already returns `403 { must_change_password: true }` on every endpoint when the flag is set on a user. Any recovery flow just has to set that flag on success; the existing gate does the rest.
2. **Recovery requires out-of-band trust.** A forgotten admin password can't be reset over HTTPS by any sufficiently authenticated admin — because there are no authenticated admins. Recovery has to lean on physical / SSH / boot-partition access.
3. **No email, no SMS, no cloud callback.** The device is single-LAN by design. All recovery is local.
4. **Every reset writes an audit event.** Non-negotiable — the audit log is the only after-the-fact evidence that a recovery happened.

## Scope map

| Case                              | Who triggers it         | Who is authenticated at the time  | Chosen path                                   | Issue |
|-----------------------------------|-------------------------|-----------------------------------|-----------------------------------------------|-------|
| User forgets password             | Admin (helping user)    | Admin is logged in                | **Admin reset** via Settings → Users          | #99 s1 |
| Admin self-service change         | Admin                   | Admin is logged in                | (already shipped) Settings → Change Password  | –     |
| **All admins locked out**         | Device operator         | Nobody                            | **Hardware factory reset / reflash**          | #100  |
| User forgot, no admin available   | Admin issues OTP        | Admin is logged in *once*         | **One-shot reset token** (slice 2 — later)    | #99 s2 |
| SD card stolen                    | Threat actor            | N/A — physical theft              | **Secrets at rest** — LUKS + wrapped keys (separate ADR) | #101 |

## Slice 1 — what ships now

### 1a. Admin resets another user's password (issue #99 case 1)

**Backend.** `user_service.change_password(..., force_change_next_login: bool = False)`.
  - Existing role check unchanged: admin can change any user's password; a non-admin can only change their own.
  - When `force_change_next_login=True`, the user's `must_change_password` flag is set to **True** after the hash rotates (instead of cleared). The existing `_must_change_block()` gate forces the user to rotate again on next login — admin never knows their final password.
  - Audit event `PASSWORD_RESET_BY_ADMIN` with the acting admin's id + target user id.

**API.** `PUT /api/v1/users/<id>/password` accepts an additional `force_change` boolean. The admin-editing-another-user path sets it; the self-service path ignores it (can't force-flag yourself into a loop).

**UI.** Settings → Users: add a **"Reset password"** button per user row (admin-only, hidden for viewers). A small modal asks for the temporary password (same 12-char minimum as create-user) and, on submit, calls the endpoint with `force_change: true`.

**Safety rails.**
  - Refuse to reset the only remaining admin. The service already guards "can't demote the last admin" — extend the same guard to "can't force-change the last admin" so the admin can't strand themselves in a must-change loop if the reset fails halfway.
  - All reset flows audit-log.

### 1b. ~~CLI admin recovery~~ — rejected on review

An earlier revision of this plan shipped `scripts/reset-admin-password.py`: a sudo-only Python script that rewrote the admin's bcrypt hash in-place. It was removed, and the deploy path along with it, after a product review.

**Why rejected.** Even narrowly scoped, a documented command that resets the admin password is a backdoor. Threat-model language aside ("sudo = operator-trusted, same bar as rm -rf"), the practical problem is that a *searchable, discoverable* recovery command on a home-security product normalises the "physical access == easy bypass" mental model. That's a posture we're explicitly rejecting.

**Consequence.** The "sole admin locked out" case has **no software recovery**. It becomes a **hardware factory reset** — a pin-short / button on the server board that wipes `/data` and returns to first-boot. Tracked as hardware-refresh work; documented interim in `docs/guides/admin-recovery.md`.

### 1c. "Forgot password?" on `/login`

**Never a disclosure.** A pre-auth surface must not leak the existence of recovery paths. The login page shows a single muted line: *"Contact your administrator if you can't sign in."* No command, no mention of SSH, no link to documentation. Full recovery procedure lives in `docs/guides/admin-recovery.md` (in the repo, not served on the device).

## Slice 2 — deferred (#99 case 2)

Admin issues a **one-shot, short-lived reset token** from Settings → Users. The admin hands it to the user out-of-band (phone / whiteboard). The login page gets a "Use reset code" link that takes a username + token, logs the user in with `must_change_password=true`, consumes the token, and audit-logs.

Token design (to be revisited):
  - 6-8 digit numeric code (easier to read out loud than a hex string).
  - 15-minute TTL, single-use, per-user, rate-limited at 5 attempts / 15 min.
  - Stored hashed in `users.json` (`reset_token_hash`, `reset_token_expires_at`).
  - Separate audit events `PASSWORD_RESET_TOKEN_ISSUED` / `_USED`.

Deferred because slice 1 solves the immediate pain: "user forgot + admin available." Slice 2 is for multi-user households where the admin is not always present; we can ship after some real usage data.

## Slice 3 — secrets at rest (#101)

Separate ADR work. Outline:

1. **Short-term**: device-derived key wrapping. Generate a per-device key from `/proc/cpuinfo` serial + boot-time random, wrap the Flask secret key and `pairing_secret` with AES-GCM. Stolen SD card without the running device + kernel state is useless.
2. **Medium-term**: move `/data/config/` onto a LUKS partition whose key is stored in the TPM (Pi 5 has a TPM header; Zero 2W / Pi 4B do not — this is hardware-refresh work).
3. **Long-term**: mTLS private keys move into PKCS#11 via SoftHSM, then a real HSM on the server box.

Tracked in #101. Not gated by this plan; doesn't change the flows designed here.

## Rollout

1. Ship slice 1 as a single PR touching `user_service.py`, `users.py` API, `settings.html` Users tab, and login-page copy.
2. Tests:
   - Unit: `force_change_next_login` toggles `must_change_password`.
   - Integration: admin resetting another user sets the flag; that user's next request returns 403 + `must_change_password: true`; password change completes the unlock.
3. Manual on-device test: reset another user from Settings, confirm their next request returns `403 { must_change_password: true }`, then confirm a successful password change clears the lock.
4. Documentation: keep `docs/guides/admin-recovery.md`, the login page, and this plan aligned on "no software recovery for sole-admin lockout."
5. Update `docs/guides/hardware-setup.md` and `docs/history/baseline/requirements.md` (SR-SRV-xx for recovery).

## Open questions

- **Should admin's own password reset also set `must_change_password=true`?** No — an admin changing their own password is deliberate and already went through auth; forcing another change would be an infinite loop.
