# Feature Spec: systemd watchdog (sd_notify WATCHDOG=1) for camera-streamer and monitor-server

Tracking issue: #249. Branch: `feature/249-systemd-watchdog`.

## Title

Active liveness via systemd's watchdog protocol for `camera-streamer.service`
and `monitor.service`: `Type=notify`, `WatchdogSec=`, gated `WATCHDOG=1`
heartbeats tied to actual main-loop progress (not a free-running timer).

## Goal

A camera-streamer or monitor-server process that hangs without crashing
(deadlocked thread, blocked GIL on a stuck syscall, libcamera pipeline wedged
but the process still alive, Werkzeug worker stuck in a long handler) is
automatically killed and restarted by systemd. Operators get the same
self-healing behaviour they expect from Home Assistant Supervisor or Frigate's
process supervisor — a freeze becomes a 30–60-second restart, not a silent
brick.

Concretely:

- Each service declares `Type=notify`, `NotifyAccess=main`, and `WatchdogSec=`
  in its unit file.
- Each process emits `READY=1` exactly once when its primary work loop is
  actually doing work (camera lifecycle in `RUNNING` and stream up; server
  WSGI listener answering its own loopback `/healthz` probe).
- Each process emits `WATCHDOG=1` periodically — but only when proof of
  forward progress exists. The camera notifier checks a `last_alive_at`
  timestamp updated by the lifecycle main loop. The server notifier issues
  a real loopback HTTP probe to a new `/healthz` route and only pings on
  HTTP 200.
- Each process emits `STOPPING=1` in its signal handler before teardown so
  systemd does not misclassify a clean shutdown as a watchdog kill.
- `Restart=always` + `StartLimitIntervalSec=300` / `StartLimitBurst=5` bound
  the restart loop: chronic wedge → unit enters `failed`, audit trail visible
  in `journalctl -u <unit>`. No infinite reboot loops.

This is the second leg of the camera-state-reconciliation work
(ADR-0026: desired vs observed state) and the same gap one layer down at the
process level. ADR-0023 (unified fault framework) treats process death as a
fault surface; this closes the silent-freeze hole in that surface. Per
`docs/ai/mission-and-goals.md`, a monitor that silently freezes is worse than
one that crashes — a crash restarts; a freeze stays broken until a human
notices.

## Context

Existing code this feature must build on, not replace:

- `app/camera/camera_streamer/health.py:115` — `HealthMonitor._notify_watchdog`
  already opens the `NOTIFY_SOCKET` Unix datagram socket and sends raw
  `WATCHDOG=1`. **It is a free-running timer** (the 15s health loop pings
  unconditionally on every iteration). The unit file has no `WatchdogSec=`,
  so the kernel-side timer is never armed and the ping is a no-op today.
  This spec keeps the socket pattern but moves ownership into a new module
  and makes the ping conditional on lifecycle progress.
- `app/camera/camera_streamer/main.py:34` — `_handle_signal` already arms an
  8-second forced-exit timer (`_SHUTDOWN_WATCHDOG_SECONDS = 8.0`) so a stuck
  teardown still exits the process. The new `STOPPING=1` notification is
  emitted *before* this forced-exit timer arms, so systemd sees a clean
  intent-to-stop signal first.
- `app/camera/camera_streamer/lifecycle.py:464` — `_do_running()` enters the
  operational state and ends in `while not self._is_shutdown(): time.sleep(1)`
  (line 554). This is the natural site for a `notifier.beat()` call: each
  pass through that loop is the last evidence of forward progress before the
  process blocks again.
- `app/camera/camera_streamer/heartbeat.py:138` — `HeartbeatSender` is the
  *network* heartbeat that posts HMAC-signed beats to the server every 15s
  (ADR-0016). **It is unrelated to systemd's watchdog.** This spec keeps
  separate names and a separate module so the two never get confused: the
  network heartbeat proves "camera ↔ server" reachability; the systemd
  watchdog proves "camera process is making progress." Both can fail
  independently and both must be visible in audit/journals.
- `app/server/monitor/__init__.py:110` — `create_app` is the app-factory
  (ADR-0001/0003). `_startup()` (line 414) is where long-lived background
  threads are spawned (`streaming`, `cert_service`, `recording_scheduler`,
  `loop_recorder`, mDNS browser, staleness checker). The new
  `WatchdogNotifier` is started here, next to `cert_service.start()`.
- `app/server/monitor/api/system.py:71` — existing `/system/health` endpoint
  is **admin-authenticated** and returns rich internal state. We are not
  reusing it: the systemd liveness probe needs an unauthenticated, fast,
  side-effect-free endpoint. New `/healthz` is a separate surface.
- `app/server/config/monitor.service:20` — current `ExecStart` runs the
  Flask development server (`flask --app monitor run`). Werkzeug does not
  natively support systemd notifications. We work around this with an
  in-process notifier thread that issues a real loopback HTTP request to
  `/healthz` against the running listener; on HTTP 200 it pings systemd.
  This is honest about what "alive" means under the dev server and works
  identically if/when we migrate to Gunicorn (which has native notify
  support — see OQ-3).
- `meta-home-monitor/recipes-camera/camera-streamer/camera-streamer_1.0.bb`
  and `meta-home-monitor/recipes-monitor/monitor-server/monitor-server_1.0.bb`
  — install the unit files via `${systemd_system_unitdir}` and currently do
  *not* depend on `python3-systemd`. We deliberately do not add that dep
  (see OQ-2): the existing `health.py` socket idiom is ~10 lines and we
  reuse it, so RDEPENDS does not change.
