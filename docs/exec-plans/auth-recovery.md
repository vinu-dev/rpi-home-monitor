# Exec Plan — Authentication Recovery

**Status:** Accepted. Slice 1 (admin resets other user + CLI admin recovery) shipped with this plan.
**Date:** 2026-04-20
**Owner:** vinu-dev
**Closes:** #99 (slice 1), #100. Slice 2 (self-service reset token) tracked in #99; secrets-at-rest tracked in #101.

## Why

Three related gaps in the current auth story, surfaced as issues #99 / #100 / #101:

1. **Locked-out user** — an admin has no in-app way to reset another user's password. Today the admin deletes + recreates the account, losing the user's audit trail.
2. **Locked-out admin** — if the *only* admin forgets their password, there is no recovery path short of a full factory reset (wipes cameras, pairings, settings). Disproportionate.
3. **Stolen SD card** — everything on `/data/config/` is plain text: Flask secret key, pairing secrets, mTLS private keys. Physical access = full compromise.

The first two are urgent usability bugs. The third is an architectural layer (LUKS / TPM / secret wrapping) that needs its own ADR and hardware work.

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
| **All admins locked out**         | Device operator         | Nobody                            | **CLI recovery script** over SSH              | #100  |
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

### 1b. CLI admin recovery (issue #100 Option A)

**Script.** `scripts/reset-admin-password.py` — a standalone, argparse-driven Python script.
  - Imports only stdlib at top-level so it runs on the camera image out of the box. The bcrypt / user-store modules are late-imported inside `main()` so `--help` works even when the monitor app isn't importable.
  - Loads `/data/config/users.json` (path overridable via `--store`) using the same `JsonFileStore` the app uses — atomic writes, same on-disk format.
  - Finds the first user whose `role == "admin"`. Refuses if there is zero, or prints all candidates if more than one and `--username` wasn't supplied.
  - Writes a new bcrypt hash using the same `hash_password()` the app uses (cost 12) so the hash format matches exactly.
  - Sets `must_change_password: true` so the admin is forced to rotate on first login.
  - Writes a line to `/data/config/audit.log` recording `PASSWORD_RESET_VIA_CLI` with the acting Unix user. The audit event is ingested by the running app on its next poll.
  - Does **not** restart the monitor service — the user store reads from disk on each `get_user` call via `JsonFileStore`, so a fresh bcrypt hash on disk takes effect immediately without a restart.

**Invocation.**
  ```
  sudo python3 /opt/monitor/scripts/reset-admin-password.py \
      --username admin \
      --password 'temp-password-123'
  ```
  `--password` is mandatory (no interactive prompt) so the command is captured faithfully in shell history and the operator knows what to hand back to the user.

**Security model.** The script is only effective to someone who can already `sudo` on the device. That's the same bar as `systemctl stop` or `rm -rf /data` — consistent with ADR-0009's trust boundary ("physical / SSH access = operator-trusted"). Not a new threat surface.

### 1c. "Forgot password?" link on `/login`

A `<a href="#" …>` on the login page that opens a short static note: *"Ask your admin to reset it from Settings → Users. If you are the admin and have no other admin account, run `sudo /opt/monitor/scripts/reset-admin-password.py --help` on the device."*

This is a one-line documentation surface — sets expectations now, leaves room for the token flow later.

## Slice 2 — deferred (#99 case 2)

Admin issues a **one-shot, short-lived reset token** from Settings → Users. The admin hands it to the user out-of-band (phone / whiteboard). The login page gets a "Use reset code" link that takes a username + token, logs the user in with `must_change_password=true`, consumes the token, and audit-logs.

Token design (to be revisited):
  - 6-8 digit numeric code (easier to read out loud than a hex string).
  - 15-minute TTL, single-use, per-user, rate-limited at 5 attempts / 15 min.
  - Stored hashed in `users.json` (`reset_token_hash`, `reset_token_expires_at`).
  - Separate audit events `PASSWORD_RESET_TOKEN_ISSUED` / `_USED`.

Deferred because slice 1 solves the immediate pain (all the failure cases today are "user forgot + admin available" or "admin forgot + can ssh"). Slice 2 is for multi-user households where admin isn't always online; we can ship after some real usage data.

## Slice 3 — secrets at rest (#101)

Separate ADR work. Outline:

1. **Short-term**: device-derived key wrapping. Generate a per-device key from `/proc/cpuinfo` serial + boot-time random, wrap the Flask secret key and `pairing_secret` with AES-GCM. Stolen SD card without the running device + kernel state is useless.
2. **Medium-term**: move `/data/config/` onto a LUKS partition whose key is stored in the TPM (Pi 5 has a TPM header; Zero 2W / Pi 4B do not — this is hardware-refresh work).
3. **Long-term**: mTLS private keys move into PKCS#11 via SoftHSM, then a real HSM on the server box.

Tracked in #101. Not gated by this plan; doesn't change the flows designed here.

## Rollout

1. Ship slice 1 as a single PR touching `user_service.py`, `users.py` API, `settings.html` Users tab, and the new CLI script.
2. Tests:
   - Unit: `force_change_next_login` toggles `must_change_password`.
   - Integration: admin resetting another user sets the flag; that user's next request returns 403 + `must_change_password: true`; password change completes the unlock.
   - CLI: dry-run + actual rewrite against a tmp `users.json`; audit log line present.
3. Manual on-device test: lock self out, run the script over SSH, confirm login requires rotation.
4. Update `docs/hardware-setup.md` and `docs/requirements.md` (SR-SRV-xx for recovery).

## Open questions

- **Should the CLI script write the new temp password to stdout only, not to `audit.log`?** Leaning yes — the audit event records the *fact* of reset, not the temporary value. Currently drafted to match.
- **Should admin's own password reset also set `must_change_password=true`?** No — an admin changing their own password is deliberate and already went through auth; forcing another change would be an infinite loop.
