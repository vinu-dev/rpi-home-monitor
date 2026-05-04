# Admin Recovery

What to do if the only admin account on a Home Monitor device has lost its password.

Two very different cases. Pick the one that applies.

---

## Case 1 — Another admin is available

**Standard path. No SSH, no hardware reset.**

Any other admin user signs in and opens **Settings → Users → "Reset password"** on the locked-out user's row. They pick a temporary password (≥ 12 chars) and hand it to the user. The locked-out admin signs in with the temporary password and is immediately forced to rotate it to something only they know. The admin who did the reset never learns the final password.

If the user also lost their TOTP factor, also click **Reset 2FA** on the same row before they log in again — the password reset and TOTP reset are separate actions.

Coordinate before issuing a reset — if two admins reset the same user in parallel, whichever temporary password lands second wins. That's a communications hazard, not a new privilege path.

### What hits the audit log

- `PASSWORD_RESET_BY_ADMIN` when the admin sets the temporary password.
- `PASSWORD_CHANGED` when the user rotates to their final password.

Example checks on the device shell:

```bash
grep -F 'PASSWORD_RESET_BY_ADMIN' /data/logs/audit.log
grep -F 'PASSWORD_CHANGED' /data/logs/audit.log
```

---

## Case 2 — No admin is available (sole admin locked out)

**There is no software recovery path.** By design.

A software recovery command, no matter how tightly scoped, is a permanent backdoor: anyone who finds the device and can run it bypasses the admin password entirely. We're not willing to ship that on a home-security product.

The recovery path is a **hardware factory reset** — a physical button / pin-short on the server board that wipes `/data` and returns the device to first-boot state. You re-run the setup wizard, create a new admin, and re-pair the cameras.

Implementation status: **planned, not yet shipped.** Tracked with the hardware-refresh work. Until the physical reset lands, the transitional path is:

- Unplug the device.
- Remove the SD card, reflash the OS image (`docs/guides/build-setup.md`), and put it back. WiFi / admin / cameras all need to be set up again as if it were a new device.
- Any video recordings on the `/data` partition or USB drive are preserved or wiped depending on whether you reformatted those separately — treat this as a full reset.

This is deliberately painful. Forgetting the admin password on a security device should be an uncommon event; "accidentally" triggering it on a live deployment requires physical access, which is already the trust boundary.

---

## Case 3 â€” Suspected SD-card compromise or device theft

**Treat every `plaintext-on-data` secret as exposed until you rotate it.**

Follow this order:

1. Unpair every camera from **Dashboard â†’ Cameras** so the existing per-camera certificate and pairing secret stop being trusted.
2. In the Tailscale admin console, revoke the old pre-auth key, then open **Settings â†’ Network â†’ Tailscale** and clear or replace the stored auth key.
3. Reflash the server SD card and boot through first-run setup again. This forces a fresh Flask session-signing key and fresh on-disk trust material.
4. Re-run the setup wizard and re-pair each camera. That mints fresh per-camera certificates and fresh `pairing_secret` values.
5. In **Settings â†’ Users**, reset every user password. The stolen card only contained bcrypt hashes, not plaintext passwords, but offline brute-force is still possible over time.
6. Have each user re-enroll TOTP if it was enabled, because `users.json:totp_secret` was stored in cleartext on the stolen card.
7. Rotate the WiFi PSK at the router or access point. NetworkManager stored it in `/etc/NetworkManager/system-connections/<ssid>.nmconnection`, which is outside the app but still readable from the card.

Expected audit trail after recovery:

- `SECRET_KEY_ROTATED` on the freshly reflashed server boot.
- `TAILSCALE_AUTH_KEY_ROTATED` when the stored Tailscale key is cleared or replaced.
- `CAMERA_PAIRING_SECRET_ROTATED` as each camera re-pairs.

---

## Explicitly not supported

- **No `/opt/monitor/scripts/reset-admin-password.py`.** A previous iteration shipped a sudo-only CLI script; it was removed because its existence leaked an attack surface to anyone who read the login page or the repo. A single documented command that resets the admin password is the definition of a backdoor, even when gated behind `sudo`.
- **No emergency HTTP endpoint.** Even a 127.0.0.1-only endpoint is reachable from any process on the box; same objection.
- **No email / SMS reset.** The device is single-LAN; there is no trusted external identity to send a token to.

## Security model

`docs/archive/exec-plans/auth-recovery.md` is the design record. Short version: admin-assisted reset is in-app and audited; admin-alone reset is hardware-only. Anything in between is a backdoor, regardless of how narrow the permission envelope is.