- ADRs anchoring the choices: ADR-0001 (app factory), ADR-0003 (service-
  layer), ADR-0004 (camera lifecycle state machine — watchdog ties to its
  RUNNING state), ADR-0006 (modular monolith — notifier is a thread inside
  each service, no new daemon), ADR-0023 (unified fault framework — restart
  loop counts feed the existing fault surface), ADR-0026 (desired vs
  observed state — process-level reconciliation complement to camera-state
  reconciliation).

## User-Facing Behavior

### Primary path — camera-streamer self-heal on freeze

1. Operator boots the camera image. systemd starts `camera-streamer.service`
   with `Type=notify` and `WatchdogSec=30`.
2. Camera lifecycle progresses INIT → SETUP → PAIRING → CONNECTING →
   VALIDATING → RUNNING. When lifecycle enters RUNNING and the stream is up
   (or the hotspot is up in pairing mode), the notifier sends `READY=1` exactly
   once. systemctl shows `Active: active (running) since …`.
3. Lifecycle main loop (`_do_running` while-block, line 554) calls
   `notifier.beat("lifecycle")` once per second. Notifier thread, running on
   its own daemon thread, wakes every `WatchdogSec / 3` (default 10s),
   confirms the lifecycle beat is fresh (`< WatchdogSec * 0.5` seconds old),
   and sends `WATCHDOG=1`.
4. systemd's `WatchdogSec=` timer is reset on every `WATCHDOG=1`. The unit
   stays alive.
5. If the lifecycle main loop wedges (e.g., GIL-stuck on a libcamera ioctl,
   capture thread deadlocked, motion runner spin-locked), the lifecycle beat
   stops being refreshed. The notifier thread observes the stale timestamp
   and **stops sending** `WATCHDOG=1`. After `WatchdogSec` seconds elapsed,
   systemd kills the unit (SIGKILL).
6. `Restart=always` + `RestartSec=5` brings the unit back. The 8-second
   forced-exit timer in `_handle_signal` is preserved for SIGTERM-but-stuck
   shutdowns.
7. Operator sees in `journalctl -u camera-streamer`:
   `systemd[1]: camera-streamer.service: Watchdog timeout (limit 30s)!` →
   `systemd[1]: camera-streamer.service: Killing process` →
   `systemd[1]: camera-streamer.service: Scheduled restart job`.

### Primary path — monitor-server self-heal on freeze

1. systemd starts `monitor.service` with `Type=notify` and `WatchdogSec=60`
   (server is slower to first probe than the camera lifecycle).
2. `create_app` → `_startup()` instantiates `WatchdogNotifier` and starts its
   background thread before the WSGI listener begins accepting requests.
3. Notifier thread issues a `GET http://127.0.0.1:5000/healthz` with a 2-second
   timeout. The `/healthz` route is unauthenticated, returns HTTP 200 with
   body `ok\n`, performs no DB read, no template render, no audit write.
4. On the first successful probe, the notifier sends `READY=1`. systemctl
   shows the unit as `active (running)`. nginx, mediamtx ordering downstream
   of `monitor.service` now wait on a real liveness signal, not just "exec
   started".
5. Notifier thread continues probing every `WatchdogSec / 3` (default 20s).
   On HTTP 200 → send `WATCHDOG=1`. On non-200 / timeout → log at WARNING
   and **do not send** the watchdog ping; systemd's timer keeps counting.
6. If the WSGI listener freezes (worker thread stuck in a handler, no thread
   left to answer `/healthz`, GIL-blocked syscall), probes time out. After
   `WatchdogSec` seconds with no `WATCHDOG=1`, systemd kills and restarts.

### Primary path — graceful shutdown

1. Operator runs `systemctl stop camera-streamer` (or the device powers off).
2. systemd sends SIGTERM. The existing `_handle_signal` runs.
3. **First action**: notifier sends `STOPPING=1` so systemd flags the stop as
   intentional and disarms the watchdog timer for this cycle.
4. Existing 8-second forced-exit timer arms (unchanged).
5. Lifecycle teardown runs: capture stop, stream stop, heartbeat stop, health
   stop, notifier stop.
6. Process exits cleanly. systemd records a clean stop in the journal.
7. Same flow for the server: `_startup`'s atexit (or a teardown hook on the
   Flask app) calls `notifier.stop(stopping=True)`.

### Failure states (must be designed, not just unit-tested)

- **`NOTIFY_SOCKET` unset** (running outside systemd, e.g., dev shell, CI,
  pytest) → notifier no-ops cleanly. No `READY=1`, no `WATCHDOG=1`, no
  `STOPPING=1`, no error logs at startup, no thread aborts. The same module
  must be safe to import in unit tests without monkey-patching.
- **`WATCHDOG_USEC` env unset** but unit file has `WatchdogSec=` → systemd
  populates `WATCHDOG_USEC` automatically; if it is missing for any reason
  (manual `systemctl daemon-reload` mismatch), the notifier falls back to a
  30-second default and logs once at WARNING.
- **Lifecycle wedged but notifier thread alive** (the case the existing free-
  running `health.py:_notify_watchdog` mishandles today) → notifier sees
  stale `last_alive_at`, **withholds** `WATCHDOG=1`. Logs at INFO once per
  10s: `liveness gate withheld: lifecycle stale (age=Ns)`. systemd kills the
  unit on `WatchdogSec` expiry. This is the load-bearing correctness
  property of the design.
