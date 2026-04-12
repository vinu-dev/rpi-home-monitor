# ADR-0011: Authentication Hardening

**Status:** Proposed  
**Date:** 2026-04-12  
**Context:** Phase 2 security improvements for production deployment

## Decision

Harden the authentication system following OWASP best practices and patterns from Frigate NVR and Synology DSM.

### Password Storage

Keep bcrypt (work factor 12). Add HMAC pepper with a server-side key stored in `/data/secrets/pepper.key` (generated on first boot, never in git). This adds defense-in-depth: even if the password hash file is stolen, the pepper prevents offline cracking without also stealing the key file.

### Password Policy

Follow NIST SP 800-63B (2024):
- Minimum 12 characters (no maximum below 64)
- No composition rules (no "must have uppercase + number + symbol")
- Allow spaces and Unicode
- Check against a bundled blocklist of the top 10,000 breached passwords

**Rationale:** Composition rules reduce usable password space and frustrate users. Length is the single most effective factor. A 16-character passphrase ("correct horse battery staple") is stronger than "P@ssw0rd!".

### Rate Limiting

Add `flask-limiter` with in-memory backend (no Redis needed for 1-5 users):
- 5 login attempts per minute per IP on `/api/v1/auth/login`
- Exponential account lockout: 1 min after 5 failures, 5 min after 10, 30 min after 15
- All failures logged to `/data/logs/auth.log` with timestamp, source IP, username

### Session Management

- 60-minute idle timeout (standard for appliances)
- Optional "remember me" with a 30-day signed token
  - Token hash stored server-side in sessions.json for revocation
  - `Secure; HttpOnly; SameSite=Strict` cookie flags
  - Token rotated on each use
- Regenerate session ID on login (prevent session fixation)

### TOTP (Future)

Add `totp_secret` field to user model now. Implement TOTP UI in a later phase using `pyotp`. Not critical for LAN-only but valuable when exposed via Tailscale Funnel.

## Consequences

- Stronger authentication without user friction (no composition rules)
- Brute force protection without external dependencies (no Redis)
- Audit trail for security incidents
- Forward-compatible with 2FA when needed
