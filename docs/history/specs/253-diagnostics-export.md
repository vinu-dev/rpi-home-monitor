# Feature Spec: System Diagnostics Export

Tracking issue: #253. Branch: `feature/253-diagnostics-export`.

## Title

A one-click **Export Diagnostics** action in Settings → System that
produces a single timestamped tarball containing application + audit
logs, scrubbed configuration snapshots, hardware health output
(`vcgencmd`, `df`, `/proc/meminfo`), systemd journal slices for the
appliance services, and network state — for operator self-service
troubleshooting and admin-led support handoff.

## Goal

When motion stops firing, the live grid stutters, or the disk fills
unexpectedly, an operator should not have to SSH in and `journalctl`
their way to a useful trace. They open Settings → System → Export
Diagnostics, click once, and a single
`hm-diagnostics-<host>-<UTC>.tar.gz` lands in their browser. They can
read it themselves, attach it to a GitHub issue, or hand it to whoever
is helping them debug — without exposing pairing keys, password
hashes, TOTP secrets, webhook secrets, or TLS private keys.

Concretely the feature delivers:

- A new admin-gated `POST /api/v1/system/diagnostics/export` endpoint
  that assembles a deterministic tarball on disk and streams it via
  `send_file()` on the existing pattern.
- A pure **bundle assembler** service
  (`DiagnosticsBundleService`) that pulls log files from
  `/data/logs/`, scrubbed JSON snapshots of `cameras.json`,
  `users.json`, and `settings.json` (via a new
  `redact_secrets()` utility), `vcgencmd measure_temp / measure_clock /
  get_throttled / measure_volts`, `df -h` and `/proc/meminfo` /
  `/proc/uptime` reads, `journalctl -u <unit> --since=...` output for
  the appliance's systemd units, `ip -j addr` and `ip -j route` for
  network state, the OTA / firmware identity (`/etc/os-release`,
  `release_version()`, slot status if available), and a top-level
  `manifest.json` describing what the bundle contains.
- A scrubbed, viewer-blind GET shape: viewers cannot trigger an export,
  cannot enumerate prior exports, and cannot read the tarball — only
  admins.
- A bounded, single-flight execution model: one export at a time per
  appliance, capped output size (default 50 MB), per-section size
  caps so a runaway log can't blow up the bundle, and a hard
  collection timeout (60 s) so a stuck `journalctl` can't hang the
  request.
- A new dashboard button in the existing Settings page's "System" tab
  that submits a CSRF-protected POST, watches a busy spinner, and
  triggers the file download on success. Failure surfaces a clear
  inline error.
- A `DIAGNOSTICS_EXPORTED` audit event on every successful export so
  the action is visible in the same audit trail operators already
  trust.

This complements two open items already in the architectural roadmap:

- **ADR-0018** explicitly says raw metrics belong on a **future
  `/diagnostics` page** (`docs/history/adr/0018-dashboard-information-architecture.md:41`).
  The bundle assembler this spec defines is the natural data source
  for that page when it lands; the assembler exposes its sections via
  pure functions that the future page can also call without re-shelling
  to the OS.
- **Issue #247** (audit log export — referenced in #253's body) ships
  in parallel; this spec **does not duplicate** that work. The
  diagnostics bundle includes a copy of the audit log file as it sits
  on disk; audit-only export remains #247's surface.

## Context

Existing code this feature must build on:

- `app/server/monitor/__init__.py:110` — `create_app()`: the Flask
  app-factory. The new diagnostics surface is registered through the
  existing `_register_blueprints()` flow at
  `app/server/monitor/__init__.py:589`, with the bundle service
  injected during `_init_services()` at
  `app/server/monitor/__init__.py:240` (one new line, dependency-
  injected like every other service).
- `app/server/monitor/__init__.py:170` — `_init_infrastructure()`: the
  point where `app.audit` is created. The new bundle service receives
  `app.audit` for the success/failure event emission.
- `app/server/monitor/api/system.py:71` — `/system/health` endpoint:
  the natural sibling for `/system/diagnostics/export`. The new route
  lives **in the same blueprint** (`system_bp`) — diagnostics is a
  system-info concern, not a separate domain. No new blueprint module
  is required; we extend `api/system.py` with one route.
- `app/server/monitor/api/system.py:78` — the docstring already
  declares "raw metrics belong on /diagnostics, derived state belongs
  on the dashboard" (ADR-0018). This spec is the first concrete
  consumer of that future surface.
- `app/server/monitor/services/health.py` — already reads
  `/proc/meminfo` (`:86`), `/proc/stat` (`:40`), `/proc/uptime`
  (`:138`), `/sys/class/thermal/thermal_zone0/temp` (`:32`),
  `shutil.disk_usage("/data")` (`:117`), and `/sys/class/net/` for
  per-interface state (`:166`). The bundle reuses these helpers; **no
  duplicate file reads** are written. Where the bundle needs a richer
  view than `health.py` provides (e.g. `df -h` across all mounts, not
  just `/data`), the bundle assembler shells out itself.
- `app/server/monitor/services/audit.py:46` — `AuditLogger` class.
  - `log_event(event, user, ip, detail)` (`:56`) is the call shape used
    everywhere; the new event `DIAGNOSTICS_EXPORTED` reuses it
    unchanged.
  - The audit log file lives at `/data/logs/audit.log` (`:51`,
    rotation policy: 50 MB × 90 days). The bundle copies this file
    verbatim — it is already line-delimited JSON, no further
    processing needed.
- `app/server/monitor/auth.py:174` — `login_required`,
  `app/server/monitor/auth.py:196` — `admin_required`,
  `app/server/monitor/auth.py:220` — `csrf_protect`. The new route is
  `@admin_required + @csrf_protect` (POST). Viewer role
  (`models.py:183`, `role: str = "viewer"`) gets 403.
- `app/server/monitor/api/recordings.py:120` — `send_file(clip_path,
  mimetype="video/mp4")` is the file-download pattern this spec
  follows. The diagnostics route streams a tarball with
  `mimetype="application/gzip"` and a `Content-Disposition: attachment`
  header containing the deterministic filename.
- `app/server/monitor/api/audit.py:27` — `GET /api/v1/audit/events`
  exists and returns audit events as JSON (filterable by event type,
  with limit). This stays as-is; the diagnostics bundle includes a
  raw copy of the audit log file but does not duplicate the events
  endpoint. Issue #247 owns the standalone audit-log-as-file export
  (NDJSON).
- `app/server/monitor/services/cert_service.py:112` — the established
  subprocess pattern: `subprocess.run([...], check=False,
  capture_output=True, timeout=...)` with **list args, no
  `shell=True`**. Diagnostics shell-outs follow this pattern strictly
  (HAZ-253-3, SC-253-A).
- `app/server/monitor/__init__.py:402` — another existing list-args
  example: `subprocess.run(["hostnamectl", "set-hostname", hostname])`.
- `app/server/monitor/services/factory_reset_service.py:139` —
  `["systemctl", "reboot"]` pattern; diagnostics reuses the same
  conventions for invocations like `systemctl status <unit>`.
- `app/server/monitor/services/ota_service.py:313` —
  `Popen(["swupdate", ...])` shows the streaming-stdout pattern; the
  diagnostics service does **not** need streaming — it captures bounded
  output via `subprocess.run(..., timeout=N, capture_output=True)` and
  truncates if it overruns.
