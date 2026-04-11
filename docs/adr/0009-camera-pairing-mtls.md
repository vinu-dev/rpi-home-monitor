# ADR-0009: Camera Pairing and mTLS

## Status
Proposed

## Context
Camera-to-server communication currently uses plaintext RTSP. The architecture (Section 3) specifies mTLS for camera identity and RTSPS for stream encryption, but `pairing.py` is a stub. Without mutual authentication, any device on the LAN can impersonate a camera and inject video, or eavesdrop on streams.

The pairing flow must also produce the client certificates used for OTA push authentication (ADR-0008) and establish the trust relationship that LUKS key derivation relies on for the camera (ADR-0010).

### Hardware state (measured 2026-04-11)

**Server already has a CA and server certificate:**
```
/data/certs/
├── ca.crt     # ECDSA P-256, CN=HomeMonitor CA, valid 2025-2035 (10 years)
├── ca.key     # mode 0600, root only
├── ca.srl     # serial number tracking
├── server.crt # ECDSA P-256, CN=home-monitor, SANs: home-monitor, home-monitor.local, localhost, 127.0.0.1
│              # EXPIRES May 29, 2026 (1-year validity)
├── server.key
└── cameras/   # empty — no cameras paired yet
```

**Camera has empty cert directory:**
```
/data/certs/
└── cameras/   # empty
```

The CA is already ECDSA P-256 with 10-year validity. The server cert has only 1-year validity and expires May 2026 — this ADR addresses renewal.

We need:
1. A pairing ceremony that securely exchanges certs without pre-shared secrets.
2. RTSPS enforcement after pairing.
3. Revocation when a camera is unpaired.
4. Server cert auto-renewal before expiry.

## Decision

Implement a **server-local CA with PIN-based pairing** and enforce mTLS on all camera-server channels. Use **5-year validity** for issued certificates with a **systemd timer** for renewal reminders.

---

### 1. Certificate Authority (already exists)

The server's first-boot provisioning already generates the CA. This ADR formalizes the certificate layout:

```
/data/certs/
├── ca.crt                  # CA public cert (ECDSA P-256, 10-year validity)
├── ca.key                  # CA private key (mode 0600, root only)
├── ca.srl                  # Serial number tracking
├── server.crt              # Server TLS cert (signed by CA, 5-year validity)
├── server.key              # Server TLS private key
└── cameras/
    ├── cam-<id>.crt        # Per-camera client cert (signed by CA, 5-year validity)
    └── revoked/
        └── cam-<id>.crt    # Revoked certs (audit trail)
```

**Why ECDSA P-256 (not Ed25519 for TLS certs):**
Ed25519 is used for OTA image signing (ADR-0008) where we control both sides. For TLS certificates, ECDSA P-256 has broader library support — OpenSSL, nginx, MediaMTX, and Python's `ssl` module all handle P-256 natively. Ed25519 TLS cert support is still inconsistent across components.

**Certificate validity:**

| Certificate | Validity | Rationale |
|-------------|----------|-----------|
| CA | 10 years | Root of trust, long-lived. Already generated. |
| Server | 5 years | Balance between convenience and crypto rotation. Auto-renewal reminder. |
| Camera client | 5 years | Long-lived devices. Revocation handles compromise. |

**Server cert renewal:**
- A systemd timer (`cert-renewal-check.timer`) runs weekly
- Checks server cert expiry date
- 30 days before expiry: logs `CERT_EXPIRY_WARNING` to audit log, shows dashboard alert
- On expiry (or admin trigger): generates new server cert signed by CA, reloads nginx + mediamtx
- The CA key signs the renewal — no external dependency

**Fix for current state:** The existing server cert (expires May 2026, 1-year validity) will be regenerated with 5-year validity during the mTLS implementation deployment.

---

### 2. Pairing flow

