# Secrets Inventory

If your SD card walks away, every row classified as `plaintext-on-data` below is exposed in cleartext today. This page exists to make that exposure visible, not to close it.

No row in this document becomes `encrypted-at-rest` until the operator opts into the LUKS migration tracked in [docs/exec-plans/luks-post-pair-migration.md](../exec-plans/luks-post-pair-migration.md). Until then, file permissions reduce casual exposure but do not protect against raw SD-card access.

Source of truth note: the persisted settings-secret paths in this page hand-mirror `monitor.services.settings_service.SECRET_FIELDS`; the pre-commit guard imports the same constant.

| Asset | File / field | Classification | Linked threat | Linked mitigation |
|---|---|---|---|---|
| Flask session signing key | `/data/config/.secret_key` | `plaintext-on-data` | `THREAT-005`, `THREAT-016` | Opt into LUKS; on suspected compromise reflash and let the server mint a fresh key (`SECRET_KEY_ROTATED` audit event on first boot). |
| Camera pairing secret | `cameras.json:pairing_secret` | `plaintext-on-data` | `THREAT-005`, `THREAT-016` | Opt into LUKS; unpair then re-pair each camera to mint a fresh value (`CAMERA_PAIRING_SECRET_ROTATED`). |
| Tailscale auth key | `settings.json:tailscale_auth_key` | `plaintext-on-data` | `THREAT-005` | Opt into LUKS; rotate in the Tailscale admin console, then clear or replace the stored value (`TAILSCALE_AUTH_KEY_ROTATED`). |
| Offsite backup secret key | `settings.json:offsite_backup_secret_access_key` | `plaintext-on-data` | `THREAT-005` | Opt into LUKS; rotate the offsite-backup credentials at the provider and update the stored value. |
| Webhook bearer/HMAC secret | `settings.json:webhook_destinations[].secret` | `plaintext-on-data` | `THREAT-005` | Opt into LUKS; rotate the remote webhook secret and update the destination in Settings. |
| CA private key | `/data/certs/ca.key` | `plaintext-on-data` | `THREAT-005`, `THREAT-016` | Opt into LUKS; reflash and regenerate the trust chain if compromise is suspected. |
| Server mTLS private key | `/data/certs/server.key` | `plaintext-on-data` | `THREAT-005`, `THREAT-016` | Opt into LUKS; reflash and regenerate the server certificate if compromise is suspected. |
| Per-camera mTLS private keys | `/data/certs/cameras/cam-*.key` | `plaintext-on-data` | `THREAT-005`, `THREAT-016` | Opt into LUKS; unpair and re-pair affected cameras so fresh certs and pairing secrets are issued. |
| User password hashes | `users.json:password_hash` | `hashed` | `THREAT-005` | Bcrypt cost 12 reduces disclosure impact, but operators should still reset every user password after a suspected physical compromise. |
| Recovery code hashes | `users.json:recovery_code_hashes` | `hashed` | `THREAT-005` | Bcrypt-protected like password hashes; regenerate recovery codes or disable/re-enroll 2FA after suspected compromise. |
| User TOTP secret | `users.json:totp_secret` | `plaintext-on-data` | `THREAT-005` | Reset TOTP enrollment for affected users after suspected compromise; LUKS is the long-term at-rest control. |
| NetworkManager WiFi PSK | `/etc/NetworkManager/system-connections/<ssid>.nmconnection` | `os-managed` | `THREAT-005` | Rotate the router PSK and prefer an IoT VLAN or dedicated network; this file is OS-managed rather than app-managed. |
| First-boot WiFi password during provisioning | Setup wizard form value | `in-memory-only` | `THREAT-005` | The app passes the password straight to `nmcli` and does not persist it itself; only the OS-managed NetworkManager profile remains on disk. |

## Rotation Order After Suspected SD-Card Compromise

1. Revoke each camera by unpairing it, then re-pair it so new mTLS keys and a new `pairing_secret` are minted.
2. Rotate the Tailscale auth key in the Tailscale admin console and clear or replace the stored `tailscale_auth_key`.
3. Reflash the server SD card and complete setup again so the server generates a fresh `.secret_key` and TLS material.
4. Reset every user password and re-enroll TOTP where appropriate.
5. Rotate the WiFi PSK at the router because NetworkManager persisted it outside the app.

## Machine-Readable Field Index

- field: /data/config/.secret_key
- field: cameras.json:pairing_secret
- field: settings.json:tailscale_auth_key
- field: settings.json:offsite_backup_secret_access_key
- field: settings.json:webhook_destinations[].secret
- field: /data/certs/ca.key
- field: /data/certs/server.key
- field: /data/certs/cameras/cam-*.key
- field: users.json:password_hash
- field: users.json:recovery_code_hashes
- field: users.json:totp_secret
- field: /etc/NetworkManager/system-connections/<ssid>.nmconnection