- `app/server/monitor/__init__.py:129` — config keys: `DATA_DIR`,
  `RECORDINGS_DIR`, `LIVE_DIR`, `CONFIG_DIR`, `CERTS_DIR`. The bundle
  is staged in `CONFIG_DIR/diagnostics-staging/` (a tmpdir under
  `/data/config`, cleaned up after streaming) so it inherits the LUKS
  encryption-at-rest of the data partition (ADR-0010) — never under
  `/tmp` (which is `tmpfs` and may be smaller than the bundle).
- `app/server/monitor/logging_config.py` — primary application log at
  `/data/logs/monitor.log`, rotated 10 MB × 5 (50 MB total). The
  bundle copies the active file plus all rotated `.1`–`.5` siblings
  if present.
- `app/server/monitor/templates/settings.html:17` — the existing tabbed
  "System" panel inside Settings. The new **Export Diagnostics** button
  lives in the System tab's body, below the existing System Info card,
  under a new **Diagnostics** card. (We do **not** add a new top-level
  tab — diagnostics is admin-only and rare; making it a standalone tab
  inflates IA without payoff. ADR-0018 hierarchy keeps rare admin
  actions inside Settings, not on the dashboard.)
- `app/server/monitor/api/audit.py` — the audit blueprint registers
  inside `_register_blueprints()` (`__init__.py:589`). Since
  `system_bp` is the host for the new route, no new blueprint
  registration is required.
- `app/server/monitor/store.py` — the JSON store with `cameras.json`,
  `users.json`, `settings.json`. The bundle pulls **scrubbed** copies
  of each through a new `redact_secrets()` helper, not raw file copies
  — the on-disk `users.json` contains `password_hash`, `totp_secret`,
  and `recovery_code_hashes`, all of which must be replaced with
  `"[REDACTED]"` markers in the bundle (HAZ-253-1, SC-253-B).
- ADR-0011 (auth hardening) — the bundle never includes
  `password_hash`, `totp_secret`, or `recovery_code_hashes`. Even
  though they are bcrypt / encrypted, exposing them in a sharable
  artefact lowers the attack cost of an offline brute-force.
- ADR-0010 (LUKS data encryption) — the staging tmpdir lives on the
  encrypted `/data` partition; we do **not** stage in `/tmp`.
- ADR-0017 (on-demand viewer-driven streaming) — unrelated; export is
  a one-shot action, not a stream.
- ADR-0022 (no backdoors) — explicitly relevant. The diagnostics
  bundle must **never** re-derive a working credential or session
  cookie. We include `/etc/os-release` but never `/data/config/.secret_key`
  (Flask session signing key — leaking it lets anyone forge sessions),
  never bcrypt password hashes, never TOTP secrets, never TLS private
  keys, never pairing secrets, never `tailscale_auth_key`. The exact
  redaction allowlist is in **Module Impact** below.
- Cross-spec references:
  - **#247 audit log export** — produces a standalone NDJSON download
    of the audit log; the diagnostics bundle includes the same file
    verbatim. The two surfaces co-exist: a single-file audit dump is
    useful when an operator only cares about the trail; the bundle
    is useful when the trail alone isn't enough.
  - **#250 health observability** — adds richer metrics (clock-drift
    detection). When that lands, its outputs slot into the bundle
    via the `health.py` helpers without re-architecting; this spec's
    `manifest.json` schema is forward-compatible.
  - **#252 encoder presets** — unrelated.
  - **ADR-0018 future `/diagnostics` page** — the bundle assembler is
    the data layer that future page will reuse. Designing for "POST
    that returns a tarball" today does not block "GET that returns
    JSON for an in-page viewer" tomorrow; both call the same
    `DiagnosticsBundleService.collect_sections()` helper.

The issue body lists OpenHAB, MotionEye, and NVRs (Synology / TrueNAS
/ Home Assistant) as precedent. All of those produce a **single
sharable bundle**, not a streaming feed; this design matches that
expectation.

## User-Facing Behavior

### Primary path — operator clicks Export Diagnostics

1. Operator (admin) opens Settings → System tab. They see a new
   **Diagnostics** card below "System Info" with one button:
   **Export Diagnostics**, plus a one-line description: "Bundle logs,
   configuration, and hardware state for troubleshooting. Sensitive
   values (passwords, keys, secrets) are redacted before download."
2. Operator clicks the button. The button enters a busy state
   (`disabled`, spinner, label text changes to "Collecting…"). A
   client-side timeout warning appears at 30 s ("Still collecting…").
3. The browser POSTs to `/api/v1/system/diagnostics/export` with the
   CSRF header. The server:
   - acquires a per-process single-flight lock (HAZ-253-7);
   - stages the bundle under `/data/config/diagnostics-staging/<run-id>/`;
   - assembles every section (logs, scrubbed configs, hardware, network,
     systemd, OTA identity, manifest);
   - tars + gzips the result to
     `/data/config/diagnostics-staging/<run-id>/hm-diagnostics-<host>-<UTC>.tar.gz`;
   - emits `DIAGNOSTICS_EXPORTED` with cam_count, byte_size, sections,
     duration_ms;
   - streams the file via `send_file(..., as_attachment=True,
     download_name=...)`;
   - cleans up the staging dir after the response is fully sent
     (Flask `@after_request` deferred unlink, with a fallback
     scheduled cleanup pass).
4. Browser receives `application/gzip`, saves
   `hm-diagnostics-<host>-<UTC>.tar.gz` (filename from
   `Content-Disposition: attachment`). Button returns to default state.
5. Operator opens the tarball locally. Top-level layout:
   ```
   hm-diagnostics-<host>-<UTC>/
     manifest.json              # what's in this bundle, redaction notes
     logs/
       monitor.log              # current rotated active file
       monitor.log.1            # rotated copies if present
       ...
       audit.log
       ffmpeg/                  # per-pipeline ffmpeg stderr (truncated)
     config/
       cameras.json             # scrubbed
       users.json               # scrubbed
       settings.json            # scrubbed
     hardware/
       vcgencmd-measure_temp.txt
       vcgencmd-measure_clock.txt
       vcgencmd-get_throttled.txt
       vcgencmd-measure_volts.txt
       df.txt
       meminfo.txt
       uptime.txt
       cpuinfo.txt
       loadavg.txt
       thermal.txt              # /sys/class/thermal/thermal_zone0/temp
     network/
       ip-addr.json             # ip -j addr
       ip-route.json            # ip -j route
       interfaces.txt           # /sys/class/net/* state
       resolv.conf              # /etc/resolv.conf
     systemd/
       monitor.journal.txt      # journalctl -u monitor --since=-7d
       mediamtx.journal.txt
       camera-streamer.journal.txt
       ...
       systemd-status.txt       # systemctl status <each unit>
     identity/
       os-release.txt           # /etc/os-release
       release_version.txt      # release_version() output
       hostname.txt
   ```
6. The `manifest.json` records: `bundle_version: 1`, `generated_at`
   (UTC ISO), `host`, `firmware_version`, `requested_by`,
   `sections: [{name, file_count, byte_size, truncated, error}]`,
   `redactions: [{file, fields}]`, `tool_versions:
   {vcgencmd, journalctl, ip}`.

### Primary path — operator hands the bundle off