- **Notifier thread itself crashes** → caught at the `_run_loop` boundary,
  logged at ERROR with traceback. The thread re-spawns on next iteration via
  the supervising start path; if it cannot re-spawn, no `WATCHDOG=1` is sent
  and systemd kills the unit anyway. Crash is not silent.
- **Loopback probe race during boot** (server) → before the WSGI listener is
  bound, the probe times out. Notifier waits up to 30s after start before
  declaring failure; during this grace period it does not send `READY=1`
  but also does not send `WATCHDOG=1`. systemd's `TimeoutStartSec=` (default
  90s under `Type=notify`) governs the boot deadline.
- **Probe succeeds but `/healthz` is mounted on a path returning HTML or
  HTTP 302** → unit-test-enforced contract: route returns `text/plain`,
  `200`, body `ok\n`. Any drift is a regression.
- **Watchdog-kill restart loop after a real bug** → `StartLimitBurst=5` over
  `StartLimitIntervalSec=300` caps the loop at 5 restarts in 5 minutes. After
  that, the unit enters `failed` state and stays there. Operator must
  intervene (`systemctl reset-failed` after fixing). Audit-visible via
  journal.
- **`/healthz` accidentally exposed via nginx** → unit test asserts no nginx
  config change in this PR; `/healthz` is bound to `127.0.0.1:5000` like the
  rest of the dev-server surface and never proxied through nginx.
- **OTA in-progress vs watchdog kill** → ADR-0008 A/B rollback is unaffected
  because the watchdog only kills the *running* slot's process. The OTA
  installer (`camera-ota-installer.service`) is a separate unit and does not
  inherit this watchdog. swupdate's existing rollback timer covers the case
  where the *new* slot wedges on first boot.

## Acceptance Criteria

Each bullet is testable; verification mechanism is in brackets.

- AC-1: `app/camera/config/camera-streamer.service` declares `Type=notify`,
  `NotifyAccess=main`, `WatchdogSec=30`, keeps `Restart=always` and
  `RestartSec=5`, and adds `StartLimitIntervalSec=300`,
  `StartLimitBurst=5`.
  **[unit + `systemd-analyze verify` in Yocto build]**
- AC-2: `app/server/config/monitor.service` declares `Type=notify`,
  `NotifyAccess=main`, `WatchdogSec=60`, keeps `Restart=always` and
  `RestartSec=5`, and adds `StartLimitIntervalSec=300`,
  `StartLimitBurst=5`.
  **[unit + `systemd-analyze verify`]**
- AC-3: A new module `app/camera/camera_streamer/sd_notify.py` exposes
  `notify(message: bytes) -> None` and constants `READY`, `STOPPING`,
  `WATCHDOG`. It connects to `NOTIFY_SOCKET` (handling the abstract `@/…`
  prefix per the existing `health.py` pattern), sends the message, and is a
  no-op when the env var is unset. Errors are caught and logged at DEBUG, not
  raised.
  **[unit]**
- AC-4: A new class `app/camera/camera_streamer/watchdog_notifier.py:
  WatchdogNotifier` exposes `start()`, `beat(component: str = "lifecycle")`,
  `mark_ready()`, `stop(stopping: bool = True)`. `start()` spawns a daemon
  thread that wakes every `interval = WATCHDOG_USEC // 3` microseconds
  (default 10s when `WATCHDOG_USEC` absent), checks `last_beat_at` is fresh
  (`< WATCHDOG_USEC * 0.5`), and sends `WATCHDOG=1` only if fresh.
  **[unit with mocked clock + monkey-patched env]**
- AC-5: `app/camera/camera_streamer/main.py` instantiates `WatchdogNotifier`
  before lifecycle.run() returns, calls `notifier.mark_ready()` once
  lifecycle reports `RUNNING` and stream is up (or hotspot is up in pairing
  mode), and calls `notifier.stop(stopping=True)` from `_handle_signal`
  before the existing forced-exit timer arms.
  **[unit + integration]**
- AC-6: `app/camera/camera_streamer/lifecycle.py:_do_running` calls
  `notifier.beat("lifecycle")` from inside the `while not self._is_shutdown()`
  poll loop, at least once per second.
  **[unit]**
- AC-7: When the lifecycle beat goes stale (`last_beat_at` more than
  `WatchdogSec * 0.5` ago, simulated by freezing the clock), the notifier
  thread does **not** send `WATCHDOG=1`, and logs the gate-withheld line at
  INFO once per 10s.
  **[unit with frozen clock]**
