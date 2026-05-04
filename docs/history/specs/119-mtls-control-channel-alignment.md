# Feature Spec: Align Camera Control-Channel Auth With Documented mTLS Model

Tracking issue: #119. Branch: `feature/119-mtls-control-channel-alignment`.

## Title

Close the implementation/doc drift on the server-to-camera control
channel by enforcing **mutual** TLS in both directions: server
authenticates the camera (no more `ssl.CERT_NONE` on the outbound
client), camera authenticates the server (already enforced after #112
and #113). Bring `architecture.md`, `requirements.md`, and ADR-0015 §7
back into truth-with-the-code.

## Goal

Restate of issue #119. The product language and the docs say the
control channel is mTLS. Today the implementation is **half mTLS**:

- **Inbound (camera-side):** the camera already requires a CA-signed
  client cert on `/api/v1/control/*`. The source-IP fallback against
  `config.server_ip` was removed in the work for issue #112
  (`status_server.py:501-516`). After issue #113 lands, the control
  surface moves to its own listener (`:8443`) running
  `ssl.CERT_REQUIRED` so the TLS layer rejects anonymous peers before
  the HTTP handler even runs (`feature/113-camera-admin-control-split`,
  AC-2 / AC-4 in that spec). That direction is fixed.
- **Outbound (server-side):** the server's `CameraControlClient`
  pins `ctx.verify_mode = ssl.CERT_NONE`
  (`app/server/monitor/services/camera_control_client.py:48`). It
  presents `server.crt` to the camera, but it does **not** verify
  that the peer on the other end is the paired camera. A LAN-level
  attacker who could intercept or impersonate the camera's IP would
  be transparently trusted. The docstring at line 44 calls this out
  ("camera uses self-signed status cert") and the issue body's
  `docs/architecture.md:919-945` reference is exactly the doc claim
  this implementation contradicts.

This spec closes the outbound gap and aligns the docs. After this
spec ships:

1. The server **verifies** the camera's TLS server certificate on
   every control call. A peer presenting an unknown cert (any cert
   not pinned at pairing time) fails the TLS handshake; the HTTP
   layer is never reached.
2. The trust binding is **per-camera**: the server pins the camera's
   self-signed status cert at pairing time. Verification is "is this
   the same cert we accepted when the operator typed the PIN?" — not
   "did some CA sign it." This is correct for self-signed status
   certs and matches the threat model (one paired server, one camera,
   no third-party CA in the picture).
3. Source-IP is no longer load-bearing in **either** direction. It
   stays on the camera side as a defense-in-depth firewall rule
   (nftables; existing for `:443`, added for `:8443` in #113), not
   as application-layer auth.
4. ADR-0015 §7 ("Security hardening" — mTLS authentication) is
   **true as written** after this lands. `architecture.md` §6.4 and
   `requirements.md` rows that say "mTLS" stop being aspirational.

This is security-hardening / doc-truth work. It does **not** add a
user-facing feature, does **not** change the control parameter set
(ADR-0015 §4 preserved), does **not** weaken pairing (ADR-0009/PIN),
and does **not** introduce a new pre-auth surface (ADR-0022
preserved — the pinning data is captured during the existing
PIN-authenticated pairing exchange, not via any new endpoint).

This spec is **complementary to issue #113**, not blocking. #113 owns
the camera-side listener split and the move to `CERT_REQUIRED` on
the inbound control listener. #119 owns the symmetric outbound
hardening on the server side. The two land independently; #119 must
not re-spec the listener split. References to #113 in this doc are
read-only.

## Context

Existing code this change builds on, not replaces:

- `app/server/monitor/services/camera_control_client.py:38-56` —
  `_ssl_context()` builds `ssl.PROTOCOL_TLS_CLIENT` with
  `check_hostname = False` and `verify_mode = ssl.CERT_NONE`. This
  is the line being hardened. The `load_cert_chain(server.crt,
  server.key)` call below it stays exactly as is — outbound
  client-cert presentation is unchanged.
- `app/server/monitor/services/camera_control_client.py:138-145` —
  `_request()` builds `https://{camera_ip}{path}` with no port.
  After #113 ships, this same builder must target port 8443; that
  port migration is **owned by #113**. This spec assumes #113 has
  landed (or lands first); it does not introduce its own port
  decision.
- `app/server/monitor/services/pairing_service.py:80-249` — the
  existing pairing exchange. Already does an HTTPS round-trip from
  server to camera as part of the PIN handshake. This is the
  capture point: when the server completes the pairing exchange,
  it already has the camera's TLS server cert on the wire. Pinning
  is "save the cert we already saw." No new round-trip, no new
  endpoint, no new pre-auth surface (per ADR-0022 §6).
- `app/server/monitor/services/pairing_service.py:251-343` —
  `_generate_client_cert()`. Unchanged. The server still issues
  `{camera_id}.crt` (CA-signed, ECDSA P-256, 5-year validity) for
  the **client**-direction (camera→server) auth. This spec adds a
  separate per-camera **server**-direction trust artifact: the
  pinned status cert.
- `app/camera/camera_streamer/status_server.py:117-176` —
  `_ensure_tls_material()`. Camera generates a self-signed status
  cert at first boot (`status.crt` / `status.key`, ECDSA P-256,
  1825-day validity, SAN includes hostname + `.local` + `127.0.0.1`).
  **Unchanged by this spec.** The camera does not need to know
  whether the server has pinned the cert — the cert is publicly
  presented during the TLS handshake either way.
- `app/camera/camera_streamer/status_server.py:179-222` —
  `_wrap_https_server`. After #113 lands, the human listener runs
  `CERT_NONE` and the control listener runs `CERT_REQUIRED`. This
  spec does not touch either. The control listener's TLS context is
  unaffected.
- `app/camera/camera_streamer/status_server.py:501-516` —
  `_has_mtls_client_cert`. Already enforces presence of a peer cert
  validated by OpenSSL against `ca.crt`. **Unchanged.** The comment
  at line 510 ("Source-IP fallback was removed: a LAN attacker who
  could spoof `config.server_ip` could previously bypass auth")
  documents that the inbound side of issue #119 is already done.
- `app/server/monitor/models.py` — `Camera` dataclass. Gains one
  new field: `status_cert_fingerprint: str = ""` (lowercase hex
  SHA-256 of the camera's pinned status cert in DER form, 64 chars).
  Empty string = not yet pinned (legacy paired cameras pre-this-PR).
- `app/server/monitor/storage/cameras_store.py` (or whatever the
  current persister is — implementer confirms exact path during
  module impact mapping). Persists `status_cert_fingerprint` to
  `cameras.json` like every other Camera field.
- `docs/history/baseline/architecture.md:704-722` — the §6.4
  diagram and prose say "mTLS" without a qualifier. Today that is
  half-true. After this spec lands, the docs become accurate without
  a footnote.
- `docs/history/baseline/architecture.md:238-242` — threat-model
  rows. "Camera impersonation" mitigation says "mTLS camera pairing
  with client certs." After #119, the same mitigation actually
  applies to the **control** channel, not just the RTSPS push path.
- `docs/history/baseline/requirements.md:204` /
  `requirements.md:354` — TLS rows. Update to call out "server
  verifies camera peer cert via pin captured at pairing time."
- `docs/history/adr/0015-server-camera-control-channel.md:204-213` —
  §7 "Security hardening" already names mTLS. Add a paragraph that
  describes the **directional asymmetry between CA-signed
  client-direction and pinned server-direction** so the reasoning
  survives.
- ADR-0009 (mTLS pairing) — unchanged. The pinning is an additional
  per-camera fact captured during the same handshake; it does not
  rewrite the pairing protocol.
- ADR-0022 (no backdoors) — unchanged. The pinning capture happens
  during the PIN-authenticated pairing exchange, which is already a
  PIN-gated transaction. No new pre-auth surface, no new admin-only
  recovery path, no new "sudo to fix" recipe (§1, §2, §5 all
  satisfied as-is).
- Issue #112 (already closed/in-progress) — landed the camera-side
  source-IP fallback removal. This spec depends on its current
  state of `_has_mtls_client_cert` and assumes the comment in
  `status_server.py:501-516` accurately describes camera behavior.
- Issue #113 (in `ready-for-implementation`) — the listener split.
  After #113 lands, the camera's control listener runs at `:8443`
  with `CERT_REQUIRED`. This spec's port-aware client work assumes
  the constructor argument for port lands as part of #113's AC-8
  (`docs/history/specs/113-camera-admin-control-split.md`). If #113
  has not yet landed when this spec is implemented, the implementer
  rebases against `feature/113-camera-admin-control-split` first;
  see "Open Questions" OQ-1.

## User-Facing Behavior

### Primary path — operator (no observable change)

1. Operator pairs a camera via the existing PIN flow. Same UX, same
   PIN entry, same dashboard confirmation. The only new thing the
   server does internally is record the camera's status TLS cert
   fingerprint alongside the issued client cert.
2. Operator changes a stream parameter from the dashboard. Same UX,
   same response time. The control client, behind the scenes, now
   runs `CERT_REQUIRED` and verifies the camera peer cert against
   the pinned fingerprint.
3. Operator unpairs a camera. The pinned fingerprint is wiped along
   with the issued client cert. Same UX as today.
4. Operator re-pairs the same physical camera after a factory reset
   on the camera (which regenerates `status.crt`). The new cert is
   re-pinned during the new pairing exchange. The UX is identical
   to first-time pairing — the server treats this as a fresh pair.

### Failure states (must be designed, not just unit-tested)

These are the cases the issue body specifically called out as
"Add integration tests for: valid paired cert, missing cert, wrong
cert, spoofed-source / unexpected peer." Each gets a clear, testable
behaviour.

- **Camera presents the pinned cert (happy path).** Outbound TLS
  handshake completes; control call succeeds; existing
  `config_sync` path runs unchanged.
- **Camera presents an unknown cert** (e.g. operator reflashed the
  camera SD card, regenerating `status.crt`, but did not re-pair).
  Outbound TLS handshake fails with `ssl.SSLCertVerificationError`.
  `CameraControlClient._request` catches this, returns
  `(None, "Camera certificate mismatch — re-pair required")`.
  The dashboard surface (existing `config_sync = pending` path)
  shows a **distinct** state `config_sync = trust_lost` with an
  operator-readable hint: "Camera identity changed since pairing.
  Re-pair this camera to restore control." — see AC-7 below.
  This is the failure mode that today is **silent** (CERT_NONE
  accepts any cert); after #119 it is loud.
- **Spoofing peer on the same LAN** (rogue host with its own
  self-signed cert at `camera_ip`). Same as "unknown cert" path.
  TLS handshake fails. Control calls fail loudly. The attacker
  cannot make the server execute a control PUT against their host.
- **Camera unreachable** (TCP refused, timeout). Same as today —
  `URLError` → `(None, "Camera unreachable: ...")`. Unchanged. Not
  a trust event; no `trust_lost` state.
- **Pre-pin legacy camera** (paired before this spec; no pinned
  fingerprint on disk). On first control call after upgrade, the
  client falls back to **one-shot trust-on-first-use**: it captures
  the cert from the live handshake, persists the fingerprint to
  `cameras.json`, and proceeds. A single audit log line records
  the TOFU event with the captured fingerprint. After that, the
  camera is in the normal pinned state. **This is the only path
  by which a fingerprint is ever pinned without an explicit
  pairing exchange.** It exists solely so this PR is rollout-safe
  for already-paired cameras. See SC-119-3 + AC-9 + RC-119-2.
- **Server's `server.crt` rotated** (cert renewal under ADR-0009).
  Outbound mTLS still works because the camera trusts the **CA**,
  not the specific server cert. No new pinning is needed in that
  direction. (Asymmetry: server pins the camera's leaf, camera
  trusts the CA. This is intentional — see Risk RISK-119-3.)
- **Camera rotates its `status.crt`** (e.g. cert near expiry,
  factory reset). The rotation invalidates the pin. Operator must
  re-pair. We do **not** silently accept the new cert — silent
  rotation is exactly the spoofing case we are defending against.
  This is a **deliberate** UX cost; the cert has 5-year validity
  on the camera (`status_server.py:155`), so rotation should be
  rare.
- **`config_sync = trust_lost` is sticky.** Once entered, it is
  cleared only by a successful re-pair (which re-pins the cert) or
  by an explicit operator-driven "forget camera" action. No silent
  retry promotes the new cert. AC-8 covers this.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: `CameraControlClient._ssl_context()` returns an SSL context
  with `verify_mode = ssl.CERT_REQUIRED` and `check_hostname = False`
  whenever a pinned fingerprint exists for the target camera.
  **[unit: `app/server/tests/test_camera_control_client.py` —
  inspect the context object built for a camera with a non-empty
  `status_cert_fingerprint`]**
- AC-2: Calling `CameraControlClient.set_config(...)` against a
  camera whose live cert matches the pinned fingerprint succeeds.
  **[contract: ephemeral local HTTPS server in test that serves
  the same cert pinned in test fixtures, asserts 200 + parsed
  body]**
- AC-3: Calling any control method against a camera whose live cert
  does **not** match the pinned fingerprint fails with a non-empty
  error string equal to `"Camera certificate mismatch — re-pair
  required"`. The HTTP layer is **not** reached (no request body
  is sent, no audit-log line on the camera). **[integration:
  ephemeral HTTPS server with a *different* self-signed cert;
  assert the function returns the canonical error and that the
  camera's stub handler was never invoked]**
- AC-4: Calling any control method against a peer that presents
  no certificate (plain TCP) or a cert with a non-matching SAN
  fails with the same canonical error as AC-3. **[integration:
  raw TCP responder; assert TLS handshake fails before HTTP
  exchange]**
- AC-5: A camera that is **paired** but has no pinned fingerprint
  (legacy / pre-#119 row in `cameras.json`) triggers exactly one
  TOFU pin on the first successful control call. The fingerprint
  is then persisted; subsequent calls run in pinned mode (AC-1).
  The TOFU event emits one `CONTROL_TOFU_PIN` audit-log line with
  the camera_id and the captured fingerprint. **[unit + integration:
  start with `status_cert_fingerprint=""`, run one call, assert
  fingerprint populated; assert audit log line present; second call
  must NOT re-emit the event]**
- AC-6: `PairingService` exchange persists the camera's status cert
  fingerprint into the new `Camera.status_cert_fingerprint` field
  during a fresh pair. The fingerprint is the lowercase hex SHA-256
  of the cert in DER form (64 chars).
  **[unit: simulate the pairing exchange against a stub camera;
  assert `cameras.json` contains the expected fingerprint after
  pair completes]**
- AC-7: When `CameraControlClient` returns the canonical mismatch
  error (AC-3 / AC-4), `CameraService.update()` (or the equivalent
  caller — implementer confirms in module impact) sets
  `Camera.config_sync = "trust_lost"` and persists. The dashboard
  card surface (existing `config_sync` rendering) shows a
  human-readable hint: "Camera identity changed since pairing.
  Re-pair this camera to restore control."
  **[integration: end-to-end test from `CameraService.update` down
  to the JSON store; UI test optional but the rendered template
  should be checked for the hint string]**
- AC-8: `config_sync = "trust_lost"` is sticky: the next health
  poll / scheduled control call does **not** silently fall back to
  CERT_NONE, does **not** auto-update the pin, and does **not**
  silently retry. The state is cleared only by a successful
  pairing exchange (`PairingService.exchange()` resets it as part
  of writing the new fingerprint) or by operator-initiated unpair.
  **[unit: verify retry path keeps `trust_lost`; verify pair path
  clears it]**
- AC-9: Pre-#119 already-paired cameras (existing rows in
  `cameras.json` with `status_cert_fingerprint = ""`) keep working
  after upgrade. Exactly one TOFU pin per camera occurs on the
  first control call after upgrade (AC-5). No operator action is
  required for the upgrade.
  **[migration test: load a fixture `cameras.json` from before
  this PR, run one control cycle, assert exactly one pin event
  per camera and no `trust_lost` events]**
- AC-10: Unpairing a camera (existing flow) clears
  `status_cert_fingerprint` along with the issued client cert and
  cameras.json row.
  **[unit: existing unpair test extended to assert the field is
  removed]**
- AC-11: ADR-0015 §7 has a new paragraph (or note block)
  explicitly describing the directional asymmetry: "client-direction
  trust is CA-anchored (`{camera_id}.crt` signed by the server CA);
  server-direction trust is leaf-pinned (`status.crt` fingerprint
  captured at pairing). Both are mTLS; they use different trust
  models because the camera's status cert is self-signed and a
  per-camera CA round-trip would not add security over pinning."
  `architecture.md` §6.4 has a one-line update saying "server
  verifies camera peer cert via pin captured at pairing"; the row
  in the threat-model table that says "mTLS camera pairing with
  client certs" gains a sibling row covering the control channel.
  **[doc-link checker + manual review of the diff]**
- AC-12: Static check: `grep -rn "CERT_NONE" app/server/` returns
  **zero** matches in non-test code. (Tests may still construct
  CERT_NONE contexts intentionally as part of negative cases —
  scoping the assertion to non-test paths is part of the test.)
  **[CI lint: simple grep step in the test file or pre-commit]**
- AC-13: Validation matrix rows that apply (server Python, security
  path, traceability check) all pass on the resulting branch:
  `pytest app/server/tests/ -v`, `ruff check .`,
  `ruff format --check .`,
  `python tools/traceability/check_traceability.py`.
  **[CI: existing pipelines]**

## Non-Goals

- **Not changing the camera-side listener / TLS context.** That is
  #113. This spec must not edit `_wrap_https_server`,
  `_has_mtls_client_cert`, or `_require_mtls`.
- **Not moving to a CA-signed camera status cert.** A future ADR may
  decide to issue the camera's status TLS cert from the server CA at
  pairing time, replacing the self-signed cert. That is a bigger
  surgery (requires a new server-issued cert kind and a
  camera-side cert-replacement step) and is not necessary to close
  the gap #119 describes. See "Alternatives Considered" §C.
- **Not adding hostname / SAN-based identity check.** The pin is
  on the cert leaf itself; SAN is informational. We do not
  introduce hostname matching because the camera's IP is dynamic
  (DHCP) and the SAN today contains the hostname plus `.local` —
  not the IP we connect to. The pin is more precise than a SAN
  match would be anyway.
- **Not changing the camera→server (RTSPS push, OTA push,
  config-notify) auth.** Those already use mTLS with the
  CA-signed `{camera_id}.crt`; nothing to fix in that direction.
- **Not introducing a CRL or OCSP for status certs.** The pin
  *is* the revocation mechanism — re-pair invalidates it.
- **Not reworking ADR-0015 §3 or §4.** Routing and parameter set
  are owned by #113 and the existing ADR.
- **Not adding a new pre-auth surface to capture the fingerprint.**
  The capture happens during the existing PIN-authenticated
  exchange. Per ADR-0022 §1, a new pre-auth surface would require
  ADR review.
- **Not rewriting `pairing_service.py` end-to-end.** The change is
  additive: capture the cert fingerprint during the existing
  exchange, persist it, return it. No restructure of the pairing
  state machine.

## Module / file impact list

Concrete files and likely changes. The implementer may discover
small additions in adjacent files (logging glue, audit-log enum
entries) — those are in scope.

| File | Change |
|------|--------|
| `app/server/monitor/services/camera_control_client.py` | Replace `verify_mode = CERT_NONE` with pinned-fingerprint verification. New constructor argument `pin_provider` (callable taking `camera_id` → fingerprint or `""`). New canonical error string for mismatch. New TOFU pin path for legacy rows. |
| `app/server/monitor/services/pairing_service.py` | After successful exchange, capture the camera's TLS cert (DER), compute SHA-256 fingerprint, write into the Camera row alongside the issued client cert. |
| `app/server/monitor/services/camera_service.py` | Wire `pin_provider` into `CameraControlClient`; map the canonical mismatch error to `config_sync = "trust_lost"`. Clear `trust_lost` on successful re-pair. |
| `app/server/monitor/models.py` | Add `status_cert_fingerprint: str = ""` to `Camera`. Document new `config_sync` value `"trust_lost"`. |
| `app/server/monitor/storage/cameras_store.py` (path TBD by implementer) | Persist new field; migration path is "default empty string." No schema version bump needed — empty string is the legacy default. |
| `app/server/monitor/api/cameras.py` (or equivalent) | If the dashboard surface renders `config_sync`, add the new value to the rendered hint dictionary. |
| `app/server/monitor/templates/dashboard.html` (or partial) | One new banner / badge state for `trust_lost`. Same component as existing `config_sync = pending`, different copy + colour. |
| `app/server/tests/test_camera_control_client.py` | New test file (or extend existing). Cover AC-1 through AC-5, AC-9. Use ephemeral local HTTPS servers with controlled certs. |
| `app/server/tests/test_pairing_service.py` | Extend with AC-6: pairing captures fingerprint. |
| `app/server/tests/test_camera_service.py` (existing) | Extend with AC-7, AC-8, AC-10. |
| `docs/history/baseline/architecture.md` §6.4 + threat-model table | One-line + one-row updates per AC-11. |
| `docs/history/baseline/requirements.md` | Update TLS rows that reference the control channel; cite this spec. |
| `docs/history/adr/0015-server-camera-control-channel.md` §7 | Append directional-asymmetry paragraph per AC-11. |
| `docs/risk/` (existing risk register, path varies) | Add RISK-119-1 / RISK-119-2 rows; reference this spec. |
| `docs/cybersecurity/` (existing security control register) | Add SC-119-1 / SC-119-2 / SC-119-3 rows. |

`tools/traceability/check_traceability.py` will check that the new
REQ / RISK / SEC / TEST IDs in the changed source files are listed
in the matrix. No tooling changes.

## Validation Plan

Pull the applicable rows from `docs/ai/validation-and-release.md`'s
validation matrix:

- **Server Python.** `pytest app/server/tests/ -v`, `ruff check .`,
  `ruff format --check .`. Coverage gate is `--cov-fail-under=85`;
  the new tests must keep the existing pass.
- **Camera Python.** `pytest app/camera/tests/ -v`. Should be a
  no-op for #119 (no camera-side code change), but the pipeline
  has to stay green to prove non-regression.
- **API contract.** Existing camera-control contract suite should
  pass unchanged on the server side after the cert-verification
  switch — the contract is "GET / PUT control endpoints work";
  it doesn't care that the client now verifies the peer.
- **Security-sensitive path.** This is a security-sensitive path
  per `docs/ai/design-standards.md` ("Security-sensitive behavior
  must be explicit: auth, TLS, pairing, storage, and OTA should
  have clear contracts and tests"). Full relevant suite + smoke
  test required. The smoke test row that exercises `cameras.json`
  in a deployed image must show the new field round-tripping.
- **Requirements / risk / security / traceability / annotated code.**
  `python tools/traceability/check_traceability.py`. Required —
  this spec adds new traceability IDs.
- **Repository governance.** `python tools/docs/check_doc_map.py`,
  `python scripts/ai/check_doc_links.py`,
  `python scripts/ai/validate_repo_ai_setup.py`,
  `python -m pre_commit run --all-files`. The doc updates land in
  files the doc-map already tracks; no `doc-map.yml` edits needed.
- **Yocto config or recipe.** **Not applicable.** This change is
  pure server Python + docs. No Yocto rebuild, no image bump.
- **Hardware behavior.** Manual smoke verification on real paired
  hardware after deploy: pair a camera, confirm fingerprint is
  recorded; reflash camera SD to regenerate `status.crt`, confirm
  control calls fail with the canonical mismatch error and
  `config_sync = trust_lost` shows on the dashboard; re-pair,
  confirm `trust_lost` clears.

## Risk

ISO 14971-lite framing (per `docs/ai/medical-traceability.md`).
This is a security-hardening change in a home-security product, so
the relevant hazards are operational and trust-model hazards, not
patient-safety hazards.

| Hazard | Pre-control severity | Pre-control probability | Risk control |
|--------|---------------------|------------------------|--------------|
| **HAZ-119-1**: LAN-resident attacker spoofs the camera's IP and accepts forged control PUTs from the server, returning fake status to the dashboard. | High (operator believes the camera is paired and operational when it isn't) | Low pre-#119 (requires LAN access already), but **silent** today | RC-119-1 (server pins camera leaf cert; mismatch is loud, not silent — AC-3, AC-4) |
| **HAZ-119-2**: Documented mTLS claim diverges from implementation; future security review reads the docs and assumes the control channel is mTLS-protected when only one direction is. | Medium (drives downstream design decisions on a false premise) | Realised already (this issue exists) | RC-119-2 (doc updates per AC-11; ADR-0015 §7 paragraph; new threat-model row) |
| **HAZ-119-3**: Operator factory-resets the camera (regenerating `status.crt`) and silently keeps using the dashboard, not realising control commands are now flowing to a peer the server doesn't actually trust. | Medium (silent loss of control) | Medium (camera factory reset is a documented procedure) | RC-119-3 (`trust_lost` is loud and sticky — AC-7, AC-8; operator-readable hint that names the recovery action) |
| **HAZ-119-4**: Pre-#119 paired cameras break on upgrade because the new code path requires a fingerprint that was never captured. | High (operator loses control of all cameras on rollout) | Would be high without RC | RC-119-4 (TOFU pin on first call after upgrade — AC-5, AC-9; bounded to one event per camera, audit-logged) |
| **HAZ-119-5**: TOFU pin (RC-119-4) is itself silently exploited — an attacker who is on the LAN at exactly the moment the operator upgrades captures the pin instead of the real camera. | Low (very narrow window; requires upgrade-time LAN presence) | Very low | Accepted residual risk. Documented in audit log so operator can review pin events. Future evolution to CA-signed status certs (Alternatives §C) would close this; not in scope here. |

Risk-control summary: every loud-failure case (HAZ-119-1, HAZ-119-3)
is covered by AC-3, AC-4, AC-7, AC-8. The only remaining residual
risk (HAZ-119-5) is documented and accepted; the future-work hook
for closing it is identified.

## Security

This change touches sensitive paths, called out explicitly per
`docs/ai/roles/architect.md`:

- **`app/server/monitor/services/camera_control_client.py`** —
  outbound TLS verification mode. Direct touch.
- **`app/server/monitor/services/pairing_service.py`** — pairing
  exchange, certificate handling. Direct touch (additive: cert
  capture during the exchange).
- **`docs/cybersecurity/`** — security control register updates.
  Direct touch.
- **`docs/risk/`** — hazard register updates. Direct touch.

Threat-model deltas:

- **THREAT-119-1**: LAN-resident peer impersonates camera to the
  server. Today: succeeds silently because outbound CERT_NONE.
  After: TLS handshake fails; server emits mismatch event.
  Mitigation: SC-119-1 (pinned-leaf verification on every outbound
  control call).
- **THREAT-119-2**: Compromised CA (rare, but in scope per ADR-0009
  — the CA is locally generated and lives on the server). Today's
  inbound side relies on the CA; this spec does not change that
  reliance. The outbound side is **independent** of the CA — pinning
  the leaf means a CA compromise does not let an attacker mint a
  cert that the server's outbound client will trust as the camera.
  This asymmetry is **intentional** and is the one durable security
  benefit pinning gives over a per-camera CA-issued status cert.
  Documented in AC-11.
- **THREAT-119-3**: An attacker with momentary LAN presence at the
  exact instant of upgrade hijacks the TOFU pin. Mitigation:
  SC-119-3 (TOFU is one-shot and audit-logged; operator can review
  the captured fingerprint against the camera's actual cert via
  the camera's status page if they suspect tampering). Residual
  risk; documented.

Security controls introduced:

- **SC-119-1**: Server's `CameraControlClient` runs
  `verify_mode = CERT_REQUIRED` against a per-camera pinned leaf
  fingerprint. Implements outbound mTLS verification.
- **SC-119-2**: Pinning data is captured during the existing
  PIN-authenticated pairing exchange (no new pre-auth surface).
  Persisted in `cameras.json` as `status_cert_fingerprint` (hex
  SHA-256 of DER-encoded cert).
- **SC-119-3**: TOFU pin for legacy rows is one-shot, audit-logged,
  and bounded — exactly one fingerprint per camera, never
  re-pinned outside an explicit pairing exchange.

ADR-0022 ("No Backdoors") audit:

- Rule 1 (no auth-bypassing command/script/endpoint): satisfied —
  no new endpoint introduced; the cert pin is captured during the
  existing PIN-authenticated handshake.
- Rule 2 (pre-auth surfaces disclose nothing): satisfied —
  pre-auth surfaces unchanged.
- Rule 3 (lost-sole-admin recovery is hardware): satisfied —
  unaffected.
- Rule 5 (when in doubt, refuse): the TOFU exception is in the
  spec because refusing it would brick already-paired cameras on
  upgrade. The refusal-cost is bounded with one-shot, audit-logged
  capture.

## Traceability

Placeholder IDs the Implementer fills in (per
`docs/ai/medical-traceability.md`). Each touched code/test file
must carry at least one `REQ:` annotation per the standing rule.

Annotation block to add at the top of each newly modified
security-critical file (or extend existing block):

```
# REQ: SWR-119; ARCH: SWA-119; RISK: RISK-119-1, RISK-119-3;
# SEC: SC-119-1, SC-119-2, SC-119-3; TEST: TC-119-1..TC-119-13
```

ID space proposed for this spec (Implementer pins exact numbers
during traceability matrix update):

- **REQ:** `SWR-119` — "Server verifies camera control-channel
  peer cert via pinned-leaf fingerprint captured at pairing."
- **ARCH:** `SWA-119` — "Outbound control client uses
  CERT_REQUIRED with per-camera pinned trust anchor; pinning
  artifact captured during pairing exchange."
- **RISK:** `RISK-119-1`, `RISK-119-2`, `RISK-119-3` (mapped to
  HAZ-119-1, HAZ-119-2, HAZ-119-3 above; HAZ-119-4 / HAZ-119-5
  reuse RC-119-4 / accepted-residual labels rather than new
  RISK rows since they are rollout-mechanic risks).
- **SEC:** `SC-119-1`, `SC-119-2`, `SC-119-3`.
- **TEST:** `TC-119-1` through `TC-119-13` mapping 1:1 to
  AC-1 .. AC-13 above.

Each new ID must be added to the traceability matrix
(`tools/traceability/`) with links back to user need
**UN-camera-trust** (existing) or, if missing, a new user need
**UN-119-camera-control-trust** with a one-line description.

## Deployment Impact

- **Yocto rebuild needed?** No. Server-only Python + docs change.
  Image build pipeline is unaffected.
- **Camera firmware change?** No. The camera's status cert and
  TLS server config are untouched.
- **OTA path?** Server-only update. The existing
  server-package-deploy path applies.
- **Hardware verification?** Yes — manual smoke verification on a
  real paired camera (see Validation Plan §"Hardware behavior").
- **Migration?** Existing paired cameras get a TOFU pin on first
  control call after upgrade (AC-5, AC-9). No operator action
  required for the upgrade itself. Operators **may** review the
  audit log post-upgrade to confirm the captured fingerprints
  match what they expect.
- **Rollback?** Pure software rollback. The new `cameras.json`
  field defaults to empty string on older code, which the older
  code ignores. No data-loss risk on rollback.
- **Backwards compatibility window?** None required. After this
  PR, all server-to-camera control calls use CERT_REQUIRED. The
  camera does not need to be aware.

## Open Questions

- **OQ-1: Sequencing relative to #113.** This spec assumes #113
  has landed (or lands first) so the control client can target
  `:8443` with `CERT_REQUIRED` on both sides. If #113 slips,
  #119 can still ship — pinning works against the current
  unified `:443` listener too. The implementer should rebase
  against the latest `main` at start-of-work and pick the right
  port wiring then. **Non-blocking.**
- **OQ-2: Where is the dashboard `config_sync` rendered?** The
  implementer should confirm the exact template + JS surface that
  shows `pending` today and add the `trust_lost` state in the
  same spot. The product copy "Camera identity changed since
  pairing. Re-pair this camera to restore control." is the
  recommended string; a UX pass on shorter copy is welcome but
  not blocking. **Non-blocking.**
- **OQ-3: Should TOFU be opt-in via a settings flag?** A more
  conservative posture would require the operator to acknowledge
  the upgrade pin per camera. We are not proposing that — it
  trades a clear rollout cost for a marginal security gain
  (HAZ-119-5). If a reviewer disagrees, this OQ becomes a
  blocking decision and would change AC-5 / AC-9. **Non-blocking
  unless reviewer flips it.**
- **OQ-4: Should `trust_lost` block heartbeat reads as well as
  control writes?** Today `config_sync` is set on writes. The
  outbound `get_status` / `get_capabilities` calls also use
  `CameraControlClient` and will hit the same handshake failure.
  The spec leans toward "yes — every outbound `CameraControlClient`
  call inherits the pinned-trust requirement," because the
  alternative (trust the camera for reads but not writes) reopens
  the silent-impersonation hole. The implementer treats this as
  decided; if a reviewer wants the asymmetry, they raise it on
  the PR. **Non-blocking unless reviewer flips it.**

No question above is regulatory or hardware-redesign in nature,
so this issue does **not** need a `blocked` label per
`docs/ai/roles/architect.md`'s "When to label `blocked` instead
of designing."

## Implementation Guardrails

- **Don't change ControlHandler / `_require_mtls` / camera TLS
  context.** Those are #113. Camera-side code does not need any
  change for #119.
- **Don't add a new endpoint** to capture the fingerprint. The
  capture happens during the existing pairing exchange. New
  endpoint = ADR-0022 review = scope creep.
- **Don't move to a CA-signed camera status cert** in this PR.
  It is a clean future evolution (Alternatives §C); doing both at
  once expands the blast radius of the rollout and requires a
  camera-side change that this PR otherwise avoids.
- **Don't reuse `Camera.config_sync = "pending"`** for the trust
  failure. Operators conflate "pending" with "transient network
  blip." `trust_lost` is the correct word and the correct UX cue.
- **TOFU is one-shot.** The phrase "trust on first use" must
  appear in exactly one place in code (the legacy-row branch) and
  must produce exactly one audit-log line. If the implementer
  finds a reason to add a second TOFU site, escalate — that is a
  spec violation.
- **The PR description should propose updating the issue title** to
  emphasise the **outbound** scope ("server-to-camera outbound
  control client + doc alignment") so future readers don't read
  this issue as the camera-side IP-fallback removal (which #112
  / #113 already handled). This avoids overlap confusion with
  #113 in the same way that spec recommended for itself.

## Alternatives Considered

### A. Pinning the camera's self-signed status cert (chosen)

What this spec proposes. Captures the cert during the existing
PIN-authenticated pairing exchange. Server uses it as a
single-trust-anchor for outbound TLS to that camera. Loud failure
on rotation, audit-logged TOFU for legacy rows. Smallest blast
radius, no camera-side change, no new pre-auth surface, makes the
docs true today.

### B. Continue with CERT_NONE; accept LAN-trust model in docs

The issue body explicitly offers this as one of two options
("update docs and security claims to match reality"). Rejected
because:

- The product is a home-security product. "We trust everyone on
  the LAN by default" is not a posture this product can
  legitimately ship with for the control plane, even if it is the
  posture for some other LAN-only IoT (Tasmota, ESPHome).
- The asymmetry of "camera enforces, server doesn't" is the worst
  of both worlds — it makes the camera harder to test (handshake
  failures during dev are noisy) without giving the operator any
  actual security benefit.
- It would not satisfy the issue's "expected outcome" preference
  for the hardening route.

Documented in this section so it is not re-proposed as a
"simpler" alternative six months from now.

### C. CA-signed camera status cert (future, not now)

At pairing time, server issues a camera status TLS cert (in
addition to the camera client cert it issues today). Camera
replaces its self-signed `status.crt` with the new one. Server
then trusts the CA and matches the camera_id via SAN.

Why this is **the right long-term direction**:

- No per-camera trust anchor on the server — `ca.crt` covers all.
- New paired cameras don't need TOFU windows.
- Cert-rotation story is the same as server-cert renewal
  (ADR-0009 §1 has the systemd-timer pattern already).
- Aligns with how the server's own TLS cert is issued (CA-signed,
  5-year validity).

Why this is **not in #119**:

- Requires a camera-side cert-replacement step (write the new
  cert into `/data/certs/status.crt`, restart the listener), with
  a rollback path if the new cert is bad.
- Requires a new pairing-payload field for the issued status
  cert.
- Doubles the rollout surface for a security gain that pinning
  also provides (with one bounded TOFU window).
- Better as a clean follow-up ADR after #119 ships and we have
  experience with the pinned model in the wild.

Tracked as future work; not blocking this issue. A new follow-up
issue will be filed by the Implementer or Researcher once #119
lands.

### D. Use the camera's `{camera_id}.crt` (the **client** cert) as the server-side trust anchor

This is tempting — both directions could share the same artifact.
Rejected because:

- The camera does not present `{camera_id}.crt` from its TLS
  server (`status.crt` is a different cert). Making it do so
  would require the camera to use `{camera_id}.crt` for both the
  outbound client direction (to MediaMTX, to the server's
  config-notify endpoint) **and** the inbound server direction
  (its own status listener). The status listener already runs on
  a self-signed cert today; conflating them is a refactor.
- Even if we did the refactor, the SAN/CN of the issued client
  cert is `CN={camera_id}/O=HomeMonitor` with no SANs. We would
  need to add SANs to make it usable as a TLS server cert, which
  is exactly the work in §C.

Pinning sidesteps both problems — the cert *is* whatever
`status.crt` happens to be, and the server doesn't care about its
SAN content.

### E. Rely solely on the firewall (LAN segmentation)

The camera's nftables rules already allow inbound from the
server IP only. Argument: if the server IP is the only thing that
can talk to the camera's `:443`/`:8443`, why does the server
need to verify the camera at all?

Rejected because:

- The firewall protects the camera (inbound), not the server
  (outbound). A spoofing attacker can absolutely *receive* an
  outbound-from-server connection on the camera's IP.
- ADR-0022 §1 spirit ("the test is 'does this reduce the
  attacker's required sophistication?'") cuts the same way here:
  defense-in-depth is the rule, not "one layer is enough."
- Kept as a complementary control (the firewall stays as it is),
  not as a replacement.

## Cross-References

- Issue #112 — closed/in-progress: removed source-IP fallback
  from `_require_mtls`. Comment at
  `app/camera/camera_streamer/status_server.py:501-516`
  documents the camera-side state this spec depends on.
- Issue #113 / `feature/113-camera-admin-control-split` —
  listener split + `CERT_REQUIRED` on the inbound control
  listener. Complementary, not blocking.
- ADR-0009 — pairing + mTLS infrastructure. Unchanged.
- ADR-0015 §7 — gets the asymmetry paragraph (AC-11).
- ADR-0017 — on-demand streaming, calls
  `CameraControlClient.start_stream` /
  `stop_stream`. Same trust hardening applies; no separate work.
- ADR-0022 — no backdoors. This spec satisfies §1, §2, §3, §5
  without modification.
