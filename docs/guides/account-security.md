---
title: Account Security Guide
status: active
audience: [human, ai]
owner: engineering
source_of_truth: false
---

# Account Security

Use this guide for operator-facing account controls: password changes,
two-factor authentication, recovery codes, and remote-access policy.

## Two-Factor Authentication

Users manage their own 2FA from **Settings > Account**.

1. Open **Settings > Account**.
2. Click **Enable 2FA**.
3. Add the displayed secret or authenticator URI to an authenticator app.
4. Enter the current authentication code and confirm.
5. Store the one-time recovery codes outside the device.

After enrollment, sign-in is two step: password first, then a TOTP code or a
single-use recovery code.

## Recovery Codes

Recovery codes are shown only when they are created or regenerated. The device
stores only hashes of those codes.

To rotate them, open **Settings > Account**, enter the current password plus a
TOTP or recovery code, then regenerate. Old recovery codes stop working as soon
as the new set is issued.

## Disabling 2FA

Users can disable their own 2FA from **Settings > Account** by entering their
current password plus a TOTP or recovery code.

Admins can reset another user's 2FA from **Settings > Users**. This clears that
user's TOTP secret and recovery-code hashes; it does not reveal any secret.
Admins cannot reset their own 2FA through the admin reset path.

## Remote Access Policy

Admins can require 2FA for remote-origin sign-ins from **Settings > System** by
enabling **Require two-factor authentication for remote access**.

The enable path is guarded: the admin turning it on must already have 2FA
enabled. This prevents a remote-access policy from locking out the operator who
enabled it. Disabling the policy stays allowed so an admin can roll back a bad
configuration.

Remote users who have not enrolled 2FA must enroll from the local network before
they can sign in remotely while the policy is enabled.

Related records:

- [`docs/history/specs/238-totp-2fa.md`](../history/specs/238-totp-2fa.md)
- [`docs/guides/admin-recovery.md`](admin-recovery.md)
- [`docs/architecture/software-architecture.md`](../architecture/software-architecture.md)
