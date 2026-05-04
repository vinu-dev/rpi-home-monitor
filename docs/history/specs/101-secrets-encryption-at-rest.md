# Feature Spec: Secrets at Rest — Risk-Disposition Close-Out for Issue #101

Tracking issue: #101. Branch: `feature/101-secrets-encryption-at-rest`.

## Title

Close out the "secrets stored unencrypted on the SD card" gap by
**adopting Option C (documented controls + per-secret hygiene + an
opt-in migration track) as the risk disposition for v1.4.x**, while
keeping the LUKS-on-`/data` work (Option A) on the existing
`docs/exec-plans/luks-post-pair-migration.md` (target 1.4.1) as the
implementation track. Reject Option B (per-secret wrapping inside the
plaintext `/data` partition) as architecturally redundant once
Option A lands and documented as such so a future agent does not
re-propose it. Ship the secrets-inventory runbook, the SC-005
control-statement update, the THREAT-005 status delta, the
traceability rows tying #101 to the SC/SEC/THREAT identifiers, a
narrow pre-commit guard against silently adding new persisted
secrets, and the smoke row that proves the inventory matches reality
on a real device.

## Goal

When an operator (or a future contributor) asks "What happens to my
secrets if my device walks away?" the answer is a single,
operator-readable page (`docs/operations/secrets-inventory.md`) that
lists every secret persisted on `/data` or shipped with the device,
classifies each one (`hashed` / `encrypted-at-rest` /
`plaintext-on-data` / `os-managed` / `in-memory-only`), names the
file and field, points at the threat-model row that captures the
residual risk, and links to the migration track that closes that
risk. The same page is what the implementer of the 1.4.1 LUKS
migration uses as its acceptance checklist (every row currently in
the `plaintext-on-data` column flips to `encrypted-at-rest` when the
migration ships).

The operator-visible promise is "the device tells me, in a single
page, exactly which secrets are at risk if my SD card walks away,
which are not, and what the plan to close the gap is." The
contributor-visible promise is "if I add a new secret to a settings
or models file, CI will fail until I either hash it, hold it in
memory, or add a row to the inventory with an explicit residual-risk
disposition." Today neither promise is met; this close-out PR makes
both true without shipping the (large, hardware-risk) LUKS migration
in the same change.

## Why this fits the mission

