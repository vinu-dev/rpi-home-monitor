# Feature Spec: Split Camera Human-Admin Surface From Machine Control Surface

Tracking issue: #113. Branch: `feature/113-camera-admin-control-split`.

## Title

Split the camera's HTTPS surface so the human-admin status page and the
server-only `/api/v1/control/*` API run on **separate listeners** with
independent TLS and auth policies.

## Goal

Restate of issue #113. Today the camera's status server (port 443,
`app/camera/camera_streamer/status_server.py`) hosts two trust models on
one listener:

- browser-driven admin pages (`/`, `/login`, `/api/wifi`,
  `/api/password`, `/api/factory-reset`, `/api/unpair`,
  `/api/ota/upload`, `/api/ota/reboot`, `/api/stream-config`, `/pair`)
  authenticated by a session cookie or, for `/pair`, a 6-digit PIN
- server-driven control endpoints (`/api/v1/control/config`,
  `/.../capabilities`, `/.../status`, `/.../restart-stream`,
  `/.../stream/{state,start,stop}`) authenticated by mTLS

The unified listener is the reason the TLS context runs in
`ssl.CERT_OPTIONAL` (browsers can't be required to present a client
cert), and the reason `_require_mtls` and `_require_auth` have to coexist
inside the same handler. Both are working today, but they share a
listener for compatibility, not by design — every future control-plane
or auth-hardening change has to re-prove that browser ergonomics
haven't loosened the machine path, and vice versa.

This spec gives each surface its own listener, its own TLS context, and
its own URL space, so:

- the human path can never accidentally accept a control call (and the
  control path can never accidentally accept a session cookie)
- the control listener can run `ssl.CERT_REQUIRED` — TLS itself fails
  for any peer that doesn't present a valid CA-signed client cert, with
  no in-handler fallback to second-guess
- future control features (PTZ, image controls, fleet ops) and future
  human features (richer admin UI, OTA UX) evolve independently
- "no cross-access" is enforced by listener boundaries instead of by
  per-route convention

This issue is architecture-maintainability work. It does not add a
user-facing feature, does not change the control parameter set
(ADR-0015 §4 is preserved as-is), does not weaken pairing
(ADR-0009/PIN), and does not relitigate ADR-0022 (no new pre-auth
surface; the existing `/pair` page stays exactly where it is).

## Context

Existing code this change builds on, not replaces:

- `app/camera/camera_streamer/status_server.py:33` — `LISTEN_PORT = 443`
  and the unified `StatusHandler`. The handler currently routes both
  human paths (via `_require_auth`) and `/api/v1/control/*` paths (via
  `_require_mtls`) on the same listener.
- `app/camera/camera_streamer/status_server.py:179` — `_wrap_https_server`
  loads `ca.crt` and sets `ctx.verify_mode = ssl.CERT_OPTIONAL` so that
  a browser without a client cert can still load `/login`. That mode is
  the literal source of the cross-surface coupling described in #113.
- `app/camera/camera_streamer/status_server.py:574` — `do_GET` /
  `do_PUT` / `do_POST` route every path through one
  `BaseHTTPRequestHandler` subclass. The control branches (`/api/v1/
  control/*`) and the human branches share method dispatchers.
- `app/camera/camera_streamer/control.py:111` — `ControlHandler` owns
  parameter validation, request-id replay protection, the rate limit
  (5 s, `RATE_LIMIT_SECONDS`), and the persisted desired stream state.
  It is **stateful** and there must be exactly one instance per camera
  process (see `_last_request_id`, `_last_change_time`,
  `stream_state_path`).
- `app/server/monitor/services/camera_control_client.py:38` — the
  server-side client that calls the camera's control API. It builds
  `https://{camera_ip}{path}` against port 443 today
  (`urllib.request.urlopen` defaults). It carries server.crt /
  server.key but pins `ctx.verify_mode = ssl.CERT_NONE` because the
  camera's status cert is self-signed; that is **out of scope here** and
  belongs to issue #119, which lands separately.
- `app/camera/camera_streamer/lifecycle.py:514` — start/stop wiring for
  the status server inside the camera lifecycle state machine. Any new
  listener must plug in here so the lifecycle owns it (and tears it
  down on shutdown / factory reset).