```
  Admin          Server                     Camera
   │               │                          │
   │  1. "Pair"    │                          │
   │──────────────>│                          │
   │               │  2. Generate:            │
   │               │     - Camera keypair     │
   │               │     - Client cert        │
   │               │       (signed by CA,     │
   │               │        5-year validity)  │
   │               │     - 6-digit PIN        │
   │               │       (valid 5 min)      │
   │  3. Show PIN  │                          │
   │<──────────────│                          │
   │               │                          │
   │  4. Enter PIN on camera setup page       │
   │  (via camera AP or LAN status page)      │
   │─────────────────────────────────────────>│
   │               │                          │
   │               │  5. Camera presents PIN  │
   │               │     via POST /api/pair   │
   │               │<─────────────────────────│
   │               │                          │
   │               │  6. Server validates PIN │
   │               │     Returns:             │
   │               │     - client.crt         │
   │               │     - client.key         │
   │               │     - ca.crt             │
   │               │     - server RTSPS host  │
   │               │     - pairing_secret     │
   │               │       (for LUKS, ADR-10) │
   │               │────────────────────────->│
   │               │                          │
   │               │  7. Camera stores certs  │
   │               │     at /data/certs/      │
   │               │     Restarts streaming   │
   │               │     with mTLS            │
   │               │                          │
   │               │  8. RTSPS + mTLS         │
   │               │<═════════════════════════│
```

---