1. Operator attaches `hm-diagnostics-pi-2026-05-04T12-30-00Z.tar.gz`
   to a GitHub issue or an email to support.
2. The recipient untars, opens `manifest.json` first to see what's
   present and what was redacted, then walks the sections.

### Failure states (designed, not just unit-tested)

- **Viewer (non-admin) hits the endpoint**: 403, no bundle is
  produced, no audit event fires (consistent with every other
  admin-gated POST). The button is **not rendered** in the dashboard
  for viewers — the existing role-gate around the System tab
  (`settings.html`) hides it. Defence in depth: server-side
  `@admin_required` independently rejects.
- **CSRF token missing or stale**: 403 from `csrf_protect`, with the
  existing error envelope. Button surfaces "Session expired, please
  reload" hint.
- **Concurrent export already running**: 429 with body
  `{"error": "diagnostics_export_in_progress",
  "retry_after_seconds": <est>}`. Single-flight lock per process. Two
  near-simultaneous clicks see exactly one tarball, not two — the
  second gets 429. AC-13 covers this.
- **Bundle exceeds the size cap** (default 50 MB compressed,
  configurable via `DIAGNOSTICS_MAX_BYTES`): the assembler truncates
  oversize sections in section-priority order (logs first, then
  systemd journals, then ffmpeg stderr) and writes the truncation
  into the manifest. The tarball is delivered with the manifest
  flagging which sections were trimmed. AC-9 + AC-10 cover this. The
  caller is **not** silently shorted — the manifest is authoritative.
- **`vcgencmd` / `journalctl` / `ip` fails or times out**: per-tool
  timeouts (5 s for `vcgencmd`, 30 s for `journalctl`, 5 s for `ip`)
  bound the run. On failure, the section file is written with
  `<command failed: <reason>>` and the manifest's section.error is
  set. The bundle is still delivered — partial diagnostics are more
  useful than no diagnostics. AC-11 covers this.
- **Total collection budget exceeded** (60 s default,
  `DIAGNOSTICS_TIMEOUT_SECONDS`): the assembler aborts further
  sections, gzips what it has, and adds an `aborted: true` flag in
  the manifest. The tarball is still delivered.
- **`/data/config/diagnostics-staging/` cannot be created** (disk
  full): 503 with body
  `{"error": "diagnostics_staging_failed", "detail": "no space"}`.
  No partial bundle is delivered. Audit event
  `DIAGNOSTICS_EXPORT_FAILED` fires with reason. The dashboard's
  storage warning (existing) already surfaces the underlying cause.