- `meta-home-monitor/recipes-camera/camera-streamer/camera-streamer_1.0.bb`
  references `config/nftables-camera.conf` (recipe path; the file lives
  in the recipe's WORKDIR overlay). The new control port must be added
  to that ruleset, allowed only from the paired server IP, with the
  same restriction shape as the existing 443 rule.
- ADR-0015 §1–§3 — the chosen pattern was "HTTP REST on the existing
  camera HTTPS server" with two auth methods on one listener. This
  spec **revises §3 of ADR-0015 only**: keep HTTP REST + mTLS, but
  split onto two listeners. Camera-side parameter set (§4),
  controllable parameters (§4), config sync model (§6), security
  hardening (§7), and bidirectional sync (§8) are unchanged.
- ADR-0022 — no new pre-auth surface. The split adds zero new
  pre-auth paths. The control listener has no pre-auth path at all
  (CERT_REQUIRED rejects the TLS handshake before any HTTP route).
- Issue #119 (open in `ready-for-design`) covers tightening the
  control-channel auth direction (server verifies camera cert,
  remove IP-fallback). #119 is intentionally complementary, not
  blocking. After #113 lands, #119's "remove IP fallback" task is
  effectively done on the camera side because there is no IP fallback
  in the control listener; #119 still needs to harden the server's
  outbound `CERT_NONE`.

## User-Facing Behavior

### Primary path — admin (browser)

1. Operator points a browser at `https://camera.local/` (or its IP).
2. Self-signed cert warning appears once (unchanged from today).
3. After the cert acceptance, the browser receives the existing login
   page from port 443. **No client-cert prompt.** The TLS context on
   port 443 runs `verify_mode = ssl.CERT_NONE` — the human listener
   does not request, validate, or even peek at a client certificate.
4. Login → status page → WiFi change / password change / OTA upload /
   factory reset / pair flow — all behave exactly as today. Pairing
   (`/pair`, `/api/pair`) stays on this listener (PIN-authenticated as
   in ADR-0009; no behavior change).
5. Any request for `/api/v1/control/*` on port 443 returns **404
   (Not Found)** — that namespace is unrouted on the human listener.
   Tests assert this explicitly.

### Primary path — server (machine)

1. The server's `CameraControlClient` opens an HTTPS connection to
   `https://{camera_ip}:8443/api/v1/control/...`.
2. The TLS handshake runs in `verify_mode = ssl.CERT_REQUIRED` on the
   camera. The camera's TLS layer demands a client certificate signed
   by `/data/certs/ca.crt`. If absent or signed by another CA, the
   handshake fails with TLS alert `bad_certificate` (or
   `certificate_required`). The HTTP handler is never reached.
3. With the cert valid, the request enters `ControlHandler` exactly as
   today: parameter validation, replay protection, rate limit,
   audit log. No change to `ControlHandler` semantics.
4. Any request for human-admin paths (`/`, `/login`, `/api/wifi`,
   `/api/password`, `/api/factory-reset`, `/api/unpair`,
   `/api/ota/*`, `/pair`, `/api/pair`, `/api/stream-config`) on port
   8443 returns **404** — that namespace is unrouted on the control
   listener.
5. The pre-pairing case (no `ca.crt` on disk yet) keeps the control
   listener **bound but un-startable** — the constructor logs
   "control listener disabled until pairing" and the listener thread
   does not start. Port 8443 is closed, `connect()` is refused at the
   TCP layer. After pairing writes `ca.crt`, the lifecycle starts the
   control listener (see Module / file impact list).

### Failure states (must be designed, not just unit-tested)

- **Browser hits the control port** (`https://camera.local:8443/`) →
  TLS handshake fails because the browser presents no client cert.
  The browser shows its generic SSL error. We do NOT add a
  human-readable HTML response — exposing a 4xx page on the control
  port would re-introduce a non-mTLS surface there.
- **Server hits the human port** with mTLS credentials → handshake
  succeeds (CERT_NONE is permissive), control paths return 404. The
  server's control client logs the 404 and the camera-side audit log
  records nothing (the human handler does not touch
  `ControlHandler`). Acceptance test covers it.
- **Server upgrades, camera does not** (mismatched OTA window) →
  control client tries `:8443`, gets `connection refused` (camera
  still listening only on `:443` for control). Server marks
  `config_sync = pending` and surfaces a "camera firmware older than
  server, please reboot for OTA" hint on the dashboard. The fallback
  is **deliberately one-shot and observable**: the client does not
  silently retry on `:443` for control, because retrying on the
  human port is exactly what this issue is asking us to stop doing.
- **Camera upgrades, server does not** → server still hits `:443`
  for control. The new camera firmware returns 404 on the human
  listener for `/api/v1/control/*`. Server marks `config_sync =
  pending`. Fixed by upgrading the server.
- **Operator's nftables not refreshed** (post-OTA, ruleset stale) →
  port 8443 inbound from server IP is dropped at the firewall;
  control client times out. Smoke-test row covers it; OTA recipe
  installs the updated `nftables-camera.conf` so this should not
  happen on the deployed image.
- **Two control requests in flight** crossing 5 s rate limit →
  unchanged from today. `ControlHandler` is the single instance
  (shared between listener threads if both pointed at it; the spec
  pins this to one listener so no shared-instance question arises).
- **Pre-pairing browser admin** (operator viewing status before
  pairing) → only `:443` runs; `:8443` is bound only after `ca.crt`
  exists. Operator sees the same status page they see today.
- **Factory reset deletes `ca.crt`** → control listener
  shutdown is part of the unpair / factory-reset flow; the lifecycle
  state machine tears down both servers, recreates `:443`, and leaves
  `:8443` un-started until the next pairing. AC-7 covers this.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: With the camera in the post-pairing RUNNING state, port 443 is
  bound and serves `/login` over HTTPS with no client-cert request
  (TLS context `CERT_NONE`).
  **[unit: `app/camera/tests/test_status_server.py` against the wrapped
  socket; `ctx.verify_mode == ssl.CERT_NONE`]**
- AC-2: With the camera in the post-pairing RUNNING state, port 8443
  is bound and the TLS context is `CERT_REQUIRED` and validates against
  `/data/certs/ca.crt`.
  **[unit: new `app/camera/tests/test_control_server.py`]**
- AC-3: A request to `https://camera/api/v1/control/config` on port
  443 returns 404 regardless of whether the caller presents a client
  cert. The same path on port 8443 with the paired server cert
  succeeds and returns the current config.
  **[contract: paired-cert harness from existing
  `test_status_server.py` mTLS suite]**
- AC-4: A request to `https://camera/login` on port 8443 fails the
  TLS handshake when the caller presents no client cert. With a valid
  client cert, the same path returns 404 (not the login page).
  **[integration: `test_listener_separation.py` — TLS error class
  asserted on no-cert path; 404 asserted on with-cert path]**
- AC-5: Pre-pairing (no `ca.crt`), only port 443 is open. Port 8443
  refuses TCP connections.
  **[unit: lifecycle test that asserts `_status_server.start()` is
  called but `_control_server.start()` is not, and a TCP connect to
  `:8443` fails with `ConnectionRefusedError`]**
- AC-6: After pairing, the lifecycle starts both listeners. After
  unpair / factory reset, both listeners stop and `:8443` becomes
  unbindable; `:443` rebinds (post-reset state is "human listener only,
  control disabled").
  **[lifecycle integration test reusing
  `test_lifecycle_state_machine.py` patterns]**
- AC-7: `ControlHandler` is instantiated exactly once per camera
  process and is wired into the control listener only; the human
  listener has no reference to it. (Static check: `grep -n
  "ControlHandler" status_server.py` returns no constructor call.)
  **[unit + lint test that fails on the constructor occurrence]**
- AC-8: Server's `CameraControlClient` builds URLs with explicit port
  8443 (default) and the constant is overridable via constructor
  argument (so tests can target an ephemeral port).
  **[unit: `app/server/tests/test_camera_control_client.py`]**
- AC-9: When the server hits `:8443` and gets `ConnectionRefusedError`
  (mismatched-firmware case), `CameraService.update()` records
  `config_sync = pending` and the dashboard surface (existing) shows
  the pending state. The client does **not** fall back to `:443`.
  **[integration: mocked socket layer that refuses 8443]**
- AC-10: nftables ruleset (`nftables-camera.conf`) allows tcp dport
  8443 only from the paired server IP; default-drop applies otherwise.
  Existing 443 rule is unchanged.
  **[unit: textual assertion of the ruleset; smoke-test row that
  scans 8443 from a non-server IP and confirms drop]**
- AC-11: All existing `test_status_server.py` mTLS tests are split
  into two files (admin tests still run against the human listener,
  control tests run against the new control listener). No test
  regresses; the contract test harness path-coverage matrix shows
  every existing endpoint mapped to exactly one listener.
  **[CI: `pytest app/camera/tests/ -v` green]**
- AC-12: A negative test asserts that NO `/api/v1/control/*` route is
  registered in the human handler, and NO human-admin route is
  registered in the control handler. (Walk both handlers' dispatch
  tables; assert disjoint sets.)
  **[contract test, runs in CI and prevents regressions on future
  PRs]**
- AC-13: Hardware verification: on a deployed image, an admin can
  log in over `:443` from a browser; the server can push a config
  change over `:8443`; an attempt to call `:443/api/v1/control/config`
  with the server's mTLS cert returns 404.
  **[hardware verification + new smoke-test row in
  `scripts/smoke-test.sh`]**
- AC-14: Audit log behavior is unchanged on the camera. Control
  operations still log to `/data/logs/control.log` with the existing
  fields (timestamp, parameter, old → new, requester cert CN). No new
  audit events are introduced.
  **[unit on `audit`/`control.py` log emit; contract assertion]**
- AC-15: Documentation update: ADR-0015 §3 (request routing diagram)
  is updated to reflect the split; ADR-0009 §nftables note gains a
  line about the additional rule. ADR-0022 is **not** modified
  (no new pre-auth surface introduced).
  **[doc-link checker passes; PR description cites ADR-0015 + ADR-0022
  with one-line summary of what changed and what didn't]**

## Non-Goals

- Renaming `status_server.py` → `admin_server.py` or splitting the
  module file. The two listener objects can live inside
  `status_server.py` (or with the control listener factored into a
  thin sibling, Implementer's call) without renaming. Renaming is
  churn out of proportion to the maintainability win.
- Tightening the server-side outbound `CERT_NONE` (camera_control_client
  uses `verify_mode = ssl.CERT_NONE` because the camera's status cert
  is self-signed). That belongs to issue #119 and lands separately.
  This spec does **not** make #119 worse — the new control listener
  presents the same self-signed status cert as today, so the server
  side's verification posture is unchanged.
- Adding new control endpoints. The set is exactly what
  `ControlHandler` exposes today (per ADR-0015 §4 and ADR-0017 stream
  state).
- Adding new human-admin endpoints.
- Switching from Python's `http.server` to a framework (Flask, etc.)
  on the camera. ADR-0006 (modular monolith) and the Zero 2W's RAM
  budget say no.
- Adding a reverse proxy (nginx, caddy) on the camera. Same reason.
- Removing the PIN-authenticated `/pair` page or moving it to a third
  port. It stays where it is — pre-auth-by-design, scoped to the
  pairing ceremony, ADR-0009.
- Changing the RTSPS streaming port (8554) or any other port not
  named here.
- Changing the camera's mDNS advertisement set. Only `:443` is
  advertised (so a browser-based `camera.local` discovery path keeps
  working). The server learns the control endpoint from pairing
  metadata, not mDNS.
- Persisting any new state on `/data`. The split is purely a runtime
  / process-shape change.

## Module / File Impact List

New code (camera-side):

- `app/camera/camera_streamer/control_server.py` — thin module owning
  the second `http.server.HTTPServer` on `:8443`, its TLS context
  (`ssl.CERT_REQUIRED`), and a `ControlHandler`-only request handler.
  No human-admin routes registered. Constructor takes the
  `ControlHandler` instance (never instantiates one). Implements
  `start()` / `stop()` mirroring `CameraStatusServer`.
- `app/camera/camera_streamer/control_handler_http.py` (or kept inline
  in `control_server.py`) — the `BaseHTTPRequestHandler` subclass that
  dispatches `/api/v1/control/*` GET/PUT/POST requests to the shared
  `ControlHandler` instance, with the existing
  `parse_control_request` + JSON response helpers reused.
- New constant `CONTROL_LISTEN_PORT = 8443` lives in `control_server.py`
  with a comment pointing back to this spec and ADR-0015 §3.

Modified code (camera-side):

- `app/camera/camera_streamer/status_server.py`:
  - Remove all `/api/v1/control/*` route branches from `do_GET`,
    `do_PUT`, `do_POST`.
  - Remove `_require_mtls` and `_has_mtls_client_cert` from the human
    handler.
  - In `_wrap_https_server`, change `ctx.verify_mode = ssl.CERT_OPTIONAL`
    to `ssl.CERT_NONE`. Drop the `load_verify_locations(ca_path)` call
    on the human listener (the human listener has no use for the CA).
  - `CameraStatusServer.__init__` no longer instantiates
    `ControlHandler`. Instead, the lifecycle injects the shared
    `ControlHandler` into both servers explicitly. (The
    `control_handler` property remains for backward compatibility but
    is now sourced from the lifecycle, not constructed inside.)
  - The `/api/stream-config` endpoint stays on `:443` (it's the human
    admin path that lets the operator change stream settings from the
    camera's own status page) and continues to delegate to the shared
    `ControlHandler` with `origin="local"` exactly as today.
- `app/camera/camera_streamer/lifecycle.py:514` — wire the
  `ControlServer` start/stop alongside the existing `CameraStatusServer`
  start/stop. Both are members of the lifecycle. Order:
  1. After pairing (`ca.crt` present) → start human + control listeners
  2. On unpair / factory reset → stop control listener first, then
     human, then drop `ca.crt`
  3. On shutdown → stop both
- `app/camera/camera_streamer/control.py` — no functional change.
  Add a comment at the top citing this spec for the listener-split
  rationale; the parameter table, validation, audit, rate limit, and
  stream state file remain authoritative.

Modified code (server-side):

- `app/server/monitor/services/camera_control_client.py`:
  - Add `CONTROL_PORT = 8443` constant at module top.
  - `_request()` builds `https://{camera_ip}:{self._control_port}{path}`.
  - Constructor takes optional `control_port=CONTROL_PORT` for tests.
  - On `ConnectionRefusedError` / `URLError(connection refused)`,
    return a distinct error string `"camera control port unreachable
    (firmware mismatch?)"` so `CameraService` can surface a useful
    operator hint. **No fallback to `:443`.**

Modified code (firmware build):

- `meta-home-monitor/recipes-camera/camera-streamer/files/config/nftables-camera.conf`
  (referenced from the recipe at
  `meta-home-monitor/recipes-camera/camera-streamer/camera-streamer_1.0.bb:25`)
  — add `tcp dport 8443 ip saddr $SERVER_IP accept` mirroring the
  existing 443 rule. Default-drop policy is unchanged.
- The `monitor-server` recipe does **not** need a firewall change
  (the server is the originator of the connection).

Tests (new):

- `app/camera/tests/test_control_server.py` — TLS context assertions,
  start/stop wiring, 404 on human paths, mTLS handshake required.
- `app/camera/tests/test_listener_separation.py` — cross-access
  matrix: human path on `:8443` → 404; control path on `:443` → 404.
  Same matrix re-run with mTLS cert presented to `:443` (still 404).
- `app/camera/tests/test_lifecycle_listeners.py` — pre-pairing /
  post-pairing / unpair listener state.
- `app/server/tests/test_camera_control_client.py` (extend) — port
  8443 default, custom port via constructor, no-fallback on
  connection refused, distinct error string on mismatch.

Tests (modified):

- `app/camera/tests/test_status_server.py` — keep all admin-side
  cases; remove or move all `/api/v1/control/*` cases to
  `test_control_server.py` (no test regresses; contract surface is
  the same).

Smoke-test additions (`scripts/smoke-test.sh`):

- "browser admin can log in over `:443` and change WiFi"
- "server pushes config over `:8443` and stream restarts"
- "calling `:443/api/v1/control/config` with a valid client cert
  returns 404"
- "scanning `:8443` from a non-server LAN IP times out / drops"

Dependencies:

- No new Python packages. No new system packages. No Yocto recipe
  additions beyond the firewall config edit.

Out-of-tree:

- No `app/server/` model change. No `Camera` field added.
- No mDNS service-name change. The camera continues to advertise
  `_https._tcp` on `:443`.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Camera Python | `pytest app/camera/tests/ -v`, `ruff check .`, `ruff format --check .` |
| Server Python (control client) | `pytest app/server/tests/test_camera_control_client.py -v` plus full `app/server/tests/` for safety |
| Auth or security path | full camera + server suite + smoke; change touches `**/auth/**`-adjacent code (mTLS context) |
| API contract | new contract test files for the cross-access matrix; existing control-API contract tests preserved |
| Yocto / firewall | rebuild camera image; `bitbake -c devshell camera-streamer` not required, but the recipe diff is reviewed; smoke-test row exercises the rule |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`; `python scripts/ai/check_doc_links.py` |
| Hardware behavior | deploy + `scripts/smoke-test.sh` rows above; manual browser test on `:443`; manual server-driven config push on `:8443` |

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-113-1 | Mismatched OTA window: server upgraded before camera (or vice versa) → control plane silently breaks because the port is wrong. | Moderate (operational) | Medium | RC-113-1: server's `CameraControlClient` returns a distinct, operator-visible error on `:8443` connection-refused; `CameraService` raises `config_sync = pending` and the dashboard already surfaces that state. No silent fallback to `:443`. Atomic OTA bundle (ADR-0014/0020) keeps server and camera firmware in lockstep on a normal upgrade. |
| HAZ-113-2 | Browser starts prompting users for a client cert on `:443`. | Minor (UX) | Low | RC-113-2: human listener's TLS context is now `CERT_NONE` (no client-cert request at all). Removing `load_verify_locations` on `:443` is what makes this guarantee structural rather than convention. AC-1 pins it. |
| HAZ-113-3 | Operator's `nftables` ruleset stale after OTA → control plane unreachable. | Moderate (operational) | Low | RC-113-3: OTA recipe installs the updated `nftables-camera.conf`; smoke-test row scans `:8443` reachability post-deploy; the camera's status page displays a "control plane unreachable from server" banner if the heartbeat layer has been silent for >2 health intervals. |
| HAZ-113-4 | Future PR adds a new control endpoint to the wrong listener (the human one), regressing the split. | Moderate (security) | Medium (humans drift over time) | RC-113-4: AC-12 contract test (path-coverage disjoint-set check) runs in CI; any new route registered on the wrong handler fails the test. PR template adds a one-line check "if you added a `/api/v1/control/*` route, did you add it to the control listener?" |
| HAZ-113-5 | The control listener starts before `ca.crt` is on disk (e.g., a race during pairing), TLS context construction fails noisily, lifecycle wedges. | Moderate (operational) | Low | RC-113-5: lifecycle gates control-listener start on `os.path.isfile(ca_path)` (mirrors the existing pre-pairing log line). Failure mode is "control listener disabled until pairing", logged once, retried by the lifecycle on the next state transition. |
| HAZ-113-6 | Adding a second listener uses more file descriptors / sockets / threads on the constrained Zero 2W. | Minor | Low | RC-113-6: each Python `HTTPServer` is one socket + one thread (the existing pattern). Memory footprint delta is well under 5 MB. Hardware verification row in AC-13 confirms post-deploy. |
| HAZ-113-7 | `/api/stream-config` (the human-admin form that lets the operator change stream settings from the camera's status page) bypasses the new mTLS-only control listener and writes the same camera.conf the control listener writes. | Minor (security) | Low | RC-113-7: this is intentional — `/api/stream-config` is session-cookie-gated (admin password) and shares the *same* `ControlHandler` instance, with `origin="local"`. The two paths converge on `ControlHandler` exactly as today; ADR-0015 §8 (bidirectional sync) covers the ping-pong prevention. Spec calls this out explicitly so a future reviewer doesn't try to "fix" it. |

Reference `docs/risk/` for the existing camera-domain risk register;
this spec adds rows; it does not redefine risk policy.

## Security

Threat-model deltas (Implementer fills concrete `THREAT-` / `SC-` IDs):

- **Strengthens** the machine-control trust boundary: `CERT_REQUIRED`
  on the control listener means a peer without a CA-signed client
  cert cannot complete the TLS handshake. Today the same defense lives
  one layer deeper (in `_require_mtls`'s `getpeercert()` check); after
  the split, the defense is at the TLS layer itself, which is the
  stricter and easier-to-audit position.
- **Removes** the `CERT_OPTIONAL` mode from the camera's HTTPS
  surface entirely. There is no listener that asks-for-but-doesn't-
  require a client cert. This eliminates the class of bug where a
  future code path forgets `_require_mtls()` and silently accepts a
  no-cert call.
- **Does not add a pre-auth surface.** The human listener already had
  `/login`, `/pair`, and the static error pages reachable pre-auth.
  After the split, that set is unchanged. The control listener has no
  pre-auth surface (TLS terminates the connection before any HTTP
  layer runs).
- **Sensitive paths touched:** `**/auth/**` (yes — `_require_mtls`
  removed from the admin handler; the same predicate moves into the
  control handler), `**/secrets/**` (no), `**/.github/workflows/**`
  (no), camera lifecycle (`lifecycle.py`, sensitive — yes), pairing
  flow (no — `/pair` stays on `:443`), OTA flow (no — `/api/ota/*`
  stays on `:443`). Per `docs/ai/roles/architect.md` these sensitive
  paths are flagged here for extra review.
- **Audit:** no new audit events. Existing `control.log` is unchanged.
  No human-admin event names change.
- **Rate limit / lockout:** `ControlHandler.RATE_LIMIT_SECONDS = 5`
  is preserved as the single source of truth for control-plane rate
  limiting. The human-admin rate limit (`auth.py` server-side
  patterns; on the camera, login rate limit lives in
  `status_server.py`) is unchanged.
- **Defense in depth:** nftables `tcp dport 8443 ip saddr $SERVER_IP
  accept` keeps the network-layer guard. mTLS keeps the TLS-layer
  guard. `ControlHandler`'s parameter validation keeps the
  application-layer guard. The split removes one *coupling* (CERT_OPTIONAL)
  and adds zero new authentication primitives.
- **No backdoor introduced.** ADR-0022 is not weakened. No new
  pre-auth path; no recovery / bypass surface; no in-handler "if
  source IP matches server" fallback (that was already removed in
  #112 / #119 and is **not** reintroduced anywhere in this spec).

## Traceability

Implementation annotations map issue #113 onto the existing controlled
traceability catalogue:

- Listener separation and TLS hardening: `SWR-013`, `SWR-039`,
  `RISK-002`, `RISK-007`, `SC-001`, `SC-002`, verified by `TC-004`,
  `TC-037`.
- Cross-access prevention (path-coverage disjoint set): `SWR-039`,
  `RISK-007`, `SC-002`, verified by new contract test wired into
  `TC-037`.
- Server outbound control-port migration: `SWR-039`, `RISK-007`,
  verified through extended `app/server/tests/test_camera_control_client.py`
  rolling under `TC-037`.
- Camera lifecycle wiring (start/stop ordering, pre-pairing gate):
  `SWR-013`, `RISK-002`, verified by lifecycle integration tests in
  `TC-004` family.
- Yocto firewall rule: `SWR-049` (production hardening), `RC-018`
  (risk-control-verification.md), `RISK-018`/`RISK-019`, verified
  by `TC-044` / `TC-047`.

The `HAZ-113-*` / `RC-113-*` rows in this spec remain issue-local
design records. They roll up to the controlled IDs above rather than
introducing new global traceability IDs in this implementation slice.

## Deployment Impact

- Yocto rebuild needed: **yes**, for the camera image — the firewall
  config file changes (one-line addition). Recipe (`camera-streamer_1.0.bb`)
  itself is unchanged. No new packagegroup. No layer-class change.
- Server image: no Yocto change.
- OTA path: standard combined OTA bundle (server + camera together
  per ADR-0014/0020). The split is wire-compatible only when both
  ends are upgraded; the operator-visible mismatched-window behavior
  is described in HAZ-113-1.
- Hardware verification: yes — required. Browser admin on `:443`,
  server-driven config push on `:8443`, and the negative-cross test
  (`:443` + mTLS cert returns 404).
- Default state on upgrade: control listener auto-starts on the
  upgraded camera *if* `ca.crt` exists (i.e., camera is paired).
  Pre-paired cameras are unchanged behaviorally; only `:443` runs
  until pairing.
- No data-migration step. No `users.json` / `cameras.json` schema
  change. No reboot-required state transition.

## Open Questions

(None of these are blocking; design proceeds. Implementer captures
answers in PR description.)

- OQ-1: Confirm port 8443 is not used by anything else on the camera
  image. Quick check: `ss -tlnp` on a running camera, plus a
  `bitbake-layers show-recipes` scan for `:8443` in installed
  recipes. If conflict, choose 8444 — port number is incidental,
  callout is "well-known alternate HTTPS, easy to remember."
- OQ-2: Whether to factor the new control listener into its own
  module (`control_server.py`) or keep both `HTTPServer` instances
  inside `status_server.py` with one new class. **Recommendation:
  separate module** — the names already say what they're for, and
  it makes AC-12's path-coverage disjoint-set check trivial to
  implement (two distinct dispatch tables in two files).
- OQ-3: Should `CameraControlClient`'s constructor take a
  `control_port` argument (per AC-8), or read it from a config /
  settings entry? **Recommendation: constructor argument with a
  module-level default.** Adding a `Settings` field for the port is
  speculative configuration we don't need yet; the constant is fine.
- OQ-4: Documentation update to ADR-0015 §3 — done in this PR or in
  a follow-up ADR-0015 supplement? **Recommendation: this PR**, as
  a minimal §3 patch + a "Revised by issue #113" note at the top.
  Larger architectural commentary belongs in a future ADR if the
  pattern recurs.
- OQ-5: Should the camera's mDNS advertisement gain a second SRV
  record for `:8443` (so the server can discover the control port)?
  **Recommendation: no.** The server learns the camera's IP during
  pairing and the control port is a constant. Adding mDNS for the
  control listener creates a discovery surface for an endpoint that
  shouldn't be discovered. Filed under "ideas we don't need" so a
  future reader can see we considered it.

(No question is blocking; if OQ-1 finds a port conflict, the
Implementer picks the next free port and updates the constants. No
spec edit required.)

## Implementation Guardrails

- Preserve modular monolith (ADR-0006): no new daemon, no new process.
  Two `http.server.HTTPServer` instances inside the same camera-streamer
  Python process.
- Preserve service-layer pattern (ADR-0003): `ControlHandler` stays
  the single business-logic owner for control operations; the new
  HTTP handler is a thin adapter exactly like the existing one in
  `status_server.py`.
- Preserve ADR-0009 / ADR-0015 trust model: same mTLS, same CA, same
  cert files, same `ControlHandler` parameter set. Listener split is
  a runtime-shape change, not a trust-model change.
- Preserve ADR-0022: no new pre-auth surface, no recovery / bypass
  primitive, no documented "alternate way in." This PR cites
  ADR-0022 in its description with the explicit one-liner "this PR
  adds zero new pre-auth surfaces."
- Do **not** remove `_require_mtls` from `control.py` or its test
  scaffolding — the predicate moves from `status_server.py`'s human
  handler to the new control handler so the application-layer
  defense (`getpeercert()` check + CN match if added later) stays
  available even if the TLS layer's `CERT_REQUIRED` is ever
  loosened.
- Do **not** add a fallback path in `CameraControlClient` that
  retries control calls on `:443` after `:8443` fails. Silent
  fallback is exactly the maintainability hazard #113 is
  closing.
- Tests + docs ship in the same PR as code, per
  `engineering-standards`. PR description must include:
  the ADR-0015 §3 diff, the ADR-0022 one-liner, the firewall rule
  change summary, and the cross-access matrix output.
- The PR description should propose updating the issue title to make
  clear that this is the **listener split**, not a broader auth
  rework — to avoid scope creep with #119.