- AC-8: `app/camera/camera_streamer/health.py` no longer calls
  `_notify_watchdog`; that method is deleted (the watchdog responsibility is
  exclusively `WatchdogNotifier`'s). `HealthMonitor` calls
  `notifier.beat("health")` to record health-thread freshness without
  coupling watchdog correctness to it.
  **[unit + regression: grep guard against `_notify_watchdog`]**
- AC-9: A new module `app/server/monitor/services/watchdog_notifier.py:
  WatchdogNotifier` exposes the same surface as the camera notifier, but its
  thread issues `urllib.request.urlopen("http://127.0.0.1:5000/healthz",
  timeout=2)` (host/port read from `app.config["WATCHDOG_PROBE_URL"]` with
  that default) and only sends `WATCHDOG=1` on HTTP 200 with body `ok\n`.
  **[unit + integration with embedded WSGI server]**
- AC-10: A new public route `GET /healthz` is registered (preferably in a
  new tiny blueprint `app/server/monitor/api/healthz.py`). It is **not**
  protected by `@admin_required` or CSRF. It returns HTTP 200,
  `Content-Type: text/plain`, body `ok\n`. It performs no DB read, no audit
  write, no template render, no settings load.
  **[unit, contract test, manual]**
- AC-11: `/healthz` discloses no internal state — no version string, no
  hostname, no IP, no camera count, no settings flags. The body is the
  literal `ok\n`.
  **[unit, contract test asserting exact body and headers]**
- AC-12: `app/server/monitor/__init__.py:_startup` instantiates
  `WatchdogNotifier`, starts its thread, and registers `notifier.stop`
  on `app.teardown_appcontext` (or via `atexit`) so a clean Flask shutdown
  emits `STOPPING=1`.
  **[unit + integration]**
- AC-13: Server notifier sends `READY=1` exactly once after the first
  successful loopback probe (HTTP 200 from `/healthz`); subsequent successful
  probes only send `WATCHDOG=1`.
  **[unit with mocked HTTP]**
- AC-14: Server notifier withholds `WATCHDOG=1` on probe timeout / non-200
  response; logs `liveness gate withheld: probe timeout` at WARNING no more
  than once per 30s.
  **[unit + integration with thread-block injection]**
- AC-15: When `NOTIFY_SOCKET` is unset, both notifiers no-op cleanly: thread
  starts, beats are recorded, but no socket calls are made. No errors logged
  at startup beyond a single DEBUG line.
  **[unit]**
- AC-16: Notifier handles abstract socket addresses (`@/run/systemd/notify`)
  by translating leading `@` to `\0`, matching the existing
  `health.py:_notify_watchdog` behaviour.
  **[unit]**
- AC-17: Notifier thread catches and logs (at ERROR) any exception inside
  `_run_loop`; does not propagate; the thread is restarted on next `start()`
  call. A panic in the notifier never propagates into the camera lifecycle
  or the Flask request path.
  **[unit + integration with raise injection]**
- AC-18: `WATCHDOG_USEC` env (set automatically by systemd from
  `WatchdogSec=`) is honoured when present. When absent, the notifier
  defaults to `30_000_000` microseconds (30s) and logs once at INFO.
  **[unit]**
- AC-19: The camera notifier's interval is computed as
  `max(1, WATCHDOG_USEC // 3 // 1_000_000)` seconds, so at the default
  `WatchdogSec=30` the ping fires every 10s (well below the kernel timer's
  expiry).
  **[unit]**
- AC-20: Both services emit a single INFO log line `liveness ready` when
  `READY=1` is sent and `liveness shutting down` when `STOPPING=1` is sent.
  No PII, no hostname, no IP — just the marker lines so journal readers can
  grep.
  **[unit]**
- AC-21: `Restart=always` is preserved; watchdog-triggered SIGKILLs are
  treated as ordinary unsuccessful exits and the unit restarts after
  `RestartSec=5`.
  **[Yocto + smoke]**
- AC-22: After 5 watchdog-kills within 300s, the unit enters `failed` state
  and stays there (no infinite reboot loop). Operator must
  `systemctl reset-failed` to recover.
  **[smoke]**
- AC-23: Yocto image build (`bitbake -p home-monitor-image-prod` and
  `bitbake -p home-camera-image-prod`) succeeds with the updated unit files;
  installed unit files pass `systemd-analyze verify` (run during image build
  or a smoke step).
  **[Yocto]**
- AC-24: Hardware smoke: `pkill -STOP $(pidof python3 | head -n1)` against
  the camera-streamer for `> WatchdogSec` triggers systemd to restart the
  unit, observable in `journalctl -u camera-streamer`.
  **[hardware smoke]**
- AC-25: Hardware smoke: a Werkzeug worker forced into a blocking sleep for
  `> WatchdogSec` causes loopback `/healthz` probes to time out and the unit
  is restarted by systemd.
  **[hardware smoke]**

## Non-Goals

- Hardware watchdog (`/dev/watchdog` on BCM2711). Different layer (kernel),
  different blast radius, deserves its own ADR. Out of scope per issue body.
- A cross-process "camera offline → server requests reboot" loop. That is
  ADR-0026 reconciliation territory, not process-level watchdog.
- Replacing the existing camera *network* heartbeat (`HeartbeatSender`,
  ADR-0016). The two systems answer different questions and stay separate.
- Replacing the existing admin-authenticated `/system/health` endpoint. We
  *add* `/healthz` next to it, with a tightly scoped contract.
- Migrating Flask dev server → Gunicorn. The loopback-probe pattern works on
  both; the migration is its own change with its own packaging story (see
  OQ-3).
- Adding a per-component liveness matrix (capture beats, stream beats,
  motion-runner beats). MVP is a single lifecycle beat for the camera and a
  single loopback probe for the server. Per-component gating is a follow-up
  if field data shows wedges that the lifecycle-loop tick misses (see OQ-5).
- Surfacing watchdog-restart counts in the dashboard / Alert Center. The
  systemd journal is the source of truth for v1; an ADR-0023 fault surface
  for "process restarted by watchdog" is a follow-up.
- Adding `python3-systemd` as a Yocto runtime dep. The existing socket idiom
  in `health.py` is sufficient; no new RDEPENDS in this PR (see OQ-2).
- Watchdog tuning per board profile (Pi 4 vs Pi 5). Single value per service
  is sufficient; revisit if slow boards trip false positives.

## Module / File Impact List

**New code:**

- `app/camera/camera_streamer/sd_notify.py` — pure helper. ~30 lines.
  Exports `notify(msg: bytes) -> None`, constants `READY = b"READY=1"`,
  `STOPPING = b"STOPPING=1"`, `WATCHDOG = b"WATCHDOG=1"`. No-op when
  `NOTIFY_SOCKET` unset. Handles abstract socket address. No threads, no
  state. Replicates the existing `health.py:_notify_watchdog` socket logic
  in one well-tested place so it can be reused by both processes (camera
  copy; server copy lives under `monitor/services/`).
- `app/camera/camera_streamer/watchdog_notifier.py` — `WatchdogNotifier`
  class. Owns the daemon thread, the `last_beat_at` timestamp dict
  (per-component), the freshness gate, and the `READY` / `WATCHDOG` /
  `STOPPING` send sequence. Pure business logic, no Flask import, no
  lifecycle import.
- `app/server/monitor/services/sd_notify.py` — server copy of the helper.
  Same surface as the camera one. We accept the small duplication rather
  than a shared package because `app/camera/` and `app/server/` are deployed
  to different filesystems on different processes.
- `app/server/monitor/services/watchdog_notifier.py` — `WatchdogNotifier`
  class for the server. Probe loop uses `urllib.request.urlopen` against
  `127.0.0.1:5000/healthz`. Configurable URL via `app.config
  ["WATCHDOG_PROBE_URL"]` for tests.
- `app/server/monitor/api/healthz.py` — minimal blueprint exposing
  `GET /healthz`. Registered at root path (no `/api/v1` prefix) so the URL
  is conventional. Returns `("ok\n", 200, {"Content-Type": "text/plain"})`.
  No imports beyond Flask `Blueprint`.
- `app/camera/tests/unit/test_sd_notify.py` — env-handling, abstract
  socket, no-op when unset, no exceptions raised on socket failure.
- `app/camera/tests/unit/test_watchdog_notifier.py` — interval computation,
  freshness gate withholds on stale beat, `READY`/`STOPPING` send semantics,
  thread safety, exception swallowing.
- `app/camera/tests/integration/test_lifecycle_watchdog.py` — lifecycle
  reaches RUNNING → `READY=1` observed; lifecycle wedged (mock `_do_running`
  to block) → no `WATCHDOG=1` observed.
- `app/server/tests/unit/test_sd_notify.py` — mirror of camera tests.
- `app/server/tests/unit/test_watchdog_notifier.py` — probe loop, READY on
  first 200, withhold on timeout, single READY emission.
- `app/server/tests/integration/test_healthz.py` — `/healthz` returns 200,
  body `ok\n`, no auth, no DB hit, no template render.
- `app/server/tests/contracts/test_api_contracts.py` — extend with the
  `/healthz` contract assertions (exact body, headers, no auth required,
  no leakage).
- `app/server/tests/integration/test_app_startup_watchdog.py` — `_startup`
  starts the notifier; thread issues at least one probe within 5s; READY
  observed exactly once.

**Modified code:**

- `app/camera/config/camera-streamer.service` — add `Type=notify`,
  `NotifyAccess=main`, `WatchdogSec=30`, `StartLimitIntervalSec=300`,
  `StartLimitBurst=5`. Keep existing `Restart=always`, `RestartSec=5`,
  hardening directives. Update top-of-file `# REQ:` annotation to add
  the new `SWR-249` placeholders per `medical-traceability.md`.
- `app/server/config/monitor.service` — same shape as camera unit; set
  `WatchdogSec=60`. Add `Type=notify`, `NotifyAccess=main`,
  `StartLimitIntervalSec=300`, `StartLimitBurst=5`.
- `app/camera/camera_streamer/main.py` — instantiate `WatchdogNotifier`
  early; pass it into `CameraLifecycle`; in `_handle_signal` call
  `notifier.stop(stopping=True)` before the existing forced-exit timer.
- `app/camera/camera_streamer/lifecycle.py` — accept optional `notifier`
  parameter on `CameraLifecycle.__init__`; in `_do_running`'s while-loop
  call `notifier.beat("lifecycle")`; call `notifier.mark_ready()` after
  `led.connected()` (line 550).
- `app/camera/camera_streamer/health.py` — delete `_notify_watchdog`
  method (lines 115–132); replace its sole call site (line 88) with
  `notifier.beat("health")` (notifier injected via constructor — same
  pattern as `thermal_path`).
- `app/server/monitor/__init__.py` — instantiate `WatchdogNotifier` in
  `_init_services`; call `notifier.start()` in `_startup` before the
  return from `create_app`; register `healthz_bp` in `_register_blueprints`.
- `app/server/monitor/api/__init__.py` — re-export `healthz_bp`.
- No change to nginx configs (`/healthz` stays on loopback).

**Yocto:**

- `meta-home-monitor/recipes-camera/camera-streamer/camera-streamer_1.0.bb`
  — no RDEPENDS change. The unit file is already in `SRC_URI`; bumping
  the recipe (or relying on file-stamp invalidation) picks up the new
  `Type=notify` directive on next `bitbake`.
- `meta-home-monitor/recipes-monitor/monitor-server/monitor-server_1.0.bb`
  — same. No RDEPENDS change.
- No change to packagegroups; no change to image manifests.
- No new Yocto layer.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md` "Validation Matrix":

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| Camera Python | `pytest app/camera/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | new contract test for `GET /healthz` (status, body, headers, no-auth) |
| Security-sensitive surface | `/healthz` is the only new public endpoint; contract test asserts exact body, no internal state, no JSON, no version, no headers beyond `Content-Type`. Threat model addendum for the `NOTIFY_SOCKET` write path. |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Yocto config or recipe | `bitbake -p` for `camera-streamer` and `monitor-server`; `bitbake -p home-monitor-image-prod` and `home-camera-image-prod`; `systemd-analyze verify <unit>` against the unit files (run inside the recipe `do_install` or as a separate Yocto check). |
| Hardware behaviour | deploy + `scripts/smoke-test.sh` rows below |

Smoke-test additions (Implementer to wire concretely):

- "camera-streamer announces READY to systemd within 60s of boot
  (`systemctl show camera-streamer -p ActiveEnterTimestamp`)."
- "monitor.service announces READY to systemd within 90s of boot."
- "`pkill -STOP $(pidof python3 | head -n1)` against camera-streamer for
  `WatchdogSec + 5` seconds triggers a watchdog-kill; unit restarts; journal
  shows `Watchdog timeout`."
- "Forcing the Werkzeug worker into a `time.sleep(120)` (test hook) causes
  `/healthz` probes to time out and the unit is restarted by systemd."
- "5 forced wedges in 5 minutes lands the unit in `failed`; systemctl shows
  `Result: start-limit-hit`."
- "Clean `systemctl stop` does NOT log a watchdog timeout; journal shows
  `STOPPING=1` was received before SIGTERM teardown."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-249-1 | Unit file declares `WatchdogSec=` but the process never sends `WATCHDOG=1` (e.g., `Type=simple` left in by mistake, or notifier import missing). Result: every unit gets killed every `WatchdogSec`. | Major (mission) | Low | RC-249-1: AC-1, AC-2, AC-21 enforce both `Type=notify` and the notifier wiring; integration test boots the service in a Yocto VM and asserts `READY=1` is received before `WatchdogSec` expires. |
| HAZ-249-2 | Free-running notifier thread sends `WATCHDOG=1` while the lifecycle main loop is wedged, masking a real freeze. (This is the bug today in `health.py:_notify_watchdog`.) | Major (mission) | Medium | RC-249-2: AC-7, AC-8, AC-14 — notifier gates on per-component beat freshness; the existing free-running ping is removed. Unit test forces a stale beat and asserts the notifier withholds. |
| HAZ-249-3 | Loopback HTTP probe loops back into the same WSGI worker and adds load proportional to `1 / probe_interval`. | Minor (operational) | Low | RC-249-3: AC-10, AC-11 keep `/healthz` cheap (no DB, no template, ~50µs path). Probe interval is `WatchdogSec / 3` (default 20s) — at most 3 probes/min. |
| HAZ-249-4 | `WatchdogSec` set too aggressively (e.g., 10s) so legitimate slow operations (camera reset, OTA prep, USB enumeration) trip false positives and the unit restart-loops. | Moderate (operational) | Medium | RC-249-4: 30s/60s defaults chosen with margin over the existing 15s health interval; AC-22's `StartLimitBurst=5` ensures any false-positive loop self-terminates instead of restart-spamming the journal. |
| HAZ-249-5 | Watchdog-kill restarts mask a real bug in the field (operator never investigates because it "comes back"). | Moderate (operational) | Medium | RC-249-5: every watchdog-kill is logged in `journalctl -u <unit>` with `Watchdog timeout`. ADR-0023 unified-fault-framework integration is a follow-up. RC-249-1's start-limit converts "silent restart loop" into "unit failed" within 5 minutes. |
| HAZ-249-6 | Notifier crashes silently in its own thread, no `WATCHDOG=1` is sent, but the operator believes the watchdog is armed. | Minor (operational) | Low | RC-249-6: AC-17 — notifier catches and logs at ERROR with traceback; thread re-spawn on next iteration. If the notifier dies, systemd kills the unit on `WatchdogSec` expiry anyway — failure is fail-safe. |
| HAZ-249-7 | `/healthz` becomes a public information-leak endpoint over time as future PRs "just add a version string". | Minor (security) | Medium | RC-249-7: AC-11 contract test pins exact body to `"ok\n"` and exact headers; any drift fails CI. Documented in the route docstring as load-bearing. |
| HAZ-249-8 | Watchdog-restart during OTA install conflicts with A/B rollback (process killed mid-install). | Minor (operational) | Low | RC-249-8: only the `monitor.service` runtime is watchdog-armed; the OTA installer (`camera-ota-installer.service`) is a separate one-shot unit and is unchanged. swupdate's existing rollback timer covers a wedge of the *new* slot's first boot. |
| HAZ-249-9 | Dev-loop confusion: developers running `python -m monitor.run` (no systemd) see notifier "doing nothing" and assume it is broken. | Minor (operational) | Medium | RC-249-9: AC-15 — when `NOTIFY_SOCKET` is unset, notifier emits a single DEBUG line at start (`"liveness disabled: NOTIFY_SOCKET unset"`). Not WARNING (would be noise); not silent (would be confusing). |

Reference `docs/risk/hazard-analysis.md` — this spec adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs in
`docs/cybersecurity/threat-model.md`):