`docs/ai/mission-and-goals.md` calls out "trustworthy ... feels like
a real product, not a prototype." A device that silently leaks the
session-signing key, the camera pairing secret, the CA private key,
and the Tailscale auth key the moment its SD card is removed is the
opposite of "trustworthy" even when the runtime behaves correctly.
`docs/ai/design-standards.md` ("Security-sensitive behavior must be
explicit: auth, TLS, pairing, storage, and OTA should have clear
contracts and tests") puts storage on the same product-quality bar
as auth — this spec brings that bar to the at-rest-secrets surface.

`docs/ai/engineering-standards.md` § "Security: No Backdoors" is
authoritative on what we *won't* build:

- "No documented command, script, or endpoint that bypasses the
  primary auth mechanism."
- "Pre-auth surfaces never disclose internals."
- "Lost-access recovery is a hardware concern."
- "Admin-assisted recovery is fine when audited."

ADR-0022 ("No Backdoors in Authentication or Recovery") parks
TPM-backed recovery OTP as a future option ("Tracked as part of the
secrets-at-rest work (ADR pending, issue #101)"). This spec
*acknowledges* that parking but does NOT lift it: the RPi 4B and
Zero 2W have no TPM (ADR-0010 §"Hardware crypto performance"), and
introducing a software-only "recovery OTP" inside the plaintext
`/data` partition would be exactly the convenience-without-hardware
backdoor ADR-0022 forbids. The TPM track stays parked behind the
hardware refresh; this spec only commits to closing the gap with
controls available **today**.

ADR-0010 (LUKS Data Partition Encryption — *Accepted, implementation
in progress*) is the *design* for the at-rest control. Its
implementation rollout is in `docs/exec-plans/luks-post-pair-
migration.md` (target 1.4.1, opt-in only, container-tested before
hardware). This spec is the *risk-disposition close-out* that lets
us mark issue #101 as having an answer (controls documented +
inventory + linter + smoke + traceability), separate from the
calendar of when LUKS lands on every device.

`docs/exec-plans/luks-post-pair-migration.md` already lists the
operational mitigations for the LUKS migration (feature flag, atomic
snapshot, container loopback test, pre-flight checks). This spec
explicitly defers to that plan for the *what* and the *when* of
LUKS, and only adds the close-out artefacts that don't depend on
LUKS landing.

## Context

What is **already shipped** on `main` (not rebuilt by this spec):

- `docs/history/adr/0010-luks-data-encryption.md` — the design.
  Cipher (Adiantum), KDF (argon2id), per-board memory parameters,
  initramfs unlock paths, OTA-hook coupling with ADR-0008, recovery
  paths. **This spec does not redesign — it points to ADR-0010 as
  authoritative for any future implementer.**
- `meta-home-monitor/recipes-kernel/linux/linux-raspberrypi/adiantum.cfg`
  — kernel config fragment present (`CONFIG_CRYPTO_ADIANTUM=y` plus
  the NEON helpers, `CONFIG_DM_CRYPT=y`, `CONFIG_BLK_DEV_DM=y`).
  Wired via
  `meta-home-monitor/recipes-kernel/linux/linux-raspberrypi_%.bbappend`
  (annotated `REQ: SWR-046, SWR-050; RISK: RISK-018, RISK-019;
  SEC: SC-018, SC-019; TEST: TC-043, TC-044`).
- `meta-home-monitor/recipes-core/packagegroups/packagegroup-monitor-security.bb`
  — `cryptsetup` already in `RDEPENDS` for both server and camera
  images. The userland is in the build; what's missing is the
  service that calls it.
- `meta-home-monitor/wic/home-monitor-ab-luks.wks`,
  `meta-home-monitor/wic/home-camera-ab-luks.wks` — the alternate
  WKS files for the LUKS variant. Today they are documentation-only
  (the production `.wks` ships ext4 `/data` to keep
  `local-fs.target` happy on first boot — see the inline comment in
  `home-monitor-ab.wks:11`).
- `docs/exec-plans/luks-post-pair-migration.md` — the rollout plan
  for the actual encryption flow. Feature-flagged (`/data/config/
  luks-migration-enabled`), container-loopback tested, atomic
  snapshot + tarball backup before any destructive step, opt-in,
  CLI-only for 1.4.1, hardware validation deliberately deferred.
  **This is the implementation track for #101's Option A.** This
  spec is the *risk-acceptance* track that runs in parallel.
- `app/server/monitor/__init__.py:62` — `_load_or_create_secret_key`
  persists the Flask session-signing key as a 32-byte hex string at
  `/data/config/.secret_key`, mode 0o600. Plain text. Recently
  hardened to refuse to silently rotate on write failure (a separate
  correctness fix; the cleartext-on-disk gap is still open).
- `app/server/monitor/models.py:79` — `Camera.pairing_secret` (hex,
  per-camera HMAC key). Persisted plaintext in
  `/data/config/cameras.json`.
- `app/server/monitor/models.py:268` — `Settings.tailscale_auth_key`
  (Tailscale pre-auth key). Persisted plaintext in
  `/data/config/settings.json`.
- `app/server/monitor/models.py:285-293` — `Settings.offsite_backup_*`
  fields including `offsite_backup_secret_access_key`. Persisted
  plaintext in `settings.json`. Their docstring already states
  "Credentials are stored on the encrypted /data volume and are
  never returned in plaintext from API reads." — the second clause
  is enforced (`SettingsService` redacts on GET); the first clause
  is **aspirationally true today and only literally true after the
  LUKS migration lands**. This spec aligns the docstring with
  reality.
- `app/server/monitor/services/settings_service.py:65` — the
  `SECRET_FIELDS` allowlist that drives redaction on outbound API
  reads. Includes `tailscale_auth_key`. **This is the canonical
  list of "values we already know are secret"** — slice 1 of this
  spec turns it into the seed for the inventory page and the
  pre-commit linter.
- `app/server/monitor/services/config_backup_service.py:707` — the
  backup service already gates `tailscale_auth_key` (and similar)
  behind explicit `include_*` opt-in flags, scrubbing them out
  unless the operator confirms. The pattern for "treat this field
  as secret" already exists in code; this spec generalises it.
- `/data/certs/ca.key`, `/data/certs/server.key`, `/data/certs/
  cameras/cam-*.key` — mTLS private keys. Plain PEM, mode 0o600.
  ADR-0009 §1 documents the layout. Same physical-theft exposure
  as every other `/data` secret.
- `docs/cybersecurity/threat-model.md` THREAT-005 — "Device theft
  exposes recordings, WiFi, certs, and settings." Status: Draft.
  Linked controls: SC-005, SC-006. The control statement for SC-005
  in `docs/cybersecurity/security-risk-analysis.md:11` reads
  "Protect persistent secrets and recordings through `/data`
  design, restricted file permissions, and key-management
  procedures." The current literal control is "0o600 file mode +
  hope." This spec strengthens the SC-005 statement (still without
  *requiring* LUKS in 1.4.x, since the migration is opt-in; the
  control is "documented inventory + opt-in encryption track + per-
  field hygiene gate").
- `docs/cybersecurity/security-plan.md` § "Assets" — SEC-005
  ("Persistent config and logs") and SEC-003 ("Camera credentials
  and pairing secrets"). The asset descriptions are correct; what's
  missing is the per-row "current protection" column that the
  operator-facing inventory page will mirror.
- `docs/guides/admin-recovery.md` Case 2 — already documents that
  sole-admin recovery requires a hardware factory reset. Same
  underlying principle (no software backdoor, hardware is the
  recovery primitive) applies to the secrets-at-rest threat: an
  attacker with the SD card cannot be stopped by software once they
  have the bytes; we can only narrow the window before the bytes
  are useful (LUKS) and shorten the operator's rotation cycle once
  the breach is detected. This spec adds a "What to rotate after a
  suspected SD-card compromise" subsection to that guide.
- `app/server/monitor/services/audit.py` — emits structured audit
  events. Adds the `KEY_ROTATION_*` family below.

What is **missing** today and is this spec's concrete delivery:

- **Operator-readable inventory page** at
  `docs/operations/secrets-inventory.md` listing every secret on
  the device, its file/field path, classification, and the
  threat-model row it lives under. Matches the layout of the
  shipped `docs/operations/...` runbooks and is linked from
  `docs/doc-map.yml`.
- **Pre-commit guard** at `tools/secrets/check_persisted_secrets.py`
  that scans `app/server/monitor/models.py`,
  `app/camera/camera_streamer/models.py` (if present), and the
  `services/settings_service.py:SECRET_FIELDS` constant for new
  field-name patterns matching `*secret*`, `*password*`, `*token*`,
  `*key*` (excluding `*public_key*`, `*pairing_secret_hash*`,
  etc.), and refuses the commit unless either (a) the field is in
  the inventory page with a disposition row, or (b) the field is on
  an explicit `KNOWN_NON_SECRET` allowlist with a one-line
  justification. The list of suspect substrings + the allowlist
  format is fully documented in the script header so a future
  contributor can add a row without reading the spec.
- **`SECRET_FIELDS` exported as a module constant** —
  `services/settings_service.py:65` already has the list internally;
  the linter imports it as the source of truth so the inventory
  page and the linter can never drift. (The Implementer chooses
  the import path; the constraint is "single source of truth, not
  three.")
- **Threat-model + security-risk-analysis updates**:
  - `docs/cybersecurity/threat-model.md` THREAT-005 row — keep as
    Draft (mitigation is partial), but extend the "Security
    controls" cell with `SC-005, SC-006, SC-101` (new), and add a
    new line under § "Audit Events" for `KEY_ROTATION_*`.
  - `docs/cybersecurity/security-risk-analysis.md` SC-005 row —
    rewrite the control statement to: "Protect persistent secrets
    and recordings through (a) restricted file permissions on
    `/data`, (b) the secrets-inventory page that classifies every
    persisted secret and pins residual risk, (c) the pre-commit
    linter that refuses silent additions of new persisted secrets
    without an inventory row, (d) the opt-in LUKS migration
    (`docs/exec-plans/luks-post-pair-migration.md`) for full at-
    rest encryption on devices that opt in, and (e) the
    `KEY_ROTATION_*` audit events that record post-compromise
    rotation actions." Linked code adds the linter path and the
    inventory page.
  - Add a new control row SC-101 ("Key rotation tooling and
    runbook for post-compromise response — admin-callable rotation
    of Flask secret-key, Tailscale auth-key, and per-camera pairing
    secrets, with audit events and a documented rotation order"),
    linked to THREAT-005 and THREAT-016.
- **Audit events for rotation actions** — three new event names
  emitted by the existing `services/audit.py`:
  - `SECRET_KEY_ROTATED` (Flask session-signing key rotated; all
    sessions invalidated by side effect)
  - `TAILSCALE_AUTH_KEY_ROTATED` (settings field cleared or
    replaced via the existing settings flow — the rotation is the
    operator pasting a new key from the Tailscale admin console;
    the audit row records that it happened, not the key value)
  - `CAMERA_PAIRING_SECRET_ROTATED` (covered by the existing
    unpair → re-pair flow; the audit row is the explicit emit
    that the unpair path triggers, so a rotation event is
    distinguishable from an admin-removes-camera-permanently
    event)
  Each is added to the docstring catalogue at
  `services/audit.py:8` (matching the AC-7 pattern in spec
  `99-admin-password-reset.md`) and emitted from the
  corresponding settings / pairing / cert flow that already
  exists. This is the **minimum** rotation surface the close-out
  PR ships; the actual rotation **CLI tool** is out of scope (it
  is an admin-recovery surface and would re-introduce the
  ADR-0022 backdoor risk if not designed end-to-end; this spec
  defers it).
- **Admin-recovery doc subsection** — append a new Case 3 to
  `docs/guides/admin-recovery.md`: "Suspected SD-card compromise
  / device theft." Steps: (1) revoke all camera certs (existing
  unpair flow on each row, mTLS rejects the old certs), (2)
  rotate the Tailscale auth key in the Tailscale admin console
  and clear `Settings → Network → Tailscale → Auth key` to force
  re-paste of a fresh value, (3) reflash the SD card and run
  setup wizard, (4) re-pair every camera (which mints fresh
  `pairing_secret` and fresh per-camera mTLS certs), (5) reset
  every user password via the Settings → Users flow shipped in
  #99 (since the bcrypt hashes were on the stolen card, even
  though they're not directly reversible they may have been
  brute-forced offline). Recommend operators also rotate the
  WiFi PSK at the router (NetworkManager wrote it to
  `/etc/NetworkManager/system-connections/<ssid>.nmconnection`
  in plain text — OS-level concern; documented, not patched).
  No new code; this is operator guidance.
- **Smoke-test row** that exercises the inventory page on real
  hardware: a script that lists every file under `/data/config/`
  matching the secret-substring pattern (`secret`, `password`,
  `token`, `key`) and asserts that every match either (a) appears
  in the rendered Markdown of `secrets-inventory.md` as a `field:
  <path>` line, or (b) is on the same `KNOWN_NON_SECRET`
  allowlist as the linter. Catches drift between docs and reality
  at deploy time.
- **Doc-map registration** — `docs/doc-map.yml` gains
  `docs/operations/secrets-inventory.md` under the operations
  group so `python tools/docs/check_doc_map.py` doesn't fail and
  agents that read the doc map find the new page.
- **CHANGELOG line** — one bullet under Unreleased: "Document
  every secret persisted on `/data` (`docs/operations/secrets-
  inventory.md`); add pre-commit linter that refuses silent
  additions of new persisted secrets; document
  `KEY_ROTATION_*` audit events. The opt-in LUKS migration
  (`docs/exec-plans/luks-post-pair-migration.md`) remains the
  encryption track for 1.4.1 and is unchanged by this PR."

ADRs that frame the work (none introduced by this spec; references
only):

- ADR-0010 (LUKS data encryption) — the design this spec defers to.
  **Untouched by this PR.**
- ADR-0009 (camera pairing & mTLS) — owns `pairing_secret` and the
  per-camera key material. The unpair flow's existing audit emit
  is the seat the new `CAMERA_PAIRING_SECRET_ROTATED` event lives
  on.
- ADR-0022 (no backdoors) — already parks TPM-backed recovery OTP
  as future work behind the hardware refresh and explicitly cites
  issue #101 as the secrets-at-rest tracker. This spec confirms the
  parking and adds no new authentication / pre-auth surface.
- `docs/exec-plans/luks-post-pair-migration.md` — the rollout plan.
  Status pointer: this spec adds a one-line cross-reference at the
  top of that file ("Risk-disposition close-out for #101 lives in
  `docs/history/specs/101-secrets-encryption-at-rest.md`. This
  exec plan is the implementation track; the spec is the
  risk-acceptance track.").

## User-Facing Behavior

This spec ships **no runtime UX surface change** — no new admin
control, no new dashboard tab, no new API endpoint that an operator
clicks today. Everything it ships is either a documentation surface
(operator-readable runbook + admin-recovery case + threat-model
row), a CI / pre-commit guardrail, or a smoke-row check on real
hardware. The user-facing surface is the **inventory page itself**
(operators read it once, after a deploy, to understand what's
protected and what isn't, and to plan their physical-security
posture accordingly), and the **documented Case 3 in admin-
recovery.md** (operators read it once, after a suspected
compromise, to know what to rotate and in what order).

### Primary path — operator audits "what's at risk on my device today"

1. Operator goes to `docs/operations/secrets-inventory.md` (linked
   from `docs/README.md` operations subsection and from
   `docs/doc-map.yml`).
2. Sees a table with one row per secret persisted on the device:
   columns are *Asset*, *File / field*, *Classification*, *Linked
   threat*, *Linked exec plan / mitigation*. Classifications:
   - `hashed` — bcrypt hash, brute-force-resistant; physical
     access does not yield the cleartext.
   - `encrypted-at-rest` — LUKS-protected (only after the operator
     opts in to the migration; the row says so explicitly).
   - `plaintext-on-data` — readable from the SD card today;
     mitigated only by file permissions (which an attacker with
     the raw block device bypasses). Linked threat: THREAT-005.
   - `os-managed` — written to disk by an OS subsystem outside the
     app's control (e.g., NetworkManager WiFi PSK). The mitigation
     is operator-side (rotate at the router).
   - `in-memory-only` — never persisted (e.g., the WiFi password
     during first-boot provisioning, per
     `provisioning_service.py:11`).
3. Operator now knows exactly which secrets to rotate / re-issue /
   re-pair if the device is stolen, and can decide whether to
   opt in to the LUKS migration. The page is also what a security
   reviewer reads to assess the device's physical-security posture
   without grepping the codebase.

### Primary path — operator responds to a suspected compromise

1. Operator suspects the SD card was removed (theft, repair tech,
   ex-partner with brief access). Opens
   `docs/guides/admin-recovery.md` Case 3.
2. Follows the rotation order documented there: revoke camera certs
   → rotate Tailscale auth key → reflash SD → run setup → re-pair
   each camera → reset every user password (#99 flow) → rotate
   WiFi PSK at the router.
3. Audit log on the **new** device shows `SECRET_KEY_ROTATED` (on
   first boot of the reflashed image, since the new
   `_load_or_create_secret_key` writes a fresh key — the existing
   code path needs only the new audit emit), and per-camera
   `CAMERA_PAIRING_SECRET_ROTATED` events as each camera re-pairs.
   The old device's audit log is on the stolen card and assumed
   compromised; the new device's log gives the operator a clean
   trail of the rotation actions.

### Primary path — contributor adds a new secret to the codebase

1. Contributor adds `Settings.new_provider_api_key: str = ""` to
   `app/server/monitor/models.py`.
2. `pre-commit` runs `tools/secrets/check_persisted_secrets.py`,
   which detects the new field, sees its name matches the suspect
   pattern, finds no matching row in `secrets-inventory.md`, and
   no entry in the script's `KNOWN_NON_SECRET` allowlist.
3. Commit is refused with a message:
   *"app/server/monitor/models.py:NN — `new_provider_api_key`
   looks like a persisted secret. Either (a) add a row to
   `docs/operations/secrets-inventory.md` with classification +
   linked threat, or (b) hold the value in memory only (do not
   put it on a `Settings`/`Camera`/`User` dataclass field), or
   (c) add it to KNOWN_NON_SECRET in this script with a one-line
   justification. See `docs/history/specs/101-secrets-encryption-
   at-rest.md` § 'Pre-commit guard'."*
4. Contributor either updates the inventory or the allowlist;
   commit succeeds; the inventory page (and the smoke check that
   reads it) stays current.

### Failure states

- **Operator reads the inventory before any LUKS migration is
  available.** Page makes the gap explicit ("classification:
  plaintext-on-data; mitigation: opt-in LUKS migration on roadmap
  for 1.4.1; until then, treat physical access as full
  compromise"). No false sense of security.
- **Operator reads the inventory after opting in to LUKS
  migration.** The classification of every `/data/config/*`
  field flips to `encrypted-at-rest`. The mitigation cell points
  at the LUKS exec plan's "if you forgot the passphrase" recovery
  section. The implementer who lands the LUKS migration owns the
  inventory-page update at the same time (per AC-9).
- **Linter false positive** — a contributor adds a public key
  field whose name contains `key`. The contributor adds the field
  name to `KNOWN_NON_SECRET` with a one-line justification ("CA
  certificate public part — published, not secret"). Commit
  proceeds. Pattern: false positives are explicit and reviewed,
  not silently bypassed.
- **Linter false negative** — a contributor adds a secret in a
  module the script doesn't scan (e.g., a future `app/camera/
  camera_streamer/models.py` if/when it grows persisted state).
  Mitigated by the smoke-row check that scans the *runtime*
  `/data/config/` tree on a real device — drift between code
  scope and runtime scope is caught at deploy. Documented as
  OQ-2.
- **Smoke check finds a `/data/config/*.json` field whose name
  matches a secret pattern but isn't in the inventory.** Smoke
  fails, deploy halts, the contributor either updates the
  inventory or refines the pattern. Same shape as the linter.
  No silent fallthrough.
- **Audit emission fails for `SECRET_KEY_ROTATED` etc.** Same
  trade-off as elsewhere in the audit pipeline — the rotation
  succeeds, the missing log row is the operational gap.
  HAZ-101-3 risk-controls this.
- **Operator opts in to the LUKS migration AND it fails the
  pre-flight on their device.** Out of this spec's scope — the
  exec plan owns that failure mode (atomic snapshot, abort with
  audit log, no destructive action). This spec only points at the
  exec plan as the implementation track.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: `docs/operations/secrets-inventory.md` exists, listing
  every persisted secret currently on the device. At a minimum
  it covers (each as its own row): the Flask
  `/data/config/.secret_key` (cleartext), `cameras.json`
  `pairing_secret` per camera (cleartext), `settings.json`
  `tailscale_auth_key` (cleartext), `settings.json`
  `offsite_backup_secret_access_key` (cleartext), `/data/certs/
  ca.key` (cleartext PEM), `/data/certs/server.key` (cleartext
  PEM), per-camera `/data/certs/cameras/cam-*.key` (cleartext
  PEM), `users.json` `password_hash` (bcrypt — `hashed`
  classification, NOT a residual risk), `users.json`
  `recovery_code_hashes` (bcrypt — `hashed`), `users.json`
  `totp_secret` (cleartext but TOTP secrets are intrinsically
  device-bound and ADR-0011 already accepts this for a
  non-encrypted boot — flagged, not new), and the
  NetworkManager `<ssid>.nmconnection` WiFi PSK (`os-managed`).
  Each row has classification + linked threat + linked
  mitigation columns.
  **[doc review during PR; rendered Markdown checked in]**
- AC-2: `tools/secrets/check_persisted_secrets.py` exists, is
  wired into `.pre-commit-config.yaml` as a fast hook (under
  one second on a clean tree), and refuses any new dataclass
  field on `app/server/monitor/models.py:Settings`,
  `:Camera`, `:User`, or `services/settings_service.py:
  SECRET_FIELDS` whose name matches the secret-substring
  pattern unless the field is in the inventory page or in the
  script's `KNOWN_NON_SECRET` allowlist with a one-line
  justification. The script's header docstring documents the
  pattern, the allowlist format, and the false-positive
  workflow.
  **[unit tests for the linter under `tools/secrets/tests/
  test_check_persisted_secrets.py` covering: positive (new
  secret without inventory row → exit non-zero), negative
  (new public field → exit zero), allowlist (justified
  exception → exit zero), inventory-row hit (declared field
  → exit zero), source-of-truth import from `SECRET_FIELDS`
  (renaming a field updates both)]**
- AC-3: `services/audit.py` emits `SECRET_KEY_ROTATED`,
  `TAILSCALE_AUTH_KEY_ROTATED`, and
  `CAMERA_PAIRING_SECRET_ROTATED` from the corresponding
  rotation paths:
  - `__init__.py:_load_or_create_secret_key` emits
    `SECRET_KEY_ROTATED` *only* when it generates a new key
    (i.e., on first boot or after the file was deleted); a
    successful read of an existing key emits nothing (no log
    spam on every restart).
  - `services/settings_service.py` emits
    `TAILSCALE_AUTH_KEY_ROTATED` when an admin PUTs a new
    `tailscale_auth_key` (or clears it), with the new value
    redacted from the audit payload.
  - `services/pairing_service.py` (or the existing unpair
    handler) emits `CAMERA_PAIRING_SECRET_ROTATED` on the
    unpair → re-pair sequence — the existing
    `CAMERA_REMOVED` event captures the unpair half; the new
    event is the *paired-fresh* half so a rotation is
    distinguishable from a permanent removal.
  Each event payload includes `actor_user`, `actor_ip` (where
  applicable), and the asset id (`tailscale`, camera id, etc.).
  No event payload contains the secret value.
  **[unit tests on each of the three emitting paths, asserting
  event name + payload shape + redaction]**
- AC-4: All three event names appear in the docstring catalogue
  at `services/audit.py:8` (matching the AC-7 pattern in
  `99-admin-password-reset.md`). The same catalogue is what
  the audit-export schema reviewer (#247) reads.
  **[grep test in the audit suite asserting the three event
  names are in the docstring]**
- AC-5: `docs/guides/admin-recovery.md` Case 3 exists with the
  rotation steps in the order documented above (revoke camera
  certs → rotate Tailscale auth key → reflash → re-setup →
  re-pair → reset user passwords → rotate WiFi PSK). Each
  step references the existing UI control (Settings → Network
  → Tailscale, Settings → Cameras → Unpair, Settings → Users →
  Reset password) and the audit event the step emits. The
  page also explicitly documents that the WiFi PSK is OS-
  managed and that the device cannot rotate it on the
  operator's behalf.
  **[doc review; manual link check]**
- AC-6: `docs/cybersecurity/security-risk-analysis.md` SC-005
  row's control statement is rewritten per the Context
  section above; the row also gains a new SC-101 entry below
  it with its own statement. THREAT-005 in
  `docs/cybersecurity/threat-model.md` is updated to list
  `SC-005, SC-006, SC-101` in the "Security controls" cell
  and stays Status: Draft (mitigation is partial pending
  LUKS).
  **[doc review; `python tools/traceability/check_traceability.py`
  passes after the matrix update in AC-8]**
- AC-7: `docs/operations/secrets-inventory.md` is registered
  in `docs/doc-map.yml` under the operations group, with
  category `runbook` (or whatever category the existing
  `docs/operations/*.md` runbooks use — the implementer
  matches the convention rather than inventing a new one).
  `python tools/docs/check_doc_map.py` and
  `python scripts/ai/check_doc_links.py` both pass.
  **[doc-map check; doc-link check]**
- AC-8: `docs/traceability/traceability-matrix.csv` gains
  rows linking `UN-101 → SYS-101 → SWR-101-{A,B,C}` to the
  inventory page, the linter, the new audit-event emit
  sites, and Case 3 of admin-recovery.md. New IDs
  introduced: `SEC-018` ... no — *all SEC-### in the 1-17
  range are already taken in `security-plan.md`*; the
  implementer either picks the next free number (SEC-018 if
  free at PR time, otherwise the next gap) or extends the
  description of SEC-005 to mention the secrets-inventory
  artefact. **Concrete IDs are an Implementer call** to
  avoid clashing with any in-flight spec; the placeholders
  in this spec (UN-101, SYS-101, SWR-101-A/B/C, SC-101,
  THREAT-101, RISK-101-1..3, RC-101-1..3, TC-101-AC-1..14)
  are the contract. The Markdown summary
  (`docs/traceability/traceability-matrix.md`) gets a
  one-line entry under the appropriate row.
  `python tools/traceability/check_traceability.py` passes.
  **[traceability checker]**
- AC-9: `docs/exec-plans/luks-post-pair-migration.md` gains a
  one-line cross-reference at the top: "Risk-disposition
  close-out for issue #101 is at
  `docs/history/specs/101-secrets-encryption-at-rest.md`.
  This exec plan is the implementation track; the spec is
  the risk-acceptance track. When the LUKS migration
  ships, every `plaintext-on-data` row in the inventory
  page MUST flip to `encrypted-at-rest` in the same PR."
  No other content change to the exec plan in this PR.
  **[doc review]**
- AC-10: Smoke-test row (added to `scripts/smoke-test.sh` or
  the row list it consumes): on a real device, list every
  file under `/data/config/`, JSON-parse each, walk all
  string values whose key matches the secret-substring
  pattern, and assert each is either (a) named in
  `secrets-inventory.md` as a `field:` row or (b) on the
  smoke-script's own `KNOWN_NON_SECRET_RUNTIME` allowlist
  (which mirrors the linter's allowlist). Smoke fails the
  deploy if drift is detected.
  **[`scripts/smoke-test.sh` row addition; manual run on
  hardware in PR review]**
- AC-11: The CHANGELOG Unreleased section gains the bullet
  documented in Context above. The bullet must explicitly
  say the LUKS migration is unchanged by this PR and link
  the exec plan, so a future operator reading the CHANGELOG
  doesn't conclude "encryption is on" from a docs-only PR.
  **[doc review; existing CHANGELOG-format checker if one
  exists, else manual]**
- AC-12: `services/settings_service.py:SECRET_FIELDS` is
  exported as the source of truth (importable from a
  module-level constant); the linter and the inventory page
  both reference it (the inventory page either generates or
  hand-mirrors the list, with a comment pinning the source).
  Renaming a field in `SECRET_FIELDS` causes the linter to
  fail until the inventory row is updated. (Avoids three
  separate hand-maintained lists.)
  **[unit test asserts that adding a new entry to
  `SECRET_FIELDS` without a corresponding inventory row
  fails the linter]**
- AC-13: ADR-0010 is **not modified** by this PR. The exec
  plan reference in AC-9 is the only edit to the LUKS-track
  documents. (The Architect for the eventual LUKS migration
  will revisit ADR-0010 with implementation evidence; this
  spec stays out of that lane.)
  **[grep test in CI: this PR's diff does not touch
  `docs/history/adr/0010-luks-data-encryption.md`]**
- AC-14: The pre-commit linter runs in CI and fails the PR
  if it detects an undeclared secret. The CI step name and
  invocation are added to the appropriate workflow file
  (the Implementer picks the right workflow — the project
  already has a pre-commit job that runs `pre_commit run
  --all-files`; if the new hook is a pre-commit hook, no
  separate CI step is needed). The validation matrix entry
  in this spec (see Validation Plan) names the command
  the implementer cites in the PR's depot rule gate.
  **[CI run on the close-out PR; depot rule gate row]**

## Non-Goals

- **Slice A — actually shipping LUKS-on-`/data` in this PR.** The
  rollout is genuinely 2–3 days of focused engineering plus
  hardware validation (per the existing exec plan); shipping it
  in this PR would either bypass the safety mitigations (atomic
  snapshot, container loopback test, opt-in flag) or grow this
  PR into the full 1.4.1 work. The exec plan is correct, mature,
  and ready to be picked up by the next implementer; this spec
  does not duplicate or pre-empt it. The risk-disposition
  close-out delivered here is the *parallel* track that lets
  #101 record an answer on the project board without waiting on
  the calendar of when LUKS lands.
- **Slice B — per-secret in-memory wrapping inside the plaintext
  `/data` partition (Option B in the issue body).** Rejected.
  Without disk encryption, every per-secret wrap requires a
  *wrapping key* that itself has to live somewhere — either on
  the same SD card (no security gain), in a TPM (we don't have
  one), or derived from device identity like the CPU serial
  (defeated by the same attacker who reads the SD card and the
  CPU serial together, which is trivial). Once Option A (LUKS)
  ships, every per-secret wrap inside the encrypted volume is
  pure overhead. The only scenarios where Option B adds value
  are (a) when the user has opted in to the LUKS auto-unlock
  keyfile (defence in depth against the keyfile-on-same-card
  trade-off ADR-0010 §"Auto-unlock option" already calls out)
  and (b) before Option A ships at all. Scenario (a) is
  marginal and addressed by the operator choosing not to enable
  auto-unlock; scenario (b) is the time window we are
  intentionally short-circuiting with the inventory page +
  rotation runbook + audit events + LUKS exec plan, rather
  than building two parallel encryption schemes. Rejected, with
  rationale here so a future agent finds the answer rather than
  re-deriving it. (See SEC-101-F.)
- **TPM-backed recovery OTP.** Parked by ADR-0022 behind the
  hardware refresh; reaffirmed here. Not in scope until the
  device gains a TPM (Pi 5+ with TPM header, or a USB / I²C
  TPM module — out of scope for the current BOM).
- **An admin-callable rotation CLI.** A `monitor rotate-secret
  <name>` kind of tool. Specifically rejected — it would be the
  ADR-0022 "documented command that bypasses the primary auth
  mechanism" by another name (the secret IS the auth surface
  for whatever it protects). Rotation in this spec is via
  existing UI surfaces (Settings → Network → Tailscale → Auth
  key for Tailscale; Settings → Cameras → Unpair / Pair for
  per-camera secret; first-boot generation for the Flask key;
  reflash for the lot in the suspected-compromise case). The
  audit events make those rotations *observable*; they do not
  add a new rotation surface.
- **Encryption of the `/data/recordings/` tree.** Recordings
  are not on the issue body's exposure list and the threat
  model rates them as Sensitive but not Authentication-Bypass.
  They will inherit at-rest encryption when the LUKS migration
  lands (since `/data` is the encrypted volume); they do not
  warrant a separate per-file scheme.
- **WiFi PSK protection.** Standard NetworkManager behaviour
  (writes plaintext to `/etc/NetworkManager/system-connections/
  *.nmconnection` on the **rootfs** partition, not `/data`).
  Out of scope as a code change — documented in the inventory
  page as `os-managed`, documented in admin-recovery.md Case
  3 as a router-side rotation, no app-layer mitigation.
- **A schema-versioning bump for `users.json` / `cameras.json`
  / `settings.json`.** No new fields are introduced.
- **Disabling default admin / changing the first-boot UX.**
  Out of scope; tracked under #136 / setup-wizard work.
- **A new "What's at risk?" page on the dashboard.** The
  inventory is a developer / advanced-operator artefact; a
  dashboard tile would either oversimplify (false reassurance)
  or duplicate the runbook (drift). Reconsider when the LUKS
  migration ships and the operator decision becomes "auto-unlock
  on or off" (a real product decision); today the operator
  decision is "read this and understand your physical-security
  posture," which is a doc.

## Module / File Impact List

**No production runtime behaviour change** beyond the three new
audit emits. All other changes are docs, traceability, linter,
smoke row, and CHANGELOG.

**New files:**

- `docs/operations/secrets-inventory.md` (operator-readable
  classification page; sections per AC-1).
- `tools/secrets/check_persisted_secrets.py` (pre-commit linter;
  header docstring documents pattern + allowlist + workflow).
- `tools/secrets/tests/test_check_persisted_secrets.py` (linter
  unit tests per AC-2).

**Modified code:**

- `app/server/monitor/__init__.py:62` —
  `_load_or_create_secret_key`: emit `SECRET_KEY_ROTATED` audit
  event in the *write* branch (i.e., when generating a fresh key),
  using whatever audit handle is reachable from the app factory at
  that point. If the audit logger is not yet constructed at this
  point in the boot sequence (likely — secret-key load happens
  before service wiring), fall back to a deferred emit: stash a
  flag on the app config and let the audit service emit
  `SECRET_KEY_ROTATED` once it comes up. Implementer's call on
  the cleanest plumbing; the contract is "exactly one audit event
  per fresh-generation event, zero on every existing-file load."
  Add `# REQ: SWR-101-A; SEC: SC-005, SC-101; TEST: TC-101-AC-3`
  annotation block above the function.
- `app/server/monitor/services/settings_service.py` —
  in the `tailscale_auth_key` write path (around the existing
  validation at line ~530), emit
  `TAILSCALE_AUTH_KEY_ROTATED` with the new value redacted. The
  field is already in the `SECRET_FIELDS` redaction list; the
  audit emit is the new artifact. Annotation:
  `# REQ: SWR-101-A; SEC: SC-005, SC-101; TEST: TC-101-AC-3`.
  Also export `SECRET_FIELDS` (or the list at line 65) as a
  module-level constant if not already so importable, per
  AC-12.
- `app/server/monitor/services/pairing_service.py` (and/or the
  unpair handler in `app/server/monitor/services/cert_service.py`
  — Implementer chooses which; the audit event lives at the
  *re-pair* moment, not the unpair moment, so the natural seat
  is wherever a fresh `pairing_secret` is minted on a successful
  re-exchange after a prior unpair) — emit
  `CAMERA_PAIRING_SECRET_ROTATED`. Annotation:
  `# REQ: SWR-101-A; SEC: SC-005, SC-101; TEST: TC-101-AC-3`.
- `app/server/monitor/services/audit.py:8` — extend the
  docstring catalogue to list the three new events
  (`SECRET_KEY_ROTATED`, `TAILSCALE_AUTH_KEY_ROTATED`,
  `CAMERA_PAIRING_SECRET_ROTATED`). Optionally export each as a
  module-level constant near the existing `CLIP_TIMESTAMP_*`
  block; defer if it churns too many sites (matches the OQ-1
  pattern from spec `99-admin-password-reset.md`).
- `app/server/monitor/models.py:285-293` — adjust the docstring
  on `Settings.offsite_backup_*` to read "Credentials are
  stored on the `/data` volume; full at-rest encryption depends
  on the operator opting in to the LUKS migration
  (`docs/exec-plans/luks-post-pair-migration.md`). API reads
  always redact." The current docstring's "encrypted /data
  volume" claim is aspirationally true; align with reality.
  No code change; doc-string only. Annotation block update if
  any (Implementer's call — the file already carries an
  extensive `REQ:` block on line 1).

**New tests:**

- `app/server/tests/unit/test_audit_secrets_at_rest.py` —
  covers AC-3, AC-4:
  - `test_secret_key_rotated_emitted_on_fresh_generation_only`
  - `test_secret_key_not_emitted_on_existing_file_load`
  - `test_tailscale_auth_key_rotated_emitted_on_settings_put`
  - `test_tailscale_audit_payload_does_not_contain_value`
  - `test_camera_pairing_secret_rotated_emitted_on_repair`
  - `test_audit_docstring_catalogue_lists_all_three_events`
- `tools/secrets/tests/test_check_persisted_secrets.py` —
  covers AC-2, AC-12 (unit-level enumeration of the linter's
  truth-table).

**Modified docs:**

- `docs/cybersecurity/security-risk-analysis.md` — SC-005
  rewrite + new SC-101 row. Per AC-6.
- `docs/cybersecurity/threat-model.md` — THREAT-005 cell
  update; new line under § "Audit Events" for the three
  `*_ROTATED` events. Per AC-6.
- `docs/cybersecurity/security-plan.md` — extend SEC-005's
  description (or open a new SEC ID — Implementer's call per
  AC-8) to name the inventory page and the linter as the
  current SC-005 evidence.
- `docs/guides/admin-recovery.md` — append Case 3. Per AC-5.
- `docs/exec-plans/luks-post-pair-migration.md` — one-line
  cross-reference at the top (per AC-9). No other change.
- `docs/doc-map.yml` — register
  `docs/operations/secrets-inventory.md`. Per AC-7.
- `docs/traceability/traceability-matrix.csv` — new rows for
  `UN-101`, `SYS-101`, `SWR-101-{A,B,C}`, `RISK-101-{1..3}`,
  `THREAT-101`, `SC-101`, `TC-101-AC-{1..14}`. Per AC-8.
- `docs/traceability/traceability-matrix.md` — one-line
  entry under "Software requirements".
- `docs/risk/dfmea.md` — add HAZ-101-{1..3} with severity /
  probability / RC columns matching the rows below.
- `docs/risk/risk-control-verification.md` — add RC-101-{1..3}
  verification rows pointing at the new tests above.
- `CHANGELOG.md` — Unreleased bullet per AC-11.

**Modified scripts:**

- `scripts/smoke-test.sh` (or whichever row list the hardware
  smoke runner consumes) — add the AC-10 row.
- `.pre-commit-config.yaml` — register the new linter as a
  fast pre-commit hook (per AC-2 / AC-14).

**Out of scope of this spec (touch only if a clean import
demands it):**

- `app/server/monitor/auth.py` — unchanged. The session-cookie
  Secure/HttpOnly/SameSite posture, the bcrypt cost, the lockout
  thresholds — none of those interact with the secrets-at-rest
  surface except via the SECRET_KEY (which is covered by the
  audit emit in `__init__.py`).
- `meta-home-monitor/` recipes, `linux-raspberrypi_%.bbappend`,
  `packagegroup-monitor-security.bb`, `home-monitor-ab*.wks` —
  unchanged. The kernel + userland for LUKS is already in the
  build; this PR does not enable it. The 1.4.1 LUKS rollout is
  the seat for any further Yocto changes.
- `docs/history/adr/0010-luks-data-encryption.md` — unchanged
  per AC-13.
- `app/camera/camera_streamer/` — unchanged. The camera stores
  certs and `pairing_secret` derivative material on its own
  `/data`; the same OG arguments apply (`plaintext-on-data`),
  the camera's row in the inventory page reflects this, and the
  LUKS exec plan ships the camera-side rollout. No code change
  in the camera tree from this PR.

**Dependencies:**

- No new external Python or JavaScript deps. The linter is
  stdlib (`ast`, `pathlib`, `re`).

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python (audit emits + settings flow) | `pytest app/server/tests/ -v --cov-fail-under=85`, `ruff check .`, `ruff format --check .` |
| Tooling (new linter under `tools/secrets/`) | `pytest tools/secrets/tests/ -v`; the linter itself runs as a pre-commit hook on the change set |
| Security-sensitive docs | full server suite + the new audit-emit unit tests + manual security-review checklist row covering AC-1 (inventory exhaustiveness) and AC-5 (rotation order) |
| API contract | none — no API surface change (settings PUT shape unchanged; the audit emit is a server-side observation only) |
| Frontend / templates | none — no UI surface change |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py`, `python tools/docs/check_doc_map.py` |
| Hardware behavior | `scripts/smoke-test.sh` row from AC-10; deploy + run end-to-end on real hardware with at least one paired camera + one Tailscale-configured device |
| Repository governance | `python -m pre_commit run --all-files` (will exercise the new linter), `python scripts/ai/validate_repo_ai_setup.py`, `python scripts/ai/check_doc_links.py`, `python scripts/ai/check_shell_scripts.py`, `python scripts/check_version_consistency.py`, `python scripts/check_versioning_design.py` |

Smoke-test additions (Implementer wires concretely):

- AC-10 row: walk `/data/config/*.json`, find any string-valued
  key matching the secret-substring pattern, assert each appears
  in the rendered `secrets-inventory.md` as a `field:` row or in
  the smoke script's `KNOWN_NON_SECRET_RUNTIME` allowlist.
- Optional second smoke row: trigger a Tailscale auth-key write
  (curl `PUT /api/v1/settings` with a sentinel value) and assert
  the audit log on `/logs/audit.log` shows
  `TAILSCALE_AUTH_KEY_ROTATED` with redacted payload. Verifies
  AC-3 on real hardware.

## Risk

ISO 14971-lite framing. Hazards specific to this risk-disposition
close-out PR (the LUKS-related hazards stay with the existing
exec plan and ADR-0010):

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-101-1 | The inventory page goes stale: a contributor adds a new persisted secret, the linter doesn't catch it (e.g., a new field name that doesn't match the substring pattern, or a field added in a module the linter doesn't scan), the operator reads the inventory and concludes the device's exposure is fully enumerated when it isn't. The "single source of truth" promise is silently broken. | Major (security, by misleading the operator) | Medium (drift is the natural failure mode of any hand-maintained list) | RC-101-1: Two backstops in different layers — (a) the pre-commit linter on suspect substrings (AC-2) catches the common case, (b) the smoke-row check on real `/data/config/` content (AC-10) catches drift between code scope and runtime scope. Neither is sufficient alone; together they catch all field-name-pattern matches that land in `/data/config/*.json`. Fields stored elsewhere on `/data` (e.g., `/data/certs/`, `/data/share_links.json`) are out of the linter's scope and rely on the smoke row + reviewer attention; documented as OQ-2. |
| HAZ-101-2 | Operator reads the inventory and decides "everything is fine, I don't need LUKS." False reassurance: the page exists to make the gap visible, not to mark it closed. If the page is unclear about residual risk, the operator under-protects. | Moderate (security posture choice) | Medium | RC-101-2: AC-1 mandates that every `plaintext-on-data` row carries an explicit "physical access yields the cleartext; LUKS migration on roadmap" cell. The page's *header* must include the sentence "If your SD card walks away, every row classified as `plaintext-on-data` below is exposed in cleartext today. This page exists to make that exposure visible, not to close it." Documented as a hard layout requirement; reviewer checks it. |
| HAZ-101-3 | Audit-emit failure for a `*_ROTATED` event: the rotation succeeds (Flask key, Tailscale, camera secret) but no audit row lands. Operator forensics after a suspected compromise can't tell whether a rotation already happened or not. | Moderate (operational + audit) | Low | RC-101-3: Same trade-off the existing audit pipeline accepts elsewhere (`_log_audit` swallows OSError so a rotation isn't refused by a logger problem). HAZ-101-3 is the same shape as HAZ-099-3 in spec `99-admin-password-reset.md`; the existing storage-low alert (#r1-storage-retention-alerts.md) and the audit-export schema reviewer (#247) are the layered controls. The unit tests in AC-3 assert the *attempt* to emit; the runtime acceptance is "best-effort, by design." |
| HAZ-101-4 | A future agent reads this spec, sees the ADR-0010 reference, and concludes that LUKS is *shipping* in this PR. They report #101 as closed when only the risk-disposition track is closed. The operator-facing security posture is unchanged from before this PR. | Minor (project-tracking + reporting) | Low (the spec is explicit, but humans skim) | RC-101-4: Title and Goal both lead with "risk-disposition close-out"; AC-9 forces the cross-reference at the top of the LUKS exec plan; AC-11 forces the CHANGELOG bullet to explicitly disclaim that LUKS is unchanged; the close-out PR description should propose updating the issue title from "Security: secrets stored unencrypted" to "Security: secrets-at-rest risk-disposition close-out (LUKS implementation tracked separately)." |
| HAZ-101-5 | A contributor satisfies the linter by adding a noise row to the inventory that doesn't actually correspond to a real secret in the codebase — the page accumulates dead rows and stops being trustworthy. | Minor (doc rot) | Low | RC-101-5: The smoke row in AC-10 walks the runtime `/data/config/` and asserts every `field:` row it sees in the inventory points at a real key in a real JSON file (or is on the `KNOWN_NON_SECRET_RUNTIME` allowlist). Dead rows fail smoke. |
| HAZ-101-6 | The pre-commit linter generates enough false positives that contributors learn to bypass it (`git commit --no-verify` or routine appending to `KNOWN_NON_SECRET` without thinking). The rule erodes into noise. | Moderate (engineering culture) | Medium | RC-101-6: AC-2 mandates the pattern is documented in the script header; the false-positive workflow is "add to allowlist with a one-line justification" (cheap, but visible in code review). The repository's standing `--no-verify` posture is "prohibited unless the user explicitly requests it" (per `CLAUDE.md`'s Bash safety protocol section); a contributor bypassing the linter without an allowlist entry is a review-blocking concern, not a style nit. |

Reference `docs/risk/` for the existing architecture risk register;
this spec adds rows HAZ-101-1 through HAZ-101-6.

## Security

Threat-model deltas (Implementer fills concrete `THREAT-` / `SC-` /
`SEC-` IDs in the traceability matrix per AC-8):

- **Sensitive paths touched:** `app/server/monitor/__init__.py`
  (the SECRET_KEY load — touch is *audit only*, no change to the
  load semantics or the file format),
  `app/server/monitor/services/settings_service.py`
  (the `tailscale_auth_key` write path — touch is *audit only*),
  `app/server/monitor/services/pairing_service.py` and/or
  `cert_service.py` (the re-pair audit emit). All three changes
  are observation-only — they emit a structured audit row when
  an existing rotation happens, they do not introduce a new
  rotation surface and do not weaken any existing one.
- **Sensitive paths NOT touched:** `app/server/monitor/auth.py`
  (auth flow, session posture, lockout — unchanged),
  `app/server/monitor/templates/login.html` (pre-auth surface —
  ADR-0022 lock holds, unchanged), `**/secrets/**` (no actual
  secret-handling code is rewritten — the linter scans secret-
  *adjacent* code), `meta-home-monitor/**` (Yocto/kernel/wks —
  unchanged; the LUKS exec plan owns those changes when it
  ships), `docs/history/adr/0010-luks-data-encryption.md`
  (unchanged per AC-13), `app/camera/**` (camera tree —
  unchanged), `**/.github/workflows/**` (only the depot rule
  gate row in the PR description is new; no workflow file edit
  unless the new pre-commit hook needs explicit CI wiring,
  which the existing `pre_commit run --all-files` job already
  covers).
- **No new external surface.** No new API endpoint, no new
  pre-auth surface, no new operator-callable command. The
  inventory page is read-only documentation; the linter is a
  developer-machine pre-commit hook; the smoke check runs
  against `/data` on a deployed device by an operator with
  shell access.
- **No new persisted secret material.** The audit events
  carry asset identifiers (`tailscale`, camera id) and actor
  metadata — never the rotated secret value. Redaction is
  enforced at audit-emit time and asserted by AC-3's unit
  tests.
- **SEC-101-A — inventory exhaustiveness invariant.** Every
  string field on `models.Settings`, `models.Camera`,
  `models.User`, and any future top-level dataclass that
  matches the secret-substring pattern is either declared in
  `secrets-inventory.md` with a classification, or held in
  memory only (not persisted), or on the linter's
  `KNOWN_NON_SECRET` allowlist with a justification. The
  pre-commit linter is the enforcement; the smoke row is the
  drift detector. AC-2 + AC-10 + AC-12 pin the layered
  control.
- **SEC-101-B — audit invariant for at-rest-secret rotation.**
  Every code path that mints, rotates, or replaces a persisted
  secret emits exactly one named audit event (`SECRET_KEY_ROTATED`,
  `TAILSCALE_AUTH_KEY_ROTATED`, `CAMERA_PAIRING_SECRET_ROTATED`,
  or a future event added to the catalogue at the same time
  the emitter lands). AC-3 + AC-4 pin discoverability.
- **SEC-101-C — no new rotation surface.** Rotations happen
  through existing UI / lifecycle paths (settings PUT for
  Tailscale, unpair → re-pair for camera secrets, first-boot
  / reflash for the Flask key). This spec does NOT add a
  CLI rotation tool, a localhost-only rotation endpoint, or
  any other shortcut that would re-introduce the ADR-0022
  backdoor risk. Reaffirms ADR-0022 rule 1.
- **SEC-101-D — no widening of the must-change-block
  allow-list and no edit to the pre-auth login surface.**
  This spec does not touch `auth.py` `_must_change_block`
  (per spec `99-admin-password-reset.md` AC-5/RC-099-1) and
  does not touch `templates/login.html` (per
  `99-admin-password-reset.md` AC-10/RC-099-2). The
  inventory page is operations documentation, not pre-auth
  UX, and is reachable only by someone reading the repo.
- **SEC-101-E — Option B (per-secret wrapping) deferral as
  posture.** Not building a per-secret wrapping scheme inside
  the plaintext `/data` partition is itself a security
  posture: a wrapping key inside the same plaintext volume
  has no security benefit, and a wrapping key derived from
  device identity is defeated by the same physical-access
  attacker who reads `/data`. Once Option A (LUKS) ships,
  per-secret wrapping inside the encrypted volume is
  redundant. Documented in Non-Goals so a future agent who
  is asked "why didn't we just encrypt the secrets we know
  about?" finds the answer in the spec.
- **SEC-101-F — TPM track parking reaffirmed.** The TPM-
  backed recovery OTP option from ADR-0022 is parked
  behind the hardware refresh; this spec does not lift the
  parking. Lifting it would require a hardware change
  (Pi 5 with TPM, or external TPM module) and a fresh
  threat-model review of the recovery surface.
- **THREAT-101 (new, Implementer numbers per AC-8) — drift
  between the inventory page and reality.** A new persisted
  secret is added to the codebase; the linter doesn't match
  its name; the smoke row catches it on the next deploy.
  Residual risk if the smoke row is also bypassed: an
  operator reads an incomplete inventory and under-protects.
  Linked controls: SC-101.
- **Default-deny preserved.** No new endpoint to deny; no
  new auth surface; no new file mode required. The existing
  0o600 on `/data/config/.secret_key` and the existing
  redaction posture on `SECRET_FIELDS` are preserved.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.csv` per AC-8):

- `UN-101` — User need: "When my device walks away with the SD
  card, I want to know in advance which of my secrets are
  exposed in cleartext, in what order to rotate them, and
  what the plan is to close the gap."
- `SYS-101` — System requirement: "The system shall maintain a
  documented inventory of every persisted secret on the device,
  emit a named audit event on every rotation of a known
  persisted secret, refuse silent additions of new persisted
  secrets without a documented disposition, and reference the
  encryption-at-rest implementation track as the closure
  mechanism for `plaintext-on-data` rows."
- `SWR-101-A` — Software requirement: audit emits for the
  three named rotation events (per AC-3, AC-4).
- `SWR-101-B` — Software requirement: pre-commit linter
  refuses silent additions of persisted secrets (per AC-2,
  AC-12).
- `SWR-101-C` — Software requirement: smoke-row drift
  detector compares runtime `/data/config/` to the inventory
  page (per AC-10).
- `SWA-101` — Software architecture item: the inventory page
  + linter + smoke row form the secrets-at-rest control
  triad. The inventory is the human-readable contract; the
  linter is the developer-time enforcement; the smoke row is
  the deploy-time drift detector. Single source of truth =
  `services/settings_service.py:SECRET_FIELDS` (per AC-12)
  for the redaction list; the inventory page hand-mirrors it
  with classification metadata; the linter imports it.
- `HAZ-101-1` … `HAZ-101-6` — listed above.
- `RISK-101-1` … `RISK-101-3` — one per category (drift,
  false-reassurance, audit gap; HAZ-4..6 are
  project-management / culture risks not safety risks, not
  necessarily mirrored as RISK-### rows — Implementer's call
  whether they earn matrix rows or stay as HAZ-only).
- `RC-101-1` … `RC-101-3` — one per safety risk.
- `SEC-101-A` (inventory exhaustiveness invariant),
  `SEC-101-B` (audit invariant for rotation),
  `SEC-101-C` (no new rotation surface),
  `SEC-101-D` (no pre-auth / must-change-block edit),
  `SEC-101-E` (Option B deferral as posture),
  `SEC-101-F` (TPM track parking reaffirmed).
- `THREAT-101` — drift between inventory and reality.
- `SC-101` — Key rotation tooling and runbook for
  post-compromise response (per the new control row in
  `security-risk-analysis.md`).
- `TC-101-AC-1` … `TC-101-AC-14` — one test case per
  acceptance criterion above. Many are doc reviews (AC-1,
  AC-5, AC-7, AC-9, AC-11, AC-13); the rest are code
  (AC-2, AC-3, AC-4, AC-6, AC-8, AC-10, AC-12, AC-14).

Code-annotation examples (Implementer adds these):

```python
# REQ: SWR-101-A; RISK: RISK-101-3; SEC: SC-005, SC-101;
# TEST: TC-101-AC-3, TC-101-AC-4
def _load_or_create_secret_key(config_dir):
    ...
```

```python
# REQ: SWR-101-A; RISK: RISK-101-3; SEC: SC-005, SC-101;
# TEST: TC-101-AC-3
# Emit TAILSCALE_AUTH_KEY_ROTATED on the write branch:
def _apply_tailscale_auth_key(...):
    ...
```

```python
"""tools/secrets/check_persisted_secrets.py — pre-commit linter.

REQ: SWR-101-B; RISK: RISK-101-1; SEC: SC-101; TEST: TC-101-AC-2
...
"""
```

## Deployment Impact

- Yocto rebuild needed: **no** (no recipe / packagegroup /
  kernel-fragment edit). The LUKS userland is already in
  `packagegroup-monitor-security.bb` and the Adiantum kernel
  fragment is already wired via `linux-raspberrypi_%.bbappend`;
  this PR does not change either.
- OTA path: standard server-image OTA. The runtime change is
  limited to three extra audit emits and (optionally) a
  module-level constant export in `audit.py`. Backwards-
  compatible: pre-update audit consumers see no change to
  existing event names; the three new event names appear in
  the docstring catalogue and in the audit-export schema
  (#247) the next time the export is regenerated.
- Hardware verification: yes — required for AC-10 smoke row.
  Run on a deployed device with at least one paired camera
  and a configured Tailscale auth key so the smoke check has
  real `*.json` content to walk.
- Default state on upgrade: identical to today. No migration.
  No config file changes. No new operator decision.
- Rollback: trivial. The PR adds docs, a linter, a smoke row,
  and three audit emits. Rolling back leaves the docs/linter
  on disk in `/var/lib/...` (unused by the older runtime) and
  the runtime is functionally unchanged from pre-PR (the
  three audit events stop being emitted, which is the
  pre-PR baseline).
- Audit-log compatibility: the three new event names are
  additive. Pre-update audit consumers ignore unknown event
  names (existing pattern); post-update consumers see the
  new names and can route them. No log-format change.
- Encryption-at-rest posture on upgrade: **unchanged**. The
  device still runs ext4 on `/data` until the operator opts
  in to the LUKS migration via the 1.4.1 exec plan. This PR
  does NOT enable encryption.

## Open Questions

(None of these are blocking; design proceeds. Implementer
captures answers in PR description.)

- OQ-1: Should the close-out PR ALSO export
  `SECRET_KEY_ROTATED` / `TAILSCALE_AUTH_KEY_ROTATED` /
  `CAMERA_PAIRING_SECRET_ROTATED` as module-level string
  constants in `audit.py` (next to the `CLIP_TIMESTAMP_*`
  block), or leave them as bare-string literals?
  **Recommendation:** export the constants. Costs ~6 lines,
  removes a stringly-typed footgun, and the existing
  `CLIP_TIMESTAMP_*` constants are precedent. If it churns
  more than a handful of test sites, defer per the OQ-1
  pattern in spec `99-admin-password-reset.md`.
- OQ-2: The pre-commit linter scans
  `app/server/monitor/models.py` and the `SECRET_FIELDS`
  list. It does NOT scan `/data/certs/` (PEM private keys)
  or `/data/share_links.json` (token-shaped values). Should
  the linter's scope grow to cover all top-level files
  written under `/data` by the app, or stay scoped to
  models?
  **Recommendation:** stay scoped to models in this PR.
  PEM keys are minted by `cert_service.py` from a known
  ceremony (CA generation, server cert renewal, per-camera
  cert issue) — not arbitrary contributor additions. Share
  link tokens are short-lived and aren't classified as
  at-rest secrets in the same sense. The smoke row
  (AC-10) walks runtime `/data/config/` and is the
  catch-all. Reconsider if a future PR adds a new
  long-lived secret category outside `models.py`.
- OQ-3: Should the inventory page also list the *unencrypted*
  `users.json` `password_hash` (bcrypt) and `recovery_code_hashes`
  (bcrypt) as `hashed` rows, even though they are not
  residual-risk?
  **Recommendation:** **yes** — list them with classification
  `hashed` and an explicit note "not a residual risk; bcrypt
  cost 12 is brute-force-resistant under physical access; the
  rotation guidance is still 'reset all user passwords after a
  suspected compromise' as a defence-in-depth posture, since
  bcrypt at scale is not infinitely strong against a determined
  attacker with the cleartext bytes." Inventory is more useful
  when complete than when filtered to residual-risk-only.
- OQ-4: Should the smoke-row check (AC-10) **fail the deploy
  on drift**, or fail with a warning that requires explicit
  operator acknowledgement?
  **Recommendation:** fail the deploy. A drift detector that
  warns is a drift detector that gets ignored. The
  acknowledged-bypass path is "add the new field to
  `KNOWN_NON_SECRET_RUNTIME` in the smoke script with a
  justification" — cheap, visible in `git diff`, code-reviewed.
- OQ-5: When the LUKS migration ships in 1.4.1, who owns
  flipping every `plaintext-on-data` row to
  `encrypted-at-rest` in the inventory page?
  **Recommendation:** the implementer of the LUKS migration
  PR. AC-9 pins the cross-reference at the top of the exec
  plan so the implementer finds the inventory page and knows
  to update it in the same PR. Not this spec's concern; not
  this spec's PR.

## Implementation Guardrails

- This PR ships **no LUKS migration code**. The implementation
  track for LUKS is and remains
  `docs/exec-plans/luks-post-pair-migration.md`. Any change to
  Yocto recipes, kernel config fragments, WKS files,
  first-boot services, SWUpdate post-install hooks, or the
  cryptsetup invocation is out of scope for this PR.
- Preserve ADR-0022's no-backdoor invariant. No new CLI
  rotation tool. No new pre-auth surface. No new
  localhost-only endpoint. Rotations happen through existing
  UI / lifecycle paths and are **observed** (audit emits) by
  this PR, not **introduced**.
- Preserve the must-change-block allow-list and the
  pre-auth login surface from spec
  `99-admin-password-reset.md`. This PR does not edit
  `auth.py:_MUST_CHANGE_ALLOWED_ENDPOINTS` and does not edit
  `templates/login.html`.
- Preserve the audit catalogue invariant from spec
  `99-admin-password-reset.md` (every named event lives in
  the docstring catalogue at `services/audit.py:8`). The
  three new events land in the catalogue in the same PR
  that lands their emit sites.
- Single source of truth for secret-field names is
  `services/settings_service.py:SECRET_FIELDS`. The
  inventory page hand-mirrors it; the linter imports it.
  Renaming a field in `SECRET_FIELDS` MUST update the
  inventory in the same PR (per AC-12).
- Tests + docs + traceability ship in the same PR as the
  code (per `engineering-standards.md` and
  `medical-traceability.md`).
- No new external Python or JavaScript dependencies; the
  spec scope is docs-and-tests-and-linter, not feature.
- Option A (LUKS) is the implementation track; it is
  **deferred to its existing exec plan, not rejected**.
  Option B (per-secret wrapping) is **rejected** with
  rationale durable in Non-Goals + SEC-101-E. Option C
  (this PR) is the risk-disposition close-out.
- The close-out PR description should propose updating the
  GitHub issue title from "Security: Secrets stored
  unencrypted on SD card — stolen device yields full
  system compromise" to "Security: secrets-at-rest
  risk-disposition close-out (LUKS implementation tracked
  separately in `docs/exec-plans/luks-post-pair-
  migration.md`)" so a project-board reader doesn't
  conclude "encryption is shipped" from this PR alone (per
  RC-101-4).