### 3. Server-side pairing API

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/v1/cameras/<id>/pair` | POST | admin | Generate cert + PIN, start pairing |
| `/api/v1/cameras/<id>/unpair` | POST | admin | Revoke cert, remove camera |
| `/api/v1/pair/exchange` | POST | PIN (one-time) | Camera trades PIN for certs |

**`POST /api/v1/cameras/<id>/pair`** — Admin triggers from dashboard:
- Generates ECDSA P-256 keypair for the camera
- Creates X.509 client cert signed by CA:
  - CN: `cam-<id>`
  - SAN: camera's mDNS hostname
  - Validity: 5 years
  - Serial number stored in `cameras.json` as `cert_serial`
- Generates 6-digit random PIN (cryptographically random)
- Stores PIN with 5-minute expiry in memory
- Returns PIN to admin dashboard
- Audit log: `PAIRING_INITIATED`

**`POST /api/v1/pair/exchange`** — Camera calls with PIN:
- Request body: `{"pin": "123456", "camera_id": "cam-xxxx"}`
- Validates PIN matches and not expired
- Returns: `client.crt`, `client.key`, `ca.crt`, server RTSPS URL, `pairing_secret` (ADR-0010)
- Invalidates PIN (one-time use)
- HTTPS endpoint, does **not** require login — PIN is the authentication
- Rate-limited: 3 attempts per camera per 5-minute window
- Audit log: `CAMERA_PAIRED` on success, `PAIRING_FAILED` on bad PIN

### PIN security analysis

The 6-digit PIN is short-lived (5 min) and rate-limited (3 attempts).

An attacker on the LAN would need to:
1. Know a pairing is in progress (timing window — 5 minutes)
2. Know the camera ID (visible via mDNS, assume known)
3. Guess the PIN: 3 attempts out of 1,000,000 = 0.0003% success probability

Acceptable for a LAN-only home system. The PIN never leaves the local network.

---

### 4. Camera-side pairing

The `PairingManager` class in `camera_streamer/pairing.py`:
- On boot, checks for `/data/certs/client.crt`:
  - **Not found** → unpaired. Lifecycle stays in PAIRING state, status page shows pairing form
  - **Found** → load certs, proceed to CONNECTING with mTLS
- Pairing form on camera status page (`/pair`): admin enters PIN, camera POSTs to server's `/api/v1/pair/exchange`
- On successful exchange:
  - Stores `client.crt`, `client.key`, `ca.crt` in `/data/certs/`
  - Stores `pairing_secret` for LUKS key derivation (ADR-0010)
  - Triggers lifecycle transition to CONNECTING

---

### 5. Camera lifecycle integration

ADR-0004 noted that PAIRING would be a future state. The lifecycle becomes:
```
INIT → SETUP → PAIRING → CONNECTING → VALIDATING → RUNNING → SHUTDOWN
```
- **PAIRING**: Waits for certs. If already paired (certs exist), auto-skips to CONNECTING
- **CONNECTING**: Establishes RTSPS connection to server using mTLS

---

### 6. mTLS enforcement

After pairing, all camera-server channels use mTLS:

**RTSPS (video streams):**
- MediaMTX configured with `serverCert`, `serverKey`, `requireClientCert: true`
- Camera connects with `client.crt` + `client.key`, verifies server cert against `ca.crt`
- Server verifies camera cert against CA — rejects unknown/revoked certs

**OTA push (ADR-0008):**
- Camera OTA agent (port 8080) verifies server cert
- Server presents `server.crt` when pushing updates
- Same CA trust chain

**Health/status polling:**
- Server polls camera health endpoint over HTTPS with mTLS
- Same certs, same trust chain

---

### 7. Unpair / revocation

When admin removes a camera:
1. Server moves `cameras/cam-<id>.crt` to `cameras/revoked/cam-<id>.crt`
2. Adds cert serial to in-memory revocation set (rebuilt from `revoked/` on startup)
3. MediaMTX reloaded to reject the revoked cert
4. Camera removed from `cameras.json`
5. nftables: camera IP removed from `@camera_ips` set
6. Audit log: `CAMERA_REMOVED`, `CERT_REVOKED`

**Why not CRL/OCSP:** With <10 cameras, an in-memory set is simpler than running a CRL distribution point or OCSP responder. The server is both the CA and the only relying party.

**Camera factory reset:** If re-flashed, camera loses certs and appears unpaired. Admin pairs again — old cert is already revoked.

---

### 8. Certificate on camera for OTA and LUKS

The `pairing_secret` returned during cert exchange (step 6 of pairing flow) serves two additional purposes:
- **OTA push authentication (ADR-0008):** mTLS certs issued during pairing authenticate the OTA channel
- **LUKS key derivation (ADR-0010):** `pairing_secret` + CPU serial derive the camera's LUKS encryption key

This makes pairing the single trust establishment ceremony. One pairing flow → mTLS identity + OTA authentication + disk encryption key.

## Rationale

- **Server-local CA**: No external CA dependency. The server IS the trust root — appropriate for a self-hosted home system
- **PIN-based pairing**: Familiar pattern (Bluetooth, WiFi Direct, HomeKit). Simple to implement, easy for non-technical users, secure enough for LAN-only. HomeKit uses a similar SRP-based PIN exchange (8-digit) — our 6-digit PIN with rate limiting is comparable security for the threat model
- **ECDSA P-256**: Already in use (CA cert exists). Best balance of security, library support, and performance. Ed25519 TLS cert support is inconsistent across nginx, MediaMTX, and Python ssl
- **5-year cert validity**: 1-year (current) is too short — requires frequent renewal on a home device. 10-year is lazy — no crypto rotation. 5-year with renewal reminder balances both
- **In-memory revocation**: With <10 cameras, a revocation set rebuilt from disk on startup is simpler and more reliable than CRL/OCSP infrastructure
- **Single pairing ceremony**: One flow establishes mTLS identity, OTA trust, and LUKS key material. Avoids three separate provisioning steps

## Alternatives Considered

### Pre-shared key (PSK)
Simpler but no per-camera identity. Can't revoke one camera without rotating the shared secret. Can't distinguish cameras in logs.

### QR code pairing
Camera would display QR code. Rejected — cameras are headless RPi Zero 2Ws with no display.

### mDNS auto-pair
Camera auto-pairs on discovery without admin confirmation. Security risk — any device advertising `_rtsp._tcp` would be trusted.

### Let's Encrypt / public CA
Requires internet access and public DNS. These devices are LAN-only. Self-signed CA is appropriate.

### SRP-6a (HomeKit-style)
Stronger than our PIN exchange — PIN never crosses the network. More complex to implement. For a home LAN where the exchange happens over HTTPS, the simpler PIN approach is sufficient.

## Consequences

- Camera cannot stream until paired — intentional, prevents rogue camera injection
- Pairing requires admin at dashboard AND PIN entry on camera — two-step, slightly more friction than auto-discovery
- All components (MediaMTX, nginx, OTA agent, health poller) must be mTLS-configured
- If server CA key is compromised (SD card theft), all camera trust is broken — mitigated by LUKS on `/data` (ADR-0010)
- Server is single point of trust — if server is lost, cameras must be re-paired to a new server (no CA backup in v1)
- The `pairing_secret` creates a coupling between ADR-0009 (pairing) and ADR-0010 (LUKS) — by design, simplifies provisioning
- Server cert renewal is automated via systemd timer — no manual intervention needed