- **Sensitive paths touched:** `app/camera/camera_streamer/lifecycle.py` is
  on the architect.md sensitive list (camera state machine). Change is
  additive — a new `notifier.beat()` call inside an existing while-loop and
  a constructor parameter — no state-machine semantics change. Per
  `docs/ai/roles/architect.md` flagged here for extra scrutiny on the impl
  PR. No `**/auth/**`, `**/secrets/**`, OTA flow, pairing flow, or
  workflow change.
- **New attack surface — `/healthz` (unauthenticated):** the only public
  surface added. Returns the literal bytes `ok\n` and nothing else. No DB
  access, no auth check (intentional — must work pre-login for systemd's
  loopback probe), no version disclosure. Contract test (AC-11) pins the
  exact body and headers as load-bearing.
  - **Pre-auth surface check** per `docs/ai/design-standards.md`: passes.
    `/healthz` discloses nothing an attacker did not already know (the
    server exists and is listening on the LAN — visible from any TCP scan).
- **New attack surface — `NOTIFY_SOCKET`:** outbound UNIX-datagram socket
  to a kernel-managed path. The kernel enforces `NotifyAccess=main` (only
  the main process can write). Even a compromised request handler cannot
  write to the socket from a worker thread on a different PID, because we
  set `NotifyAccess=main` and Werkzeug runs request handlers in the same
  process. No filesystem path is created by us; the socket lives in the
  kernel's abstract namespace.