- **A non-Linux host (CI / dev machine)**: `vcgencmd` is absent; the
  hardware section's per-tool fallback writes `<command not
  available on this platform>` and the test-bench bundle is still
  produced. AC-12 covers this. (We do not skip the route entirely
  because integration tests need to drive it.)
- **Catastrophic assembler error** (uncaught exception inside the
  service): 500 with body `{"error": "diagnostics_export_failed"}`.
  The staging dir is unlinked. `DIAGNOSTICS_EXPORT_FAILED` audit
  event fires with `detail` truncated to 256 chars; full traceback
  goes to `monitor.log` (not the bundle that triggered the failure
  — that bundle does not exist).
- **Operator clicks while logged-in as the only admin and the
  session has just expired**: the POST returns 401; the button
  surfaces "Session expired, log in again" and triggers a re-auth
  flow consistent with every other admin POST in the app.
- **Tarball's filename contains a hostname with non-ASCII or
  unsafe characters**: filenames are sanitised through a strict
  allowlist (`[A-Za-z0-9._-]`, max 64 chars after slug-collapse) so
  no client-controlled string reaches the response header. AC-14
  covers this. (Hostname is not directly client-controlled but
  `Settings.hostname` is — sanitisation defends against history.)
- **Symbolic links inside `/data/logs/`**: the assembler resolves
  symlinks before reading and refuses any file whose resolved path
  escapes `/data/logs/` (defence against a hypothetical attacker who
  has shell access and plants a symlink to `/etc/shadow`). AC-15
  covers this.
- **Audit log was just rotated to 0 bytes by an admin clearing it
  (`AUDIT_LOG_CLEARED`)**: that's the legitimate post-clear state
  and the bundle ships the (sentinel-only) audit.log without
  comment. The `DIAGNOSTICS_EXPORTED` event itself is recorded after
  the clear, so the audit trail of "X cleared the log, then Y
  exported diagnostics" survives.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: A new `POST /api/v1/system/diagnostics/export` endpoint is
  registered on `system_bp` and returns `application/gzip` with a
  `Content-Disposition: attachment; filename="hm-diagnostics-<host>-<UTC>.tar.gz"`
  header on success. The body is a valid gzipped tar archive.
  **[contract: response shape + header + tarfile.is_tarfile() check]**
- AC-2: The endpoint is admin-only. A viewer session gets 403; an
  unauthenticated request gets 401; a stale CSRF token gets 403.
  **[security test: TestDiagnosticsExportAuth (auth + role + CSRF)]**
- AC-3: A successful export emits a `DIAGNOSTICS_EXPORTED` audit
  event whose detail records `{bytes, sections, duration_ms,
  truncated_sections, aborted}`. A failed export emits
  `DIAGNOSTICS_EXPORT_FAILED` with the reason (no traceback in the
  audit detail; it goes to `monitor.log`).
  **[unit + audit assertion]**
- AC-4: The tarball contains a top-level directory named
  `hm-diagnostics-<host>-<UTC>/` with the section subtrees defined in
  User-Facing Behavior (`logs/`, `config/`, `hardware/`, `network/`,
  `systemd/`, `identity/`) and a `manifest.json` at the top level.
  Empty subtrees are omitted from the tarball but recorded in the
  manifest with `file_count=0` and an `error` if applicable.
  **[unit: parse the bundle and assert the structure]**
- AC-5: `manifest.json` validates against a schema with these required
  keys: `bundle_version` (int), `generated_at` (UTC ISO Z), `host`
  (str), `firmware_version` (str), `requested_by` (str — never an IP
  alone, never a password hash, never a session cookie), `sections`
  (list of `{name, file_count, byte_size, truncated, error}`),
  `redactions` (list of `{file, fields}`), `tool_versions`
  (dict of `{tool: version_string}`).
  **[unit: jsonschema-style assertion]**
- AC-6: Scrubbed `config/users.json` does NOT contain
  `password_hash`, `totp_secret`, or `recovery_code_hashes` — all
  three are present as keys with the literal value `"[REDACTED]"`.
  Scrubbed `config/cameras.json` does NOT contain `pairing_secret`.
  Scrubbed `config/settings.json` does NOT contain
  `tailscale_auth_key`, `webhook_destinations[].secret`, or
  `webhook_destinations[].custom_headers`.
  **[security test: TestDiagnosticsRedaction parametrised over
  every redacted field; both presence-of-key + value-is-REDACTED
  asserts]**
- AC-7: The bundle does NOT contain any file from `/data/certs/`
  (TLS private keys), `/data/config/.secret_key` (Flask session
  key), or any path inside `/data/recordings/` (clip data).
  **[security test: walk every entry in the tarball, assert no
  match against a deny-glob list]**
- AC-8: The bundle does NOT contain any environment variable dump,
  `os.environ` snapshot, or process argv listing.
  **[security test: walk every text file, regex against
  `MONITOR_DATA_DIR=`, `SECRET_KEY=`, `TAILSCALE_AUTH_KEY=`, and a
  small allowlist of expected env-shaped strings — none present]**
- AC-9: When the uncompressed bundle exceeds
  `DIAGNOSTICS_MAX_BYTES` (default 50 MB), oversize sections are
  truncated in priority order and the manifest's
  `sections[i].truncated` is `true` with `byte_size` reflecting the
  truncated size. The tarball still streams successfully.
  **[unit: stub a section to emit 100 MB; assert manifest +
  truncation behaviour]**
- AC-10: Per-section size caps are enforced (default: logs 20 MB,
  systemd 10 MB, ffmpeg 5 MB, hardware 1 MB, config 1 MB, network 1
  MB, identity 1 MB). Caps are configurable via `app.config`.
  **[unit]**
- AC-11: When `vcgencmd` exits non-zero or times out, the
  corresponding `hardware/vcgencmd-*.txt` contains
  `<command failed: <stderr-or-timeout>>` and `manifest.sections`
  records the per-file error. The bundle still streams.
  **[unit: monkeypatch subprocess.run to raise TimeoutExpired and to
  return non-zero; both surface gracefully]**
- AC-12: On a non-Linux host (no `vcgencmd`, no
  `/sys/class/thermal/`, no `journalctl`), the export still produces
  a valid bundle whose missing-tool sections are populated with
  `<command not available on this platform>` markers and whose
  manifest records the absence per section. The CI test environment
  drives this path.
  **[integration test on the existing test bench, which lacks Pi
  hardware]**
- AC-13: A second concurrent POST while a first is still assembling
  returns 429 with `{"error": "diagnostics_export_in_progress",
  "retry_after_seconds": <int>}`. Single-flight lock is per-process.
  **[integration: two threads, assert exactly one 200 + one 429]**
- AC-14: The download filename matches
  `^hm-diagnostics-[A-Za-z0-9._-]{1,64}-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.tar\.gz$`
  regardless of `Settings.hostname` content. Hostnames containing
  characters outside the allowlist are slug-collapsed; an empty
  result falls back to `host`.
  **[unit: parametrise hostname over `"unicode-名前"`, `"a/b\\c"`,
  `""`, `"normal"`; assert filename pattern]**
- AC-15: The log-collection step refuses any file whose resolved
  realpath does not start with `/data/logs/`. A planted symlink at
  `/data/logs/secret -> /etc/shadow` is skipped; the section's
  manifest entry records the skip with `error: "path escape"`.
  **[security test]**
- AC-16: The staging directory under
  `/data/config/diagnostics-staging/` is removed after the response
  is fully sent. A test that simulates client-disconnect mid-stream
  asserts the staging dir is still cleaned up by the fallback
  scheduled pass within 60 s.
  **[integration]**
- AC-17: `journalctl --since=-7d` is invoked per appliance unit
  (`monitor.service`, `mediamtx.service`, `camera-streamer.service`,
  `tailscaled.service`, `monitor-wifi-watchdog.service`,
  `monitor-hotspot.service`, `avahi-homemonitor.service`,
  `gpio-trigger.service`). Units that don't exist on the host (e.g.
  on the test bench) produce `<unit not present>` entries; they do
  NOT cause the export to fail.
  **[unit + integration]**
- AC-18: The diagnostics route is rate-limited to 6 requests / hour
  per session (the existing rate-limit decorator pattern, if not
  available, falls back to a simple counter on
  `app.diagnostics_service`). Rationale: an export is admin-only and
  rare; a stuck client retry-loop should not DoS the appliance.
  **[unit]**
- AC-19: The `manifest.json` `requested_by` field carries the
  username (`session.get("username", "")`) and the source IP
  (`request.remote_addr` after the existing ProxyFix at
  `__init__.py:122`); it does NOT carry the session cookie value or
  any other auth token.
  **[security test]**
- AC-20: Hardware smoke: on a Pi 4 with at least one paired camera,
  pressing **Export Diagnostics** produces a tarball whose
  `hardware/vcgencmd-get_throttled.txt` contains a value matching
  `/^throttled=0x[0-9a-fA-F]+$/`, whose `network/ip-addr.json`
  parses as JSON, and whose `systemd/monitor.journal.txt` is
  non-empty.
  **[hardware smoke entry in `scripts/smoke-test.sh`]**
- AC-21: A test fixture `cameras.json` containing every secret-bearing
  field with non-empty values is round-tripped through the redaction
  helper and asserted byte-for-byte to never leak a single secret
  value across all output sections (audit log content also walked,
  in case an audit entry's `detail` happened to embed a secret).
  **[security test: TestDiagnosticsRedactionFullSweep]**

## Non-Goals

- **Push to an external collector** (e.g. shipping diagnostics to a
  vendor S3 bucket): out of scope. The product is self-hosted; an
  outbound push of internal config is the wrong default. v2 could add
  optional outbound delivery behind an opt-in admin setting if
  operator demand emerges.
- **Real-time streaming of `journalctl -f` to the dashboard**: the
  issue body explicitly punts this to "use journalctl / SSH for
  that." Streaming logs is an `/api/v1/logs/tail` shape that needs
  WebSocket and back-pressure handling; not in this spec.
- **Automated PII redaction inside log lines**: we redact known
  secret-bearing JSON fields only (allowlist-driven). We do **not**
  attempt to find and redact credit-card numbers, IPs, IMSIs,
  email addresses, etc., inside `monitor.log` body — that is a
  bottomless task and the issue body explicitly punts it to operator
  responsibility ("provide scrubbing guidance in docs"). Operator
  guidance lives in the Diagnostics card description and a new
  `docs/guides/diagnostics-export.md` runbook.
- **Bundling video clips**: out of scope. Recordings are large, may
  contain protected scenes, and have their own download path
  (`api/recordings.py`). The bundle size cap (50 MB) is also
  intentionally too small to fit even one clip.
- **Encrypted / signed bundles**: out of scope for v1. The bundle
  travels over HTTPS to an authenticated admin's browser; at-rest
  protection is the operator's responsibility once it leaves the
  appliance. v2 could add admin-supplied passphrase-encryption (e.g.
  age-encrypt) if the threat model demands it.
- **A separate `/diagnostics` page in the dashboard**: ADR-0018 says
  this exists in the future; this spec does **not** build it. It
  builds the data assembler the future page will call. Adding a UI
  page now would balloon scope and pre-empt the ADR-0018 follow-up.
- **Selecting which sections to include from the UI**: v1 ships
  "everything (with caps)". A section-picker UI would inflate the
  Settings page for a button operators click maybe twice a year. v2
  can add it if support flows demand it.
- **Bundle history / re-download from server**: bundles are produced
  on demand and cleaned up. We do **not** persist them under a
  stable path with a "Past exports" list; that would create an
  unindexed, slowly-growing pile of secrets-adjacent data. Operators
  who need history keep their own copies.
- **Differential / delta bundles ("changes since last export")**:
  out of scope.
- **Including the Flask `SECRET_KEY` "to help debug session
  issues"**: explicitly forbidden (ADR-0022 — no backdoors). Session
  bugs are reproduced from log lines and the
  `monitor.auth._is_session_valid` event trail; never from the
  signing key.
- **Auto-running an export on crash**: out of scope. Crash dumps
  belong in a dedicated coredump pipeline; piggy-backing them on
  this manual surface conflates triggers.

## Module / File Impact List

**New code:**

- `app/server/monitor/services/diagnostics_bundle.py` (new) — the
  bundle assembler service. Public API:
  - `class DiagnosticsBundleService:`
    - `__init__(self, *, data_dir, config_dir, store, audit, max_bytes,
      timeout_seconds, section_caps, units)`
    - `collect_sections(self, *, requested_by, requested_ip) -> BundleResult`
      — pure assembler, returns a `BundleResult` dataclass with
      `staging_path`, `archive_path`, `archive_bytes`, `manifest`,
      `sections`. Single-flight enforced via `threading.Lock`.
    - `cleanup(self, run_id: str) -> None` — unlink staging.
  - Module-level helpers (kept private to this file unless reused):
    - `_collect_logs(...)`, `_collect_hardware(...)`,
      `_collect_systemd(...)`, `_collect_network(...)`,
      `_collect_identity(...)`, `_collect_config(redact_fn, store)`.
    - `_run_command(argv, *, timeout_seconds, cap_bytes) -> CommandResult`
      — wraps `subprocess.run` with the project's standard list-args,
      `shell=False`, capture, timeout, byte cap. Returns a
      `CommandResult(stdout, stderr, returncode, error)`.
- `app/server/monitor/utils/redact.py` (new) — `redact_secrets(obj,
  paths)` recursively scrubs an arbitrary JSON-serialisable object
  by JSONPath-like dotted/indexed paths, replacing matched leaves
  with the sentinel `"[REDACTED]"`. Pure function, no Flask import.
  Used here AND available for future re-use (e.g. by a future
  `/diagnostics` page that returns scrubbed JSON in-band).
  - `REDACT_PATHS` constants module-local:
    - `USERS = ["users[*].password_hash", "users[*].totp_secret",
      "users[*].recovery_code_hashes"]`
    - `CAMERAS = ["cameras[*].pairing_secret"]`
    - `SETTINGS = ["tailscale_auth_key", "webhook_destinations[*].secret",
      "webhook_destinations[*].custom_headers"]`
- `app/server/monitor/api/system.py` (extended) — one new route
  `POST /system/diagnostics/export` with `@admin_required` +
  `@csrf_protect`. Wire layout: route delegates to
  `current_app.diagnostics_service.collect_sections(...)`, then
  `send_file(archive_path, mimetype="application/gzip",
  as_attachment=True, download_name=...)`. The cleanup runs on
  `flask.after_this_request`.
- `app/server/tests/unit/test_diagnostics_bundle.py` (new) — tests:
  manifest schema, per-section truncation, command-failure paths,
  symlink-escape rejection, redaction round-trip, single-flight
  lock, filename sanitisation, non-Linux fallback.
- `app/server/tests/security/test_diagnostics_redaction.py` (new) —
  full-sweep redaction test (AC-21): walk every byte of the
  generated tarball and assert no fixture-known-secret value
  appears anywhere. Parametrised over secret fields.
- `app/server/tests/integration/test_api_diagnostics_export.py`
  (new) — endpoint contract: shape, headers, auth gates,
  concurrency 429, timeout / aborted manifest path.
- `docs/guides/diagnostics-export.md` (new) — operator runbook:
  what the bundle contains, what's redacted, how to share it
  responsibly, what NOT to send to public issue trackers (a
  redacted-but-still-sensitive checklist: hostnames, internal IP
  ranges, paired camera IDs, log lines that may contain personally
  identifying timestamps).

**Modified code:**

- `app/server/monitor/__init__.py:240` — `_init_services()`: one new
  block instantiating `DiagnosticsBundleService` with
  `data_dir=app.config["DATA_DIR"]`,
  `config_dir=app.config["CONFIG_DIR"]`, `store=app.store`,
  `audit=app.audit`, plus configurable caps from `app.config`.
  Attached as `app.diagnostics_service`.
- `app/server/monitor/__init__.py:127` — `app.config.update(...)`:
  add new keys `DIAGNOSTICS_MAX_BYTES=50 * 1024 * 1024`,
  `DIAGNOSTICS_TIMEOUT_SECONDS=60`,
  `DIAGNOSTICS_SECTION_CAPS={...}` (mapping),
  `DIAGNOSTICS_UNITS=["monitor.service", "mediamtx.service",
  "camera-streamer.service", ...]` so deployments can override per
  appliance variant (e.g. test bench with no camera-streamer unit).
- `app/server/monitor/services/audit.py:8` — extend the docstring
  event list to include `DIAGNOSTICS_EXPORTED` and
  `DIAGNOSTICS_EXPORT_FAILED`. (Audit constants in this codebase are
  raw strings passed into `log_event(event, ...)`; there's no
  `enum`/`constants` module to update — see `services/audit.py:56`.)
- `app/server/monitor/templates/settings.html` — add a new
  **Diagnostics** card inside the existing System tab body. Single
  button (`<button x-data x-on:click="exportDiagnostics()">`); a
  small Alpine helper that POSTs with the existing CSRF helper,
  awaits the blob, drives an anchor with `URL.createObjectURL` to
  trigger the download, and surfaces inline error messages on 4xx /
  5xx. Uses existing `.form-card` / `.button-primary` styling — no
  new CSS classes.
- `app/server/monitor/static/js/dashboard.js` (or
  `settings.html`'s inline Alpine, whichever the project uses for
  the System tab — verify in implementation; current pattern is
  inline Alpine per ADR-0012) — `exportDiagnostics()` helper.
- `app/server/tests/integration/test_api_system.py` — extend
  existing tests to assert the new route's auth gate behaviour
  alongside the existing `/system/health` and `/system/info` tests.
- `docs/traceability/traceability-matrix.md` — Implementer adds the
  rows for `UN-253` / `SYS-253` / `SWR-253-A..G` / `HAZ-253-1..10` /
  `SEC-253-A..D` / `THREAT-253-1..3` / `TC-253-AC-1..21`.

**Out-of-tree:**

- **No camera-side firmware change.** Diagnostics is server-only.
  The camera continues to run unchanged.
- **No Yocto recipe change.** Tools used (`vcgencmd`, `journalctl`,
  `df`, `ip`) are already in the base image (`vcgencmd` is part of
  `vc-graphics`; `journalctl` ships with systemd; `df` and `ip`
  ship with coreutils / iproute2). No new recipe, no new
  `.bbappend`, no new packagegroup entry.
- **No new external Python dependency.** `tarfile`, `gzip`,
  `subprocess`, `json`, `pathlib`, `shutil`, `threading` are all
  stdlib.
- **No data migration.** No schema change to any persisted JSON.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| Camera Python | n/a — no camera-side change |
| API contract | new contract tests for `POST /api/v1/system/diagnostics/export` (shape, headers, auth gates, CSRF, 429 single-flight) |
| Frontend / templates | browser-level smoke on Settings → System: button is visible only for admin, click produces a downloaded `.tar.gz`, error states render inline |
| Security-sensitive path | full server suite + the new `tests/security/test_diagnostics_redaction.py`. The change does NOT modify `**/auth/**` semantics (it consumes `@admin_required` + `@csrf_protect` unchanged), does NOT touch `**/secrets/**` or the certs directory (it explicitly excludes both), does NOT modify pairing / wifi / cert / OTA flows. |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Coverage | server `--cov-fail-under=85` (existing). New `diagnostics_bundle.py` has many small branches; expect ≥ 90 % via tests above. |
| Hardware behavior | deploy + `scripts/smoke-test.sh` row "Export Diagnostics; assert tarball includes a non-zero `monitor.journal.txt` and a parseable `vcgencmd-get_throttled.txt`." |
| Workflow / shell changes | none |
| Yocto config or recipe | none |

Smoke-test additions (Implementer wires concretely in
`scripts/smoke-test.sh`):

- "Log in as admin in a browser, open Settings → System, click
  Export Diagnostics, save the tarball, untar it, and confirm
  `manifest.json` parses, `logs/monitor.log` is non-empty, and
  `hardware/vcgencmd-get_throttled.txt` matches `^throttled=0x`."
- "Log in as a viewer; confirm the Export Diagnostics button is
  not rendered (or is disabled with the existing role-gate
  feedback)."
- "From the same admin session, click Export twice in quick
  succession; confirm the second attempt either queues correctly or
  surfaces the 429 'export in progress' message; confirm exactly
  one tarball lands."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-253-1 | An admin shares a diagnostics tarball with a third party (vendor support, GitHub issue) and the bundle leaks a password hash, TOTP secret, pairing key, TLS private key, Tailscale auth key, or webhook secret — silently giving the recipient persistent or escalation-grade access to the appliance or downstream systems. | Critical (security — credential disclosure) | Medium (operators routinely share bundles for support; the failure is 100 % of bundles if the redaction is wrong) | RC-253-1: server-side allowlist-driven `redact_secrets()` (AC-6) + a dedicated security test (AC-21) that walks every byte of the produced tarball against a fixture of known-non-empty secrets and fails the build on any match + an explicit deny-list (AC-7) covering `/data/certs/`, `/data/config/.secret_key`, and `/data/recordings/`. The test is parameterised so adding a new secret-bearing field elsewhere fails CI until the redact list is updated. SEC-253-B / SC-253-B carry the rule. |
| HAZ-253-2 | A viewer or unauthenticated client triggers an export and either downloads it or causes resource exhaustion on the appliance. | Major (security + availability) | Low | RC-253-2: `@admin_required` on the route (AC-2); test covers viewer, unauth, and stale-CSRF cases. The button is also hidden in the viewer UI (defence in depth), but the server-side gate is the load-bearing control. |
| HAZ-253-3 | The diagnostics service shells out with operator-controlled strings (e.g. hostname into `journalctl`, a path into `df`) and a malicious config value triggers command injection. | Critical (RCE) | Very Low (we never put user input into argv; argv is an in-source list of constants) | RC-253-3: every shell-out uses `subprocess.run(argv_list, shell=False, timeout=N)` with a hard-coded list of args — no f-strings, no `.format()`, no shell expansion. Hostnames and other user-controlled strings appear only in the OUTPUT (file content) of the bundle, not as argv. A unit test asserts `shell=False` and `argv` is a list-of-str for every `_run_command` site. SEC-253-A / SC-253-A. |
| HAZ-253-4 | The bundle includes a section path that follows a symlink the operator (or an attacker with shell) planted, exposing arbitrary host files — e.g. `/data/logs/secret -> /etc/shadow`. | Major | Very Low | RC-253-4: log collection refuses any file whose `os.path.realpath()` doesn't start with the configured logs dir (AC-15). Same boundary check guards `/data/config/` and `/data/certs/`. Test plants a symlink in a tmpdir-rooted logs dir and asserts the file is skipped with a manifest-recorded error. |
| HAZ-253-5 | A runaway log file (`monitor.log` having grown to GB-class because rotation broke) causes the export to consume all RAM or fill `/data` while staging. | Major (availability) | Low (rotation is configured; broken-rotation is rare) | RC-253-5: per-section size cap enforced during read (AC-10), total bundle cap (AC-9). Log reads are streamed (`shutil.copyfileobj` with `length` parameter), not slurped. The staging dir is on `/data` so it inherits the storage-low warning (existing) — if `/data` is critically low, the storage manager already pages the operator. |
| HAZ-253-6 | `journalctl` hangs (e.g. when systemd-journald is in a bad state), the export blocks the worker thread, and the dashboard becomes unresponsive. | Moderate (UX availability) | Low | RC-253-6: hard per-tool timeout (30 s for journalctl) AND a total-budget timeout (60 s) (AC-11, AC-17). On timeout, the section is written with `<command timed out>`, and the export proceeds. The route is also rate-limited (AC-18) to prevent retry storms from amplifying the symptom. |
| HAZ-253-7 | Two admins click Export simultaneously; both runs allocate staging dirs, contend on `journalctl`, and one of them ships a corrupt tarball. | Minor (UX) | Low | RC-253-7: per-process single-flight lock (`threading.Lock`) on `DiagnosticsBundleService`; the second caller gets 429 with `retry_after_seconds` (AC-13). |
| HAZ-253-8 | The audit event itself (`DIAGNOSTICS_EXPORTED`) accidentally records a session cookie or another secret in its `detail` field. | Major (audit-trail leak) | Very Low | RC-253-8: `detail` is built from a fixed schema — `bytes`, `sections`, `duration_ms`, `truncated_sections`, `aborted` — never includes request body, cookies, or `Authorization` headers. AC-19 + a security test covers it. |
| HAZ-253-9 | A future contributor adds a new secret-bearing field to `Settings` or `Camera` and forgets to add it to the redaction allowlist, silently leaking it on the next operator export. | Major (security regression) | Medium (Settings grows over time) | RC-253-9: the security test (AC-21) walks every byte of the bundle against a fixture of every non-empty secret-bearing field, which is itself **derived** from a model-introspection helper that lists `Field` annotations marked sensitive. Adding a sensitive field without updating the allowlist fails the test before merge. The pattern is documented in `docs/guides/diagnostics-export.md` and called out in the new fields' code comments. |
| HAZ-253-10 | The download filename includes a hostname that contains characters illegal in HTTP header values (e.g. CRLF), allowing response-splitting. | Major (security) | Very Low | RC-253-10: filename is sanitised through a strict allowlist regex (AC-14) before reaching the response header. Test parametrises over hostile hostnames including CRLF. |

Reference `docs/risk/hazard-analysis.md` for the existing register;
this spec adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs):

- **Sensitive paths touched:** the change does NOT modify
  `**/auth/**`, `**/secrets/**`, `**/.github/workflows/**`,
  `pairing.py`, `wifi.py`, certificate / TLS / OTA flow code, or
  `docs/cybersecurity/**`. The change is confined to:
  - `app/server/monitor/services/diagnostics_bundle.py` (new)
  - `app/server/monitor/utils/redact.py` (new)
  - `app/server/monitor/api/system.py` (one new route)
  - `app/server/monitor/__init__.py` (one new service wiring)
  - `app/server/monitor/templates/settings.html` (one new button)
  - `app/server/monitor/services/audit.py` (docstring only)
  - tests + docs
  The new code **reads** from sensitive paths (audit log, scrubbed
  config files) but only after `@admin_required` and only into
  redacted, capped artefacts. It does **not** modify any sensitive
  path's behaviour.
- **Net new attack surface:** one POST endpoint behind
  `@admin_required + @csrf_protect`, rate-limited (AC-18),
  single-flight (AC-13). Roughly equivalent to today's
  `/system/factory-reset` in posture (admin-only, CSRF-gated,
  destructive in different ways) — diagnostics is read-only-ish
  (it produces an artefact but does not mutate appliance state).
- **No new persisted secret material.** No tokens stored, no
  credentials persisted, no signing keys created.
- **Auth:** `@admin_required` (`auth.py:196`) + `@csrf_protect`
  (`auth.py:220`). Viewers (the only other role) are forbidden.
  Pre-auth surfaces never reference this route — error responses
  follow the existing envelope (`{"error": "<msg>"}`) and never
  leak that diagnostics exists. The Settings page only renders the
  button when `session.role == 'admin'`.
- **Input validation:** the route accepts no operator input (no
  body, no query string). The only inputs are `session["username"]`
  and `request.remote_addr`, both already vetted by the auth
  middleware. The bundle's filename is built server-side from
  `Settings.hostname` (sanitised — AC-14) and a UTC timestamp.
- **Subprocess invocation:** every shell-out is
  `subprocess.run(argv_list, shell=False, timeout=N,
  capture_output=True)` with an in-source `argv_list`. **No
  `shell=True` anywhere in the new code.** A unit test asserts this
  by inspecting every `_run_command(...)` call site (or by
  monkey-patching `subprocess.run` and asserting kwargs). SC-253-A
  carries the boundary.
- **Operator-controlled strings reaching argv:** none. Hostnames
  and similar appear only in **output** (the manifest, file
  contents) — not in any argv.
- **Redaction (load-bearing security control):**
  `redact_secrets(obj, paths)` walks the object using the dotted /
  indexed paths in `REDACT_PATHS.{USERS, CAMERAS, SETTINGS}` and
  replaces every match with the literal sentinel `"[REDACTED]"`. A
  separate test (AC-21) byte-walks the entire produced tarball and
  asserts no known fixture-secret value appears anywhere — covering
  cases where a secret might leak via an unexpected route (e.g. an
  audit log line that happened to include a webhook URL). SC-253-B.
- **Deny-list of paths:** even if redaction is correct, the
  assembler refuses to read from `/data/certs/`,
  `/data/config/.secret_key`, and `/data/recordings/`. AC-7. SC-253-C.
- **Audit completeness:** every export emits exactly one
  `DIAGNOSTICS_EXPORTED` (success) or `DIAGNOSTICS_EXPORT_FAILED`
  (failure) event with `bytes`, `sections`, `duration_ms`,
  `requested_by`, source IP. The detail field is structured (no
  free-form interpolation of request input). SC-253-D.
- **Outbound network:** none. The bundle is written to local
  staging and streamed back to the requesting client over the
  existing HTTPS session. The new code makes no outbound HTTP /
  DNS / SMTP call.
- **Resource exhaustion:** per-tool timeouts, per-section size
  caps, total bundle cap, total time budget, single-flight lock,
  per-session rate limit. The route cannot be used to DoS the
  appliance from a logged-in admin position any more easily than
  any other admin endpoint. The 429 single-flight response is
  cheap (no work).
- **At-rest protection of the staging artefact:**
  `/data/config/diagnostics-staging/` lives on the LUKS-encrypted
  data partition (ADR-0010). Cleanup happens after the response is
  fully sent (Flask `after_this_request`), with a fallback
  scheduled cleanup pass (AC-16) for client-disconnect cases.
- **At-rest protection of the bundle in the operator's hands:**
  out of scope for v1 (see Non-Goals). Documented in the runbook so
  operators know what they're holding.
- **No-backdoors compliance (ADR-0022):** the bundle never
  includes the Flask session signing key, never includes a
  password hash, never includes any TOTP material, never includes
  TLS private keys, never includes pairing secrets, never includes
  Tailscale auth keys, never includes webhook secrets. **An
  attacker who obtains a bundle gains the same diagnostic visibility
  the operator already has — not credential-grade access.** The
  bundle is by design as harmless as `journalctl --since=-7d`
  output a logged-in operator could collect by hand, plus
  scrubbed config, plus hardware metrics — no more.
- **Path traversal:** the assembler resolves every read path with
  `os.path.realpath()` and refuses any whose result escapes the
  expected prefix (AC-15). SC-253-A also covers any `tarfile.add()`
  call: tar entries are added with controlled `arcname=` values
  (a fixed in-source layout), never with operator-controlled paths.
- **CSRF on POST:** `@csrf_protect` (`auth.py:220`) — token must
  be present and valid; missing / stale tokens get 403.
- **Rate-limit interaction:** 6 requests / hour / session (AC-18).
  Lower than the 5-second control-channel rate-limit on cameras
  because export is heavier (60 s budget, 50 MB output) and rarer.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-253` — User need: "When my appliance is misbehaving, I want
  to collect a single sharable bundle of logs, configuration, and
  hardware state so I can troubleshoot it myself or hand it to
  someone who can — without having to SSH in or worry about
  accidentally leaking passwords, keys, or secrets."
- `SYS-253` — System requirement: "The system shall provide an
  admin-gated diagnostics-export action that produces a single
  timestamped tarball containing application + audit logs,
  scrubbed configuration snapshots, hardware health output,
  systemd journal slices for the appliance services, and network
  state, with allowlist-driven redaction of all secret-bearing
  fields and a hard-deny list for cryptographic and recording
  paths."
- `SWR-253-A` — Endpoint: `POST /api/v1/system/diagnostics/export`,
  admin-only, CSRF-protected, rate-limited, single-flight.
- `SWR-253-B` — Bundle layout: `manifest.json` plus
  `logs/`, `config/`, `hardware/`, `network/`, `systemd/`,
  `identity/` subtrees.
- `SWR-253-C` — Redaction: scrubbed copies of `users.json`,
  `cameras.json`, `settings.json` with secret leaves replaced by
  `"[REDACTED]"`; deny-list refuses `/data/certs/`,
  `/data/config/.secret_key`, `/data/recordings/`.
- `SWR-253-D` — Bounds: per-tool timeout, per-section size cap,
  total bundle cap, total time budget, all configurable from
  `app.config`.
- `SWR-253-E` — Manifest schema: bundle_version, generated_at,
  host, firmware_version, requested_by, sections, redactions,
  tool_versions.
- `SWR-253-F` — Audit: `DIAGNOSTICS_EXPORTED` and
  `DIAGNOSTICS_EXPORT_FAILED` events; structured detail (no
  request-body / cookie leaks).
- `SWR-253-G` — UI: a new **Diagnostics** card in Settings → System
  with one button (admin only) that triggers the download and
  surfaces inline failure messages.
- `SWA-253` — Software architecture item: "DiagnosticsBundleService
  is a service-layer module under `app/server/monitor/services/`
  that depends on store + audit + a pure `redact_secrets` helper;
  the route in `api/system.py` is thin (auth gate + service call +
  send_file)."
- `HAZ-253-1` ... `HAZ-253-10` — listed above.
- `RISK-253-1` ... `RISK-253-10` — one per hazard.
- `RC-253-1` ... `RC-253-10` — one per risk control listed above.
- `SEC-253-A` (subprocess argv hardening + path-realpath gate),
  `SEC-253-B` (allowlist redaction + byte-sweep test),
  `SEC-253-C` (path deny-list for certs / .secret_key / recordings),
  `SEC-253-D` (audit completeness — `DIAGNOSTICS_EXPORTED` /
  `DIAGNOSTICS_EXPORT_FAILED`).
- `THREAT-253-1` (admin shares bundle, secrets leak),
  `THREAT-253-2` (viewer / unauth tries to access),
  `THREAT-253-3` (command injection via shelled-out tools),
  `THREAT-253-4` (symlink / path-traversal via planted file),
  `THREAT-253-5` (response-splitting via hostile filename).
- `SC-253-1` ... `SC-253-N` — controls mapping to the threats above.
- `TC-253-AC-1` ... `TC-253-AC-21` — one test case per acceptance
  criterion above.

## Deployment Impact

- **Yocto rebuild needed: no.** All tools used (`vcgencmd`,
  `journalctl`, `df`, `ip`) are already in the base image. No new
  recipe, no new `.bbappend`, no new packagegroup change.
- **OTA path:** standard server-image OTA. On first boot of the
  new image:
  - The new `app.diagnostics_service` is wired during
    `_init_services()`.
  - The new POST route registers via the existing system
    blueprint.
  - The Settings → System tab gains the new Diagnostics card.
  - No existing endpoint shape changes; viewers see no behaviour
    delta; admins see one new button.
  - Cameras themselves require no update.
- **Hardware verification:** required (low-risk, read-only-ish).
  - Smoke entry: "Pair a camera, log in as admin, click Export
    Diagnostics, save the tarball, untar, confirm
    `manifest.json` parses and `vcgencmd-get_throttled.txt` matches
    `^throttled=0x`."
  - Smoke entry: "Log in as viewer, confirm Export Diagnostics
    button is hidden / disabled."
  - Smoke entry: "Click Export twice in quick succession; confirm
    the second attempt either queues or returns 429."
- **Default state on upgrade:** the new card is visible to admins
  immediately; the button is enabled. No action is required of
  operators; the feature is purely additive.
- **Disk-space impact:**
  - Persistent footprint: zero. No artefact is left on disk after
    a successful download.
  - Transient footprint: bounded by `DIAGNOSTICS_MAX_BYTES` (50 MB)
    plus the staging tar (≈ 1.5–2× that during compression).
    Allocated under `/data/config/`; the storage manager's existing
    threshold (90 %) protects against the corner case where the
    appliance is already near full.
- **CPU-time impact on Pi:** a single export is 5–60 s of mostly
  I/O-bound work. Pi 4 / Zero 2W both handle this without affecting
  live streaming because the Flask server runs in its own process
  (the camera and mediamtx are separate units).
- **Backwards compatibility:** no API change to any existing
  endpoint. Clients that don't know about the new endpoint
  continue to work unchanged.
- **Monitoring / alerting:** the existing audit log surfaces every
  export and every failure; no new monitoring path is added.

## Open Questions

(None of these are blocking; design proceeds. Implementer captures
answers in PR description.)

- **OQ-1: Should the bundle include a SHA-256 of every section
  file in the manifest** for tamper-evident handoff (so a recipient
  can verify the bundle wasn't modified between the appliance and
  them)? **Recommendation:** yes — cheap, useful for support
  workflows, and zero extra cost since we already iterate every
  file at tar-time. Add `sections[i].files: [{name, sha256, size}]`
  to the manifest schema in v1.
- **OQ-2: Should the timeout / size caps be per-deployment
  configurable via the existing `Settings` JSON, or only via
  environment variables / `app.config`?** **Recommendation:**
  `app.config` only for v1; not user-settable. These are appliance-
  protection bounds; exposing them as a setting invites operators
  to crank `DIAGNOSTICS_MAX_BYTES=10 GB` and discover their disk
  full at the worst possible moment. v2 can revisit if real
  operator demand emerges.
- **OQ-3: Should viewers see the Diagnostics card disabled with
  a tooltip ("Admin only") rather than hidden entirely?**
  **Recommendation:** hidden — consistent with the existing
  Tailscale `connect/disconnect` admin actions in the same tab.
  No information disclosure value in showing a disabled control.
- **OQ-4: Should the bundle include `pip freeze` / installed
  package list?** Useful for reproducing dependency-shaped bugs
  but adds 5–20 KB of text and arguably leaks our supply-chain
  surface. **Recommendation:** include — the package set is
  observable from `apt list --installed` to anyone with shell, and
  reproducing dependency bugs without it is painful. Add
  `identity/installed-packages.txt` (Yocto manifest if available,
  otherwise `dpkg -l` / `pip freeze`). Truncated to 500 KB.
- **OQ-5: Should the bundle include `dmesg` (kernel ring buffer)?**
  Useful for hardware bugs (USB resets, thermal throttling,
  `kworker` storms). May contain sensitive boot messages on some
  setups. **Recommendation:** include with a per-section size cap
  (1 MB), redacted only by allowlist (no per-line scrubbing — see
  Non-Goals). Operator runbook calls out that `dmesg` may include
  hardware-fingerprinting strings.
- **OQ-6: Should the dashboard show an in-page progress bar / log
  tail while the export is running**, or is the spinner-only UI
  enough? **Recommendation:** spinner-only for v1. The progress
  bar requires either a streaming response or a separate
  `/diagnostics/export/<run-id>/status` polling endpoint, both of
  which add complexity for a button that runs ≤ 60 s. Revisit when
  a future support workflow makes "X of N sections" actually
  useful.
- **OQ-7: Should the bundle include a `journalctl --boot=-1` slice
  (previous boot's journal)** to help diagnose crash → reboot
  cycles? **Recommendation:** include only the current boot's
  journal in v1; previous-boot journals are persisted by systemd
  only on some deployments and add I/O cost. Operators with
  reboot-cycle bugs can re-export after a reboot to capture both
  sides.

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): the assembler is a
  service module; the route stays thin.
- Preserve the modular monolith (ADR-0006): the assembler runs
  in-process; no new daemon, no new socket, no IPC.
- Preserve no-backdoors policy (ADR-0022): the bundle never
  includes any credential, key, secret, or session-signing key —
  enforced by both the allowlist redaction (configs) and the
  hard deny-list (paths).
- Preserve LUKS-at-rest (ADR-0010): staging happens under `/data`,
  never `/tmp`.
- Preserve subprocess discipline (`cert_service.py:112`,
  `__init__.py:402`): list args, no `shell=True`, explicit
  timeouts, captured output. No exception to that pattern in this
  spec.
- Add no new external Python dependency.
- The bundle layout is **versioned**: `manifest.bundle_version: 1`.
  Future schema bumps add fields rather than rename them; readers
  ignore unknown sections. Documented in
  `docs/guides/diagnostics-export.md`.
- Tests + docs ship in the same PR as code, per
  `engineering-standards.md`.
- Traceability matrix updated in the same PR; `python
  tools/traceability/check_traceability.py` must pass.
- The redaction allowlist is the single source of truth for what
  is sensitive. Adding a new sensitive field to a model requires
  adding it to `REDACT_PATHS` in `utils/redact.py` AND adding a
  fixture entry to the byte-sweep security test
  (`tests/security/test_diagnostics_redaction.py`); CI fails if
  either is missing. This is the load-bearing review-time control
  for HAZ-253-9.
