# ADR-0022: No Backdoors in Authentication or Recovery

**Status:** Accepted
**Date:** 2026-04-20
**Deciders:** Vinu
**Relates to:** ADR-0009 (mTLS pairing), ADR-0011 (auth hardening). Supersedes nothing; hardens ADR-0011.

## Context

This is a home-security product. Its value proposition — to the operator, their family, their insurer — is that the device and its recordings cannot be accessed by someone who isn't authorised.

While iterating on admin-password recovery (issue #100) a CLI script was written, reviewed, shipped, and then removed. The script rewrote the admin's bcrypt hash in-place; it required `sudo` on the device; it was internally justified as "no worse than `rm -rf /data`." All of that was technically correct, and product-wrong.

The script's existence *as a documented recovery mechanism* changed the answer to the question a hostile visitor asks: "*How do I get into this device?*" Without the script, the answer is "find a password" — same as a bank or a house. With the script, the answer includes "or get `sudo` once and run this." A fleeting guest, an ex-partner who knew the old credentials, a repair technician — any of them become viable threats. The threat actor no longer has to be *sophisticated*; they have to be *transient*.

This ADR codifies the rule we should have had from day one, so the next person (or the same person six months from now) doesn't rebuild the same convenience under a different name.

## Decision

**No backdoors in authentication or recovery. Period.**

Operationally, this means:

1. **No documented command, script, or endpoint that bypasses the primary auth mechanism.** A "sudo-only" command is still documented; a "localhost-only" endpoint is still reachable from anything the OS runs; a "physical-access-only" procedure is still a recipe an attacker follows. The test isn't "could this be abused?" — anything non-trivial can. The test is "does this reduce the attacker's required sophistication?" If yes, it's a backdoor.

2. **Pre-auth surfaces disclose nothing.** The login page, unauthenticated API responses, and any other surface reachable without a session must not name recovery commands, script paths, SSH procedures, internal URLs, or privileged tools. The login screen says "Contact your administrator." That's the ceiling.

3. **Lost-sole-admin recovery is hardware.** The path is a physical factory reset (button, GPIO pin-short, or SD-card reflash). The interim, until the physical reset lands, is the SD-card reflash. The pain is deliberate — it's the bar that stops a guest-for-a-day from walking away with an operational account.

4. **Admin-assisted recovery through an authenticated UI is fine.** An admin resetting another user's password from Settings → Users is an auditable, session-gated, observable transaction. It doesn't weaken the threat model. It's not a backdoor.

5. **When in doubt, refuse.** If a recovery story has a step that reads "and then the operator runs X," stop. Find a design that doesn't need X, or escalate the requirement to hardware.

6. **Rejections are documented.** A backdoor that was proposed, reviewed, and refused is recorded under a "rejected on review" section in the relevant exec plan or ADR. The reasoning survives; nobody re-invents the same convenience in six months without reading why it was turned down.

## Alternatives considered

### Narrow-scoped CLI recovery script (what we shipped and removed)

`scripts/reset-admin-password.py`, sudo-only, single-purpose, audit-logged. Rejected after shipping — see #100 and `docs/archive/exec-plans/auth-recovery.md` §"Slice 1b — rejected on review." The threat it introduced was a lowered sophistication bar for transient-physical-access attackers; the inconvenience it removed was a full factory reset by a sole admin who forgot their password. The latter is rare; the former is every houseguest.

### Localhost-only HTTP recovery endpoint

Same objection. "Localhost-only" is not a meaningful boundary for anything running on the device (including a compromised service or a malicious add-on). Rejected.

### TPM-backed recovery OTP

A recovery OTP printed at factory-test time, stored in the TPM, displayed on a physical LCD, etc. This *is* a reasonable design — the critical property is that the OTP is bound to hardware the attacker doesn't possess. Not rejected; parked until the hardware refresh (Pi 5+ with TPM header) makes it practical. Tracked as part of the secrets-at-rest work (ADR pending, issue #101).

## Consequences

### Positive

- **Attacker required sophistication stays high.** A transient-physical-access attacker finds no documented recovery path; the real path is "reflash the SD card," which takes ~20 minutes, destroys all state, and is detectable afterwards.
- **The threat model is teachable.** "Everything short of hardware reset requires credentials" is a sentence the user can internalise.
- **Engineering discipline.** The rule makes the easy answer ("ship a recovery script") unavailable, which forces the harder design conversation ("how do we cap the blast radius without creating a bypass?"). That conversation produces better product.

### Negative

- **Sole-admin-forgets-password is painful.** By design. Until the hardware factory reset ships, the user reflashes the SD card and re-pairs cameras. This is the cost of the rule.
- **Operational runbooks can't lean on "just SSH in and fix it."** Any operational tool we ship runs *before* authentication, not *around* it.

### Neutral

- **No change to admin-assisted recovery.** Settings → Users → "Reset password" flow is unaffected; it's the correct shape.
- **No change to mTLS pairing.** Camera ↔ server pairing uses certificates, not a shared secret that could be "reset"; this ADR doesn't touch it.

## Implementation

- `docs/ai/engineering-standards.md` §"Security: No Backdoors" — operational rule, read by every AI agent working on this repo.
- `docs/guides/admin-recovery.md` — user-facing procedure. Explicitly lists rejected alternatives so they don't get re-proposed as "simpler" fixes.
- `docs/archive/exec-plans/auth-recovery.md` §"Slice 1b — rejected on review" — the working example of the rule in action.
- `scripts/reset-admin-password.py` — removed. The absence of the file *is* the enforcement.
- `app/server/monitor/templates/login.html` — pre-auth surface collapsed to a single line that names nothing.

## Audit

Any PR that adds a new authentication / recovery surface must cite this ADR in its description and explain which of the six rules applies. Reviewers flag silent additions — a new endpoint or script in an auth-adjacent module without an ADR reference is a review-blocking concern, not a style nit.