- **`READY=1` / `WATCHDOG=1` payload:** static byte strings. No user data
  ever flows into the notifier. No injection surface.
- **Restart loop as DoS vector:** an attacker who can wedge the WSGI worker
  (e.g., via a slowloris-style hold on `/healthz`) could in principle force
  watchdog kills. Mitigation: AC-22 start-limit caps the loop to 5 restarts
  in 5 minutes. The same attacker could already wedge the dev server
  without triggering this code, so the threat is no worse than today.
  Tracked as `THREAT-249-2`.
- **Audit:** every restart event is journalctl-visible. Watchdog-restart
  count visible to operator via `systemctl show <unit> -p NRestarts`. No
  new audit-log entries inside the application; the systemd journal is the
  authority for v1.
- **No CORS / no public surface beyond `/healthz`.** No nginx exposure of
  `/healthz` (loopback-only). No new env-var read at runtime beyond
  `NOTIFY_SOCKET` and `WATCHDOG_USEC` (both systemd-set, both safe).

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-249` — User need: "When my monitor's camera or server process freezes
  silently, I want it to recover by itself within a minute, without me
  noticing."
- `SYS-249` — System requirement: "The system shall detect main-loop
  forward-progress failure in `camera-streamer` and `monitor-server` and
  recover by automatic process restart."
- `SWR-249-A` — `WatchdogNotifier` daemon-thread design (start, beat,
  ready, stop, freshness gate).
- `SWR-249-B` — Camera lifecycle wires the notifier; main-loop tick is the
  load-bearing freshness signal.
- `SWR-249-C` — Server `WatchdogNotifier` issues a real loopback HTTP
  probe to `/healthz` as the freshness signal.
- `SWR-249-D` — `/healthz` route contract: status 200, body `ok\n`, no
  auth, no internal disclosure.
- `SWR-249-E` — Unit-file declarations: `Type=notify`, `WatchdogSec=`,
  `StartLimitIntervalSec=`, `StartLimitBurst=`, `Restart=always`.
- `SWR-249-F` — Notifier no-op semantics outside systemd
  (`NOTIFY_SOCKET` unset).
- `SWR-249-G` — Existing `health.py` free-running watchdog ping is
  removed; watchdog responsibility is exclusively `WatchdogNotifier`'s.
- `SWA-249` — Software architecture item: "Per-process `WatchdogNotifier`
  service inside the existing service-layer pattern (ADR-0003); thread-
  inside-process per modular-monolith (ADR-0006); freshness gate is the
  load-bearing correctness property."
- `HAZ-249-1` … `HAZ-249-9` — listed above.
- `RISK-249-1` … `RISK-249-9` — one per hazard.
- `RC-249-1` … `RC-249-9` — one per risk control.
- `SEC-249-A` (no auth bypass via `/healthz` — pre-auth surface
  discloses nothing).
- `SEC-249-B` (`NOTIFY_SOCKET` write path is kernel-mediated and
  scoped via `NotifyAccess=main`).
- `SEC-249-C` (restart-loop DoS bounded by `StartLimitBurst`).
- `THREAT-249-1` (silent freeze masquerades as healthy → resolved by
  per-component freshness gate).
- `THREAT-249-2` (attacker triggers restart loop by wedging WSGI worker
  → bounded by start-limit; no worse than current state).
- `THREAT-249-3` (`/healthz` becomes information-leak surface over
  time → pinned by contract test).
- `SC-249-1` … `SC-249-N` — controls mapping to the threats above.
- `TC-249-AC-1` … `TC-249-AC-25` — one test case per acceptance criterion.

## Deployment Impact

- **Yocto rebuild needed: yes.** Unit-file changes only; no new
  RDEPENDS, no new layer. `bitbake -p` and a VM image build for both
  `home-monitor-image-prod` and `home-camera-image-prod` are required to
  pick up the new `Type=notify` directives on hardware.
- **OTA path: standard server image OTA** (ADR-0008 A/B rollback). On
  first boot of the new slot, both services emit `READY=1` once their main
  loop is up; if either fails to do so within `TimeoutStartSec=`
  (systemd default 90s), swupdate's existing rollback timer reverts to the
  old slot. This means the watchdog feature itself is rollback-safe: a bug
  in the notifier that prevents `READY=1` causes the same outcome as any
  other startup failure — A/B rollback.
- **Hardware verification: yes — required.** Smoke rows AC-24 and AC-25.
- **Default state on upgrade:** watchdog is **on** as soon as the new image
  installs. This is the point of the feature; no opt-in. Operators with
  legitimately slow boards (PD: rare) can override via a drop-in unit at
  `/etc/systemd/system/<unit>.service.d/watchdog.conf` setting
  `WatchdogSec=` higher.
- **Backwards compatibility on a partial-merge:** if the unit file change
  lands before the application code change (or vice versa), one of two
  things happens — (a) `Type=notify` with no `READY=1` from the app:
  systemd waits `TimeoutStartSec=` (90s default) then declares startup
  failed and the unit restarts repeatedly; (b) `READY=1` from the app with
  `Type=simple` in the unit file: systemd ignores the notification, no
  watchdog timer is armed, behaviour matches today. To prevent (a), the
  PR ships unit-file change and code change atomically. The CI Yocto build
  must include a test that boots both services and asserts they reach
  `active (running)` within 60s.

## Open Questions

(None blocking; design proceeds.)

- OQ-1: `WatchdogSec` value — 30s (camera) and 60s (server), or a single
  shared 60s, or 30s for both?
  **Recommendation:** asymmetric defaults — 30s camera, 60s server. The
  camera lifecycle is event-loop driven and ticks once per second; 30s gives
  a 30× margin. The server's worst-case `/healthz` latency under load can be
  100ms+ on a Pi 4; 60s gives a 600× margin. Implementer revisits if smoke
  data shows false positives.
- OQ-2: Use `python3-systemd` (canonical, type-safe API) or in-tree socket
  helper (zero new RDEPENDS, ~30 lines)?
  **Recommendation:** in-tree helper. Matches the existing
  `health.py:_notify_watchdog` pattern; no new layer pull (although
  `meta-python` is already a dep — see `meta-home-monitor/conf/layer.conf`);
  one less version pin to track. Revisit if a future feature needs richer
  notification messages (`STATUS=`, `EXTEND_TIMEOUT_USEC=`, etc.).
- OQ-3: Migrate `monitor.service` from Werkzeug dev server to Gunicorn
  (which has `gunicorn --preload --type=notify` native support)?
  **Recommendation:** out of scope for #249. The loopback-probe pattern is
  honest about what the dev server can prove and works on Gunicorn too,
  unchanged, when we migrate. Tracked as a separate ticket; this spec is
  explicitly designed not to require that migration.
- OQ-4: Should the camera notifier gate on per-component beats (lifecycle
  + capture + stream + motion-runner), or only the lifecycle main-loop
  beat?
  **Recommendation:** MVP is lifecycle main-loop only. Per-component
  gating is a defensible follow-up if field data shows a wedge mode where
  the lifecycle ticks but capture is dead (and the network heartbeat
  hasn't already caught it). This avoids over-fitting the freshness gate
  on day 1.
- OQ-5: Should `/healthz` be `GET` and `HEAD` both (some watchdog frameworks
  prefer HEAD), or `GET` only?
  **Recommendation:** `GET` only. `urllib.request.urlopen` does GET by
  default; the body is 3 bytes. HEAD is a follow-up if a future probe needs
  it.
- OQ-6: Should we expose the notifier's last-beat timestamps via the
  existing admin `/system/health` endpoint so operators can see "lifecycle
  beat: 0.4s ago" without ssh-ing?
  **Recommendation:** yes, but in a follow-up PR. The ADR-0023 unified
  fault framework is the right place for "process restarted by watchdog"
  to surface to operators. v1 keeps the scope tight: journal + smoke
  tests are the operator's surface.
- OQ-7: `NotifyAccess=main` vs `NotifyAccess=all` — does the camera
  motion-runner subprocess (if any) need to send `WATCHDOG=1` from a
  different PID?
  **Recommendation:** `NotifyAccess=main`. Today motion-runner is a thread
  (not a subprocess) per the explorer survey. If the design later spawns
  a subprocess that owns the camera lifecycle main loop, this is revisited.

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): `WatchdogNotifier` is a
  service in `app/server/monitor/services/` (server) and a peer module in
  `app/camera/camera_streamer/` (camera). It does not import Flask in its
  business logic; the Flask blueprint for `/healthz` is a thin adapter.
- Preserve modular monolith (ADR-0006): notifier runs as a daemon thread
  inside each existing service process. No new daemon, no new process, no
  new container.
- Preserve camera lifecycle state-machine (ADR-0004): the new
  `notifier.beat()` call inside `_do_running` is additive — no new state,
  no transition change. `notifier.mark_ready()` fires once per lifetime
  after `RUNNING + streaming` and never again.
- Do not couple watchdog correctness to the network heartbeat. The two
  systems answer different questions; if the network heartbeat fails (e.g.,
  server unreachable), the camera process is still alive and must keep
  `WATCHDOG=1` flowing.
- Do not couple watchdog correctness to the notifier's own thread liveness.
  Forward-progress is determined by the **lifecycle main-loop** beat (camera)
  or the **WSGI listener's response to a real HTTP probe** (server). A
  notifier whose own thread is alive but whose freshness gate is stale must
  withhold the ping.
- `/healthz` must stay cheap. No DB, no template, no auth, no settings
  load, no audit. The contract test pins the exact body and headers.
  Future PRs adding to `/healthz` will fail the contract test and require
  explicit re-design.
- Notifier must no-op cleanly outside systemd (`NOTIFY_SOCKET` unset). All
  unit tests run in this mode by default.
- Notifier must never raise into the lifecycle or request path. Every
  socket call is wrapped; every probe call is wrapped; every thread loop
  iteration is in a try/except that logs and continues.
- Tests + docs + smoke-row updates ship in the same PR as code, per
  `docs/ai/engineering-standards.md`.
- Traceability annotations on the unit files use the existing
  `# REQ:` comment header — extend, do not replace, the existing
  `# REQ: SWR-006, SWR-012, SWR-050; ...` line.
- Yocto integration: do not add `python3-systemd` to RDEPENDS in this PR
  (see OQ-2). If the Implementer concludes the in-tree helper is too
  fragile, raise OQ-2 to a blocking decision before merging.
