# Feature Spec: IP-Based Fallback For Unreliable `.local` Resolution

Tracking issue: #90. Branch: `feature/90-mdns-ip-fallback`.

## Title

Surface a stable IP-based fallback path so operators can reach the camera and
the server when mDNS / `.local` resolution fails on their network — without
needing to know an IP address up front, run `nmap`, or SSH into the device.

## Goal

When an operator types `https://rpi-divinu-cam.local` into their phone or
laptop and nothing happens, today there is **no in-product way to recover**:
the camera is reachable on its IP, the server stores that IP, but neither side
hands the operator an address they can paste into the URL bar.

This spec closes the remaining UX gap on issue #90 after the structural mDNS
fixes shipped in PRs #198 (Avahi readiness verification), #199 (boot-time
hostname-resolution retry with `_ServerResolver`), #200 / #233 (mDNS goodbye
on hostname change with raw-UDP fallback), and the partial UX work in
commit `0b632a3` (camera IP shown on the dashboard card for admins).

What remains for this issue is the **operator-facing fallback path**:

1. A **QR-code + scannable URL block** on the camera setup-completion view and
   on the camera status page, encoding `https://<camera_ip>:443/`. After
   pairing, the operator scans with their phone camera, lands on the camera
   status page on the home network, and the URL bar now carries the IP — they
   can bookmark it and never depend on `.local` again.
2. A **server self-IP block** on the server dashboard and on the login page
   footer (visible to anyone who can reach the dashboard at all), so an admin
   who reaches `https://homemonitor.local` *once* on a working network can
   record the server's IP for future fallback. Same QR-code treatment so the
   operator can hand the URL to a phone that hasn't been able to resolve
   `homemonitor.local` reliably.
3. A **camera-side cached server IP** at `/data/config/server_resolved_ip`,
   written by `_ServerResolver` after a successful resolution, read on next
   boot as a fallback when `socket.gethostbyname()` fails. Today the resolver
   surfaces a fault but does not preserve a working IP across reboots — a
   transient mDNS outage at boot turns into a heartbeat outage until the
   network recovers, which on flaky multicast networks may never happen.
4. **Operator help under `docs/guides/`** that documents the fallback path
   end-to-end ("What to do when `.local` doesn't work"), citing the QR codes
   on the camera and server pages so support questions converge on a single
   answer.

This spec is **scoped to UX + a small camera-side resilience improvement**.
It does not re-open any of the structural mDNS fixes already on `main`, and
it does not change auth, pairing, or the control channel.

## Context

This is the third pass on issue #90. The first two are already on `main`:

- **PR #198** (`fix(camera): verify avahi-publish stayed alive after launch`):
  `app/camera/camera_streamer/discovery.py:181` — `_verify_publish_alive()`
  polls `process.poll()` for 500 ms after spawning each `avahi-publish-*`
  helper and surfaces silent failure (Avahi not on the bus, duplicate name,
  bad TXT) with stderr in the log. Closes root cause #1 (silent half-published
  service) and #2 (boot race against Avahi readiness).
- **PR #199** (`fix(camera): boot-time hostname-resolution retry with
  backoff`): `app/camera/camera_streamer/lifecycle.py:73` — `_ServerResolver`
  retries `socket.gethostbyname()` on a daemon thread with exponential
  backoff up to 60 s and a 300 s deadline; raises a structured
  `mdns_resolution_failed` fault on the heartbeat if it never resolves.
  Closes root cause #3 (one-shot resolution at boot).
- **PR #200 / #233** (`broadcast mDNS goodbye on hostname change`):
  `app/camera/camera_streamer/wifi.py:301` — `set_hostname()` hands off to
  `avahi-set-host-name` over D-Bus, falls back to a raw-UDP multicast
  goodbye + daemon restart when D-Bus access is denied. Closes root cause #5
  (stale `<old>.local` entries in network caches).

Code surfaces this spec must build on:

- `app/camera/camera_streamer/templates/setup.html:266` — the
  setup-completion view (`view-result`). After successful pairing, fetches
  `/api/status` and renders `'Camera settings page:'` followed by an
  `<a href="https://<hostname>.local">` link. This is the **first place** an
  operator sees an address for the camera after switching off the hotspot —
  and it's a `.local` link, with no IP alternative.
- `app/camera/camera_streamer/templates/status.html:488` — the camera status
  page already shows the IP in `<dd>… · <span id="h-ip">`. The data is
  available; it is just not surfaced as a copy-and-paste-friendly URL or a
  scannable QR code. The page also lacks any "share this link" affordance.
- `app/camera/camera_streamer/status_server.py:843` — `_get_status()` returns
  `ip_address` and `hostname` to the camera status page; the server-side
  payload is sufficient to derive `https://<ip>:443/`. No new fields needed.
- `app/camera/camera_streamer/lifecycle.py:124` — `_ServerResolver.start()`
  and `_run()` (`lifecycle.py:149`). Successful resolution is captured in
  `self._resolved_ip` (`lifecycle.py:117`) but is not persisted across
  process restarts. Persisting it to `/data/config/server_resolved_ip`
  buys boot-time resilience when mDNS is slow or unavailable.
- `app/server/monitor/templates/dashboard.html:266` — the existing
  `cam.ip` admin-only block (added in `0b632a3`). Pattern to follow for the
  server-self-IP block elsewhere on the dashboard.
- `app/server/monitor/api/cameras.py:139` — `list_cameras()` already exposes
  `cam.ip` for admins via `CameraService.list_cameras(admin_view=True)`
  (`camera_service.py:446`). No backend change for camera-IP visibility.
- `app/server/monitor/__init__.py` — Flask app factory; small additions for
  the new `/api/v1/system/network` endpoint (server self-IP) and to register
  the operator-help doc as a static asset. No lifecycle hook changes.

Cross-references:

- `docs/history/releases/release-02.md` — release-02 covers operator
  reliability fixes; this spec fits naturally as a usability hardening item
  rather than a new feature line.
- ADR-0015 (server↔camera control channel) — unaffected. Camera-side IP
  cache reads are local file I/O, not a wire protocol change.
- ADR-0017 (recorder ownership) — unaffected.
- Spec #238 (TOTP 2FA) introduced the `qrcode[pil]` discussion; that spec
  recommends server-side rendering for two-factor URIs. This spec adopts
  the **client-side** QR rendering library for the camera setup-completion
  view because (a) the camera image cannot pull in Pillow without a Yocto
  recipe addition; (b) the QR payload is a non-secret URL that can be
  rendered safely in JS; (c) keeping it client-side means the camera-side
  page does not require Python QR generation. The server side, where
  `qrcode[pil]` is plausibly already on the runtime image after #238, may
  use server rendering OR the same vendored client lib — implementer's
  call (see OQ-3).

## User-Facing Behavior

### Primary path — operator finishes pairing, sees a scannable URL

1. Operator opens the camera-hotspot setup page, picks their home WiFi,
   enters credentials and the server's address, taps Save.
2. Hotspot drops; phone reconnects to home WiFi. Operator reopens the
   setup URL (or scans the QR code on the printed setup card). The page
   refetches `/api/status`, sees `setup_complete=true`, and renders
   `view-result`.
3. The result panel now shows TWO addresses, both clearly labelled:
   - **By hostname**: `https://<hostname>.local` — link AND a brief tag
     "Works on most home networks; may not work on all WiFi routers."
   - **By IP**: `https://<ip>:443/` — link, plus a QR code (≤ 200 × 200 px)
     directly below it that encodes the same URL, plus a "Copy" button
     that puts the URL on the clipboard. Tag: "Always works on this
     network. Bookmark this link for future visits."
4. The operator scans the QR with their phone camera; the phone's browser
   opens the camera status page on the IP URL. The page shows the same IP
   in its hero block (existing `h-ip` element), so the operator can verify.
5. Operator bookmarks the IP URL; future visits don't depend on `.local`.

### Primary path — operator wants the IP later

1. Operator visits the camera status page (by `.local`, by IP, or by
   tapping the camera card on the server dashboard, which now exposes a
   click-through that opens the camera's IP URL for admins).
2. The hero block displays hostname and IP exactly as today.
3. Below the hero block (or in a new card titled "Reach this camera"),
   the page shows the same QR + copy-button pair as the setup-completion
   view. Visible to **any logged-in viewer or admin** of the camera page —
   if you can already see the camera, you can already see its IP, so this
   is not a new disclosure.

### Primary path — admin needs the server's IP for a setup form

1. Admin reaches the server dashboard (typically `https://homemonitor.local`).
2. Login page footer AND the dashboard "Network" tile (or footer band, see
   OQ-1) show: `Server reachable at https://<server_ip>:443/` plus the QR
   code and a Copy button. Visible **before** login on the login page so an
   admin standing at a freshly-imaged camera can grab the URL even if they
   haven't authenticated yet on the device they're holding.
3. Admin types or pastes the IP URL into the camera's hotspot setup form
   ("Server address" field) instead of `homemonitor.local`. Pairing
   proceeds against the IP. (The IP is persisted in `/data/config/`
   alongside the existing `server_ip` field; a future `.local` recovery
   does not require re-pairing.)

### Primary path — camera reboots after a transient mDNS outage

1. Camera came online yesterday, `_ServerResolver` resolved
   `homemonitor.local` to `192.168.1.42`, persisted that IP to
   `/data/config/server_resolved_ip` (timestamp + hostname recorded
   alongside).
2. Today the camera reboots while the home router is still booting / Avahi
   is rate-limiting / multicast is blocked.
3. `_ServerResolver` reads the cache file. If the cached `hostname` matches
   the configured `server_ip` AND the cache is younger than 7 days, it
   primes its `_resolved_ip` with the cached IP **and** still launches its
   normal background retry against the configured hostname.
4. Heartbeat / control-channel callers query `_ServerResolver.resolved_ip`
   (new public path, see Module Impact) — they get the cached IP
   immediately and the camera stays in heartbeat coverage during the boot
   blackout.
5. When live resolution succeeds, the cache is rewritten with the fresh
   timestamp; if it returns a different IP, the cache is updated and the
   stale fault (if any) is cleared as it is today (`lifecycle.py:184`).

### Failure states (designed, not just unit-tested)

- **No IP available at QR-render time**: `wifi.get_ip_address()` returned
  empty (camera on hotspot mode, no IP yet). The setup-completion view
  shows the `.local` link only and a one-line note "IP fallback not
  available yet — refresh this page in a few seconds." No QR is rendered;
  no error toast. The status page, which only renders post-pairing on a
  WiFi-connected camera, always has an IP — the same fallback message is
  shown for completeness if `ip_address` is empty (test environments).
- **QR code library fails to load** (offline browser, blocked CDN if any
  CDN-loaded): the URL link AND copy button still render. The QR image
  is omitted with a `<noscript>`-style fallback note "QR not rendered;
  copy the URL manually." No JS exception leaks to the user.
- **Cached server IP file corrupted or missing fields**: read fails with
  a one-line warning; resolver behaves as today (live retry only). On
  next successful resolution the cache is overwritten cleanly.
- **Cached IP is from a different hostname** (operator changed
  `server_ip` from `homemonitor.local` to a different name): cache is
  treated as stale and ignored; resolver behaves as today. The
  hostname-mismatch event is logged once per process so an operator
  can see why the cache wasn't used.
- **Cache older than 7 days**: ignored. Expiry chosen so a camera coming
  out of long-term storage doesn't try to reach a server on an IP that
  may have been DHCP-rotated; 7 days is long enough for normal home
  router lease cycles.
- **Cached IP no longer reachable** (router rotated DHCP, server moved):
  heartbeat fails to the cached IP; the existing `consecutive_failures`
  counter on the camera (`stream.py:71`) and the heartbeat error path
  emit the same `mdns_resolution_failed` fault as today. The cache is
  not invalidated on a single failure (defensive against a transient
  blip); it is invalidated on the next successful live resolution that
  returns a different IP, OR after `MAX_CACHE_AGE_S` (7 days), whichever
  comes first.
- **Server IP changes mid-session**: not addressed by this spec. The
  current heartbeat reconnection logic handles this on a best-effort
  basis; the cache is overwritten on the next successful live
  resolution.
- **Server self-IP block fails to render** (admin behind a reverse proxy
  that hides the server's LAN IP): the block shows "Network address
  hidden by reverse proxy" and falls back to the request host. No QR.
  Operators on a reverse-proxy setup are explicitly out of the
  in-product UX path — they own DNS and don't need an IP fallback.
- **Operator on a phone whose camera does not auto-launch URLs from QR
  codes**: the QR is captioned with the URL underneath in plain text;
  the operator can read it and type it. The Copy button covers the
  case where the user is on the same device as the dashboard.
- **Multiple network interfaces on the server** (e.g. both wired and
  WiFi): `/api/v1/system/network` returns the IP of the interface the
  request came in on (via `request.host_url`), not all interfaces. This
  is exactly the IP the operator just used to reach the dashboard, so
  it is the IP guaranteed to work.

## Acceptance Criteria

Each bullet is testable; verification mechanism in brackets.

- AC-1: The camera setup-completion view (`view-result` in
  `setup.html`) renders TWO labelled addresses when `setup_complete=true`
  AND `ip_address` is non-empty: the existing `https://<hostname>.local`
  link, AND a new `https://<ip>:443/` link with a QR code and Copy button.
  **[browser-level smoke + JSDOM unit on the inline script]**
- AC-2: When `ip_address` is empty in the `/api/status` payload, the
  setup-completion view shows only the `.local` link and a one-line note;
  no QR is attempted.
  **[unit on the inline script]**
- AC-3: The camera status page renders a "Reach this camera" block
  (URL + QR + Copy) for any logged-in viewer when `ip_address` is
  non-empty. Visibility matches the existing `h-ip` block — no new
  authorization gate is added because the data is already exposed.
  **[browser-level smoke]**
- AC-4: The QR code in both the setup-completion view and the camera
  status page encodes exactly `https://<ip>:443/` (no path, no query
  string), and the encoded string is asserted to match the visible link
  text.
  **[unit: parse the rendered QR or assert via the QR-library API]**
- AC-5: A new `GET /api/v1/system/network` endpoint on the server
  returns `{server_url: "https://<ip>:<port>/", ip: "<ip>", port: <int>,
  source: "request_host" | "wifi_iface"}`. Auth: NONE (visible on the
  login page footer). The endpoint never returns a routable public IP;
  if the request comes from a non-RFC1918 source it falls back to a
  `wifi_iface` lookup or an empty payload.
  **[contract test]**
- AC-6: The server login page renders a "Server address" footer block
  with the URL + QR + Copy treatment when `/api/v1/system/network`
  returns a non-empty `server_url`. Hidden when the payload is empty.
  **[browser-level smoke against a logged-out session]**
- AC-7: The server dashboard renders the same "Server address" block in
  a footer band (or settings tile, see OQ-1) so an authenticated admin
  can also grab the URL without logging out.
  **[browser-level smoke]**
- AC-8: `_ServerResolver` writes `/data/config/server_resolved_ip` after
  every successful `socket.gethostbyname()`, with payload
  `{hostname: <configured_hostname>, ip: <resolved_ip>, ts:
  <ISO-8601 UTC>}`.
  **[unit: monkeypatch `socket.gethostbyname`, assert file contents]**
- AC-9: On `_ServerResolver.start()`, if the cache file exists, has a
  matching `hostname`, and `ts` is younger than 7 days, the resolver
  primes `self._resolved_ip` with the cached value before kicking off
  its retry thread.
  **[unit: write fixture cache files and assert priming behaviour]**
- AC-10: A cache file with a mismatched `hostname` (operator changed
  `server_ip`) is ignored; resolver behaves as today.
  **[unit]**
- AC-11: A cache file with `ts` older than 7 days is ignored.
  **[unit]**
- AC-12: A corrupted cache file (invalid JSON, missing keys, unreadable)
  is ignored; the resolver logs a single warning and proceeds without
  priming.
  **[unit]**
- AC-13: When the resolver later resolves the hostname to a different
  IP than the cached value, the cache is rewritten atomically (write to
  a temp file in the same directory, then rename).
  **[unit]**
- AC-14: The camera heartbeat path can read the primed `_resolved_ip`
  during the boot retry window; an integration test simulates Avahi
  unavailable for 30 s and asserts heartbeat succeeded against the
  cached IP within that window.
  **[integration with a fake DNS resolver]**
- AC-15: The camera operator-help page under `docs/guides/` documents
  the fallback path: where to find the IP on the camera status page,
  where to find the IP on the server dashboard, what to do if neither
  is reachable. The doc is linked from both new "Reach this camera /
  server" UI blocks via a "Help" link.
  **[doc-link checker passes; visual check on a rendered preview]**
- AC-16: No regression in the existing `/api/status` (camera) and
  `/api/v1/auth/me` (server) responses — payload shapes are unchanged.
  **[contract suite passes unchanged]**
- AC-17: The setup-completion QR code, when rendered with no network
  available (`ip_address=""`), does NOT crash the page or leave a half-
  rendered QR — both the QR DOM container and the IP-link block are
  hidden together.
  **[browser-level smoke]**
- AC-18: The QR rendering library is vendored under
  `app/camera/camera_streamer/static/qrcode.min.js` (MIT-licensed,
  hash-pinned in package metadata). No CDN load. License compliance
  recorded in `docs/cybersecurity/third-party-libraries.md` (or
  equivalent register).
  **[license-and-supply-chain review checklist]**
- AC-19: The Copy button copies the URL to the clipboard via
  `navigator.clipboard.writeText` with a fallback to `document.execCommand`
  for older browsers. A 1.5 s "Copied!" toast confirms.
  **[browser-level smoke]**
- AC-20: On hardware: pair a fresh camera, complete setup on the
  hotspot, confirm the result page renders the QR; scan the QR with a
  phone camera; phone browser opens `https://<ip>` and the camera
  status page loads. Smoke entry added to `scripts/smoke-test.sh`.
  **[hardware smoke]**
- AC-21: Audit event `SYSTEM_NETWORK_FALLBACK_VIEWED` is emitted on
  every fetch of `/api/v1/system/network` from a logged-in session
  (admin or viewer). Anonymous fetches from the login page are NOT
  audited (the endpoint is intentionally unauthenticated; auditing
  pre-login traffic would create a noisy floor of pre-auth scrapes).
  Audit detail: `client_ip`, `user_id` if any, `source`. No PII; no IP
  is logged for unauthenticated calls.
  **[unit + audit-log assertion]**

## Non-Goals

- **Re-implementing the structural mDNS fixes**: PRs #198, #199, #200,
  #233 are on `main` and remain authoritative. This spec does not edit
  `discovery.py`, the `_verify_publish_alive` contract, or the
  `set_hostname()` goodbye broadcast.
- **mDNS over a different transport** (e.g. Apple Bonjour + Wide-Area
  DNS-SD): structural rework, out of scope per the issue's scope of
  "minimum viable fix."
- **A separate setup-discovery app for phones**: the issue calls this
  out as "long term" and not the v1 fix. The QR code on the existing
  setup-completion page is the ergonomic equivalent.
- **Dynamic DNS** or hosting an external rendezvous service: would
  break the local-first product principle (`docs/ai/mission-and-goals.md`).
- **Auto-detecting the operator's phone IP and pushing the camera URL
  via a local mesh / WebRTC datachannel**: scope explosion; standard
  QR is sufficient and a known UX pattern.
- **A persistent IP allowlist** for the camera, server, or pairing —
  the cache is purely for `_ServerResolver`'s boot-time fallback. It
  is not consulted by the control channel, mTLS validation, or
  pairing.
- **Surfacing the camera IP to viewers on the server dashboard**: the
  existing admin-only gate at `dashboard.html:271` is preserved. The
  IP is still surfaced to viewers on the **camera's own** status page
  (where the data is already authenticated to the same scope), but
  not on the server dashboard. The asymmetry is intentional: the
  dashboard exposes ALL cameras' IPs, which is a network-topology
  disclosure; the camera status page exposes only its own IP.
- **A QR code embedded in printed-card / sticker artwork**: a future
  packaging-level improvement, not in scope for the dashboard.
- **Renaming the camera hostname through this UI**: separate flow
  (`wifi.set_hostname()`); QR on the status page reflects the
  current hostname only.
- **Surfacing the server's outward-facing IP** (e.g. for remote-review
  via Tailscale / port-forward): this spec covers the LAN IP only.
  Remote review is `r1-local-alert-center-and-tailscale-remote-review.md`.
- **Localisation of the new UI strings** ("Reach this camera",
  "Server address", "Always works on this network"): English only,
  same as the rest of the dashboard.
- **A separate dashboard "Network" tab** to host the server self-IP
  block: footer / corner placement is sufficient; a tab implies more
  features incoming. v2 may revisit if more network status is added.

## Module / File Impact List

**New code:**

- `app/camera/camera_streamer/static/qrcode.min.js` (new, vendored) —
  MIT-licensed QR-code rendering library (e.g. `qrcode-generator` or
  `kjua` pinned by SRI hash). Loaded by `setup.html` and `status.html`
  via a relative `<script src="/static/qrcode.min.js">`. License
  metadata recorded in repo (see Implementer Guardrails).
- `app/camera/camera_streamer/templates/_partial_reach_block.html`
  (new, optional partial) — the URL + QR + Copy block, included by both
  `status.html` (in a new "Reach this camera" card) and `setup.html`
  (in `view-result`). Pure HTML + a small JS init. The partial does
  NOT take props — it reads `data-url` from a wrapping element.
- `app/server/monitor/api/system.py` (new) — Flask blueprint with one
  route `GET /api/v1/system/network` returning the server's reachable
  URL. The handler:
  - Parses `request.host` to extract the IP/port the client used to
    reach the server.
  - If the host is a hostname (not a literal IP), resolves it via
    `socket.gethostbyname()` to get an IP. If that fails, falls back
    to inspecting the WiFi interface (`netifaces` or
    `subprocess.run(['nmcli', ...])` — implementer's call; netifaces
    is already a runtime dep).
  - Returns `{server_url, ip, port, source}` or empty payload on error.
  - Audits `SYSTEM_NETWORK_FALLBACK_VIEWED` only when the request is
    authenticated.
- `app/server/monitor/static/qrcode.min.js` (new, vendored — same library
  as the camera side). Symlink or copy is acceptable; a single
  vendored file under `app/server/monitor/static/` keeps the server
  side self-contained.
- `app/server/tests/unit/test_api_system_network.py` (new) — contract
  tests for the new endpoint: anonymous fetch returns the payload,
  authenticated fetch emits the audit event, reverse-proxy / non-RFC1918
  request host returns empty.
- `app/camera/tests/test_server_resolver_cache.py` (new) — unit tests
  for the `/data/config/server_resolved_ip` cache: write-after-success,
  read-and-prime on start, hostname-mismatch ignore, age-mismatch
  ignore, corrupted-file ignore, atomic-rewrite on IP change.
- `docs/guides/network-fallback.md` (new) — operator help: "What to do
  when `.local` doesn't work." Documents the QR scan flow, the IP
  block on the camera status page, the IP block on the server
  dashboard, and the router-admin-page fallback for finding either IP
  manually. Linked from both UI blocks via a `Help` text link.

**Modified code:**

- `app/camera/camera_streamer/templates/setup.html:266` — `view-result`
  rendering: extend the inline JS to also render the IP-link block
  with QR + Copy when `d.ip_address` is non-empty. Hide the IP block
  cleanly when empty.
- `app/camera/camera_streamer/templates/status.html` — add a new
  "Reach this camera" card after the hero block, populated from the
  same `/api/status` payload's `ip_address`. Reuse the partial.
- `app/camera/camera_streamer/lifecycle.py:73` — `_ServerResolver`:
  - Add class constants `CACHE_FILE_PATH = "/data/config/server_resolved_ip"`,
    `MAX_CACHE_AGE_S = 7 * 24 * 3600`.
  - Add `_load_cache()` and `_persist_cache(ip: str)` methods (atomic
    write via `tempfile.NamedTemporaryFile(dir=...)` + `os.replace`).
  - In `start()`, before launching the thread, call `_load_cache()` and
    if it returns a valid (hostname-matching, age-fresh) IP, set
    `self._resolved_ip` to that value (cache priming).
  - In `_run()`, on every successful resolution, call
    `_persist_cache(ip)`.
  - Tests in `app/camera/tests/test_lifecycle.py` extended for the
    cache flows; existing `_ServerResolver` tests unchanged.
- `app/server/monitor/__init__.py` — register the new
  `/api/v1/system/network` blueprint. Import location consistent with
  existing API blueprints; no lifecycle hook changes.
- `app/server/monitor/templates/login.html` — add a "Server address"
  footer block (URL + QR + Copy + Help-link) that fetches
  `/api/v1/system/network` on page load and hides itself if the
  payload is empty. The block is below the login form, above the
  static footer, with a divider. No layout shift if the fetch fails.
- `app/server/monitor/templates/dashboard.html` — add the same block in
  the dashboard footer (after the existing footer divider, see
  OQ-1). Visible to all logged-in users.
- `app/server/monitor/services/audit.py` — new constant
  `SYSTEM_NETWORK_FALLBACK_VIEWED`. Audit detail surface unchanged.
- `app/server/monitor/templates/dashboard.html:266` — the existing
  admin-only `cam.ip` block: extend with a small "Open camera" link
  that opens `https://<cam.ip>:443/` in a new tab (admins only). This
  is a one-line change to wrap the IP text in an `<a>` tag with
  `target="_blank" rel="noopener"`. Improves the existing UX without
  expanding the disclosure surface (the IP was already visible).
- `docs/guides/index.md` (or whichever guides index exists) — link the
  new `network-fallback.md` doc.
- `docs/cybersecurity/third-party-libraries.md` (or the existing
  third-party register) — add the QR-code library entry with version,
  license, and SRI hash.

**Out-of-tree:**

- **No camera firmware change beyond Python**: the QR rendering is in
  the browser, not on the camera. The cache file is plain JSON in
  `/data/config/`, written by an existing daemon thread.
- **No Yocto recipe change** for the camera: the vendored QR library
  is under `app/camera/camera_streamer/static/`, which is already on
  the camera's `/opt/camera` install path through the existing
  packagegroup that ships `camera_streamer`'s static assets. Verify
  with the Yocto-touch validation that no `.bbappend` is needed
  (it shouldn't be — the static dir is bulk-copied by the recipe).
  If verification shows a recipe change IS needed, the spec
  explicitly calls it out as a Yocto-touch and the validation matrix
  row "Yocto config or recipe" applies.
- **No new external Python dependency** on the server side. The
  `/api/v1/system/network` endpoint uses only stdlib (`socket`,
  `subprocess` — already used elsewhere) plus the existing `netifaces`
  if available, with subprocess fallback.
- **No data migration**: the cache file is created on the first
  successful resolution after upgrade; absence is the legacy state.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| Camera Python | `pytest app/camera/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | new `test_api_system_network.py`; existing `/api/status` and `/api/v1/auth/me` tests must remain green |
| Frontend / templates | browser-level smoke on `setup.html`, `status.html`, `login.html`, `dashboard.html` covering the four AC scenarios (with-IP, without-IP, QR-lib-blocked, copy-button) |
| Security-sensitive path | none touched — the new endpoint is read-only and exposes only the IP the client already used to reach the server. No `**/auth/**`, `**/secrets/**`, `**/.github/workflows/**`, certificate, pairing, or OTA code is modified. The vendored QR library lands in static assets, not in any auth-relevant code path. |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Coverage | server `--cov-fail-under=85`, camera `--cov-fail-under=80` (existing). New code is small (cache I/O, one endpoint, two template partials); high coverage expected. |
| Hardware behavior | deploy + `scripts/smoke-test.sh` rows: "after pairing, scan the QR on view-result with a phone — phone opens camera status on IP", "force the camera to reboot with `homemonitor.local` blackhole'd in `/etc/hosts` — heartbeat resumes within 30 s using the cached server IP" |
| Repository governance | `python tools/docs/check_doc_map.py`, `python scripts/ai/validate_repo_ai_setup.py`, `python scripts/ai/check_doc_links.py`, `python scripts/check_version_consistency.py`, `python -m pre_commit run --all-files` |

Smoke-test additions (Implementer to wire concretely in
`scripts/smoke-test.sh`):

- "Pair a fresh camera through the hotspot. Confirm the
  setup-completion view shows BOTH a `.local` link and an IP link
  with QR; scan the QR with a phone camera. Phone browser opens the
  camera status page on the IP. Confirm the page also renders the
  'Reach this camera' block with a matching QR."
- "Open the server dashboard at `https://homemonitor.local`. Log out.
  Confirm the login page footer renders the server's IP, QR, and
  Copy button. Click Copy; paste into a phone's URL bar; confirm the
  phone reaches the server."
- "Block multicast at the AP for 60 s. Reboot the camera within that
  window. Confirm `_ServerResolver` reads the cache and the heartbeat
  resumes within 30 s without waiting for the multicast block to
  lift."
- "Manually corrupt `/data/config/server_resolved_ip` (write empty
  file). Reboot. Confirm the camera logs a single warning, behaves as
  today (live retry only), and the cache is rewritten on next
  successful resolution."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-90-1 | Cached server IP becomes stale (DHCP rotation, server moved) and the camera's heartbeat hangs trying the cached IP instead of failing fast and trying the live hostname. | Major (operational — camera silently offline) | Medium (DHCP leases rotate) | RC-90-1: cache is consulted only as a *prime* for the resolver's `_resolved_ip` (AC-9). The retry thread always also runs against the live hostname. On any hostname-resolution success that returns a different IP, the cache is overwritten (AC-13). On a 7-day age (AC-11) the cache is ignored. The heartbeat code path is unchanged — it queries `_resolved_ip` exactly as today. So a stale cache produces *at most* one heartbeat-cycle delay; the live retry surface is unaffected. |
| HAZ-90-2 | The cache-priming logic accidentally suppresses the `mdns_resolution_failed` fault (operator never sees the underlying network problem). | Moderate (UX — operator can't diagnose a real WiFi issue) | Low | RC-90-2: cache-priming sets `_resolved_ip` but does NOT short-circuit the retry thread. The fault is emitted on retry-thread deadline as today; cache-priming has no effect on fault emission. Test asserts the deadline path still fires when live resolution stays broken even with a primed cache. |
| HAZ-90-3 | `/data/config/server_resolved_ip` write fails due to disk-full or read-only `/data` (e.g. SD card wear). The exception escapes and crashes the resolver thread. | Major (camera-side daemon thread death) | Low (Yocto image keeps `/data` rw + low free-space alarm) | RC-90-3: `_persist_cache` wraps write in try/except `OSError`, logs at WARNING, and continues. The atomic-rename pattern (`tempfile + os.replace`) guarantees no partial writes. Cache write is best-effort; failure does not propagate. |
| HAZ-90-4 | The QR-code library is malicious or compromised (supply-chain attack on the vendored static asset). | Catastrophic (browser-side code execution under the camera or server origin) | Very Low (one-shot vendor + SRI hash + license review) | RC-90-4: library is vendored at a pinned version and SRI-hashed in the script tag (AC-18). License compliance + supply-chain review before merge. No CDN load; no auto-update. The hash is verified by an automated check on every CI run (script in `tools/check_static_hashes.py` — pattern from spec #245). |
| HAZ-90-5 | The QR encodes a URL the operator's phone treats as untrusted (browser warns "this site uses HTTPS with a self-signed cert") — operator clicks through warning out of habit, getting trained to ignore TLS warnings. | Moderate (security hygiene erosion) | Medium (camera and server use self-signed certs by default) | RC-90-5: this is a pre-existing issue with `.local` access too — both endpoints already require accepting a self-signed cert. The QR doesn't make it worse. The operator help doc (`network-fallback.md`) explains the cert warning is expected and documents how to install the camera's CA on a phone. (Out-of-scope improvement: ship a setup-time cert-install QR that delivers the CA bundle. Tracked as a follow-up issue, not in this spec.) |
| HAZ-90-6 | An admin shoulder-surfs the server's IP from the dashboard footer and uses it to bypass `homemonitor.local` from outside the LAN if the router is misconfigured to forward port 443. | Minor (network discipline, not a new disclosure) | Very Low (router misconfig + active observer + on-LAN at the time) | RC-90-6: the IP block on the login page is gated to `request.host` being an RFC1918 address (AC-5). When accessed over a public WAN IP, the block is empty. So the IP block does not leak the LAN address to a remote attacker. Existing TLS + auth gates apply unchanged. |
| HAZ-90-7 | The unauthenticated `/api/v1/system/network` endpoint becomes a reconnaissance vector for an on-LAN attacker mapping the network. | Minor (the IP is already obtainable via `arp -a` or DHCP table from any LAN host) | Low | RC-90-7: the endpoint returns ONLY the IP the requester already used to reach it (`request.host`). It does not enumerate other interfaces, the camera fleet, the gateway, or anything not already implied by reaching the server. Rate-limited at the existing reverse-proxy / Flask layer. Anonymous access is documented in the threat model deltas below. |
| HAZ-90-8 | The QR rendering JS allocates so much DOM the page becomes janky on a low-end phone (e.g. Pi-camera's status page on a battery-saver browser). | Minor (UX) | Low (QR is ≤ 200 × 200 px, ≤ a few KB of canvas) | RC-90-8: the QR library is < 10 KB compressed; canvas size is fixed; no animation. Browser-level smoke checks the page does not regress in mobile-Lighthouse score by > 5 points. |
| HAZ-90-9 | An operator copies the IP-URL from the dashboard, pastes into a system-wide hosts file or a phone's "personal hotspot" list, and the IP later changes without their knowledge — they bookmark a URL that no longer resolves. | Minor (UX) | Medium | RC-90-9: documented in `network-fallback.md` ("Bookmark may go stale if your router rotates DHCP leases; revisit `homemonitor.local` to pick up the new IP"). The dashboard always shows the current IP, so the operator can re-bookmark. No code change. |
| HAZ-90-10 | The client-side QR rendering happens before `wifi.get_ip_address()` returns, racing on slow boot — operator sees a flash of empty IP block. | Minor (cosmetic) | Low | RC-90-10: the inline script renders the IP block only after `/api/status` resolves with a non-empty `ip_address` (AC-2 / AC-17). Otherwise the block is hidden cleanly. No flash because the block starts hidden. |
| HAZ-90-11 | The camera status page exposes the IP to a viewer who, on a shared family LAN, was not previously meant to know the IP (e.g. a guest viewer). | Minor (privacy) | Very Low | RC-90-11: the camera status page already shows the IP at `h-ip` (line 488 of `status.html`). This spec adds a QR on the same page; it does not expand who sees the IP. Anyone authenticated to the camera's own status page is, by the existing access policy, allowed to know how to reach the camera. |
| HAZ-90-12 | The cache file accumulates an old IP after the operator factory-resets the server and brings up a new server image at a different IP, AND the camera retries the cache before the operator updates `server_ip`. | Minor (operational, transient) | Low | RC-90-12: cache hostname-match check (AC-10) catches this if `server_ip` was changed. If `server_ip` is unchanged but the server itself changed IP, the cache produces one round of heartbeat failures, then live resolution returns the new IP and the cache is overwritten (AC-13). The operator-facing fault (`mdns_resolution_failed`) surfaces if live resolution also fails. |

Reference `docs/risk/hazard-analysis.md` for the existing register; this
spec adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs):

- **Sensitive paths touched:** none. The change does NOT modify
  `**/auth/**`, `**/secrets/**`, `**/.github/workflows/**`, `pairing.py`,
  certificate / TLS / OTA flow code, or `docs/cybersecurity/**` beyond
  adding one line to the third-party-library register. The change is
  confined to:
  - `app/camera/camera_streamer/templates/setup.html` (+ a partial)
  - `app/camera/camera_streamer/templates/status.html` (+ a partial)
  - `app/camera/camera_streamer/lifecycle.py` (cache I/O on a
    daemon thread, no auth-relevant code)
  - `app/camera/camera_streamer/static/qrcode.min.js` (vendored)
  - `app/server/monitor/api/system.py` (new read-only endpoint)
  - `app/server/monitor/templates/login.html`,
    `dashboard.html` (UI blocks)
  - `app/server/monitor/services/audit.py` (one constant)
- **No new persisted secret material**. The cache file
  (`/data/config/server_resolved_ip`) holds an IPv4 address and a
  hostname — not secret on a LAN where an `arp` scan reveals the same
  data. File mode 0644 is acceptable; matches existing `/data/config`
  conventions.
- **Auth on `/api/v1/system/network`**: intentionally NONE. The
  endpoint is reachable by the login page so an unauthenticated
  user can grab the server URL. It returns no data not already
  visible to anyone reaching the server (the URL they already used
  to reach it). Rate-limit interaction: the endpoint is expected to
  be called once per page load; no batching, no enumeration. We
  inherit the existing app-level rate-limit (no new bypass).
- **Input validation**: `request.host` is parsed via Werkzeug's
  existing helpers (no manual string splitting); `socket.gethostbyname`
  is called with a hostname we already received in the request — no
  user-controllable string interpolation.
- **No subprocess invocation in any new code on the server side.**
  The fallback `nmcli` / `netifaces` lookup is gated behind a guard
  that only fires when `request.host` was not a literal IP — and
  only if the implementer chooses subprocess over `netifaces`.
  Subprocess argv is fixed; no operator string reaches argv.
- **No outbound network calls** added. The `/api/v1/system/network`
  endpoint returns data inferred from the inbound request only.
- **Audit completeness**: `SYSTEM_NETWORK_FALLBACK_VIEWED` records
  authenticated fetches (AC-21). Anonymous fetches are NOT audited
  to avoid a noisy floor of pre-login scrapes; the trade-off is
  documented and the threat model assumes anyone on the LAN can
  scan the server already.
- **CSRF**: not relevant — endpoint is `GET` only; no state change.
  Existing CSRF middleware allows GETs by default.
- **CORS**: not changed. Endpoint serves same-origin only; no CORS
  headers added.
- **Vendored static asset (QR library)**: SRI-hashed in the
  `<script>` tag, integrity-verified by the browser. License (MIT)
  recorded; supply-chain review on the chosen version. CI hash
  check (per HAZ-90-4 RC) prevents silent replacement.
- **Cache file integrity**: the cache is read-only consumed by the
  `_ServerResolver`; no other component reads it. A malicious
  edit (root user with shell on the camera) can poison the IP for
  one heartbeat cycle until live resolution overrides it. This
  attacker already has root on the device — out of scope.
- **Visibility of camera IP via the dashboard**: unchanged
  (admin-only). The new "Open camera" link wraps the existing
  IP text in an `<a>`; no new audience.
- **TLS posture**: unchanged. The IP URL the QR encodes hits the
  same self-signed cert the `.local` URL hits today. Operator
  trust-on-first-use experience is unchanged.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-90` — User need: "When `.local` doesn't resolve, I want a clear,
  product-supplied path to reach my camera or server without typing an
  IP address from memory or running network-scanner tools."
- `SYS-90` — System requirement: "The system shall expose the camera
  and server IPs through scannable in-product UX (QR code + copy-able
  URL) on the relevant setup, status, and login surfaces, AND shall
  cache the resolved server IP on the camera so a transient mDNS outage
  at boot does not break heartbeat coverage."
- `SWR-90-A` — Camera setup-completion view renders an IP-URL block
  with QR + Copy when `ip_address` is non-empty.
- `SWR-90-B` — Camera status page renders a "Reach this camera" block
  with QR + Copy.
- `SWR-90-C` — Server login page renders a "Server address" block with
  QR + Copy when `request.host` resolves to an RFC1918 IP.
- `SWR-90-D` — Server dashboard renders the same "Server address" block
  in its footer band.
- `SWR-90-E` — `_ServerResolver` persists every successful resolution to
  `/data/config/server_resolved_ip` atomically.
- `SWR-90-F` — `_ServerResolver` primes its `_resolved_ip` from the
  cache on `start()` if hostname matches and age ≤ 7 days.
- `SWR-90-G` — `_ServerResolver` retry thread runs unchanged regardless
  of cache priming; fault emission unchanged.
- `SWR-90-H` — `GET /api/v1/system/network` returns
  `{server_url, ip, port, source}` derived from `request.host`, gated
  to RFC1918 source addresses.
- `SWR-90-I` — Audit event `SYSTEM_NETWORK_FALLBACK_VIEWED` recorded on
  authenticated fetches.
- `SWA-90` — Software architecture item: "QR rendering is client-side
  via a vendored MIT-licensed library; the cache file is plain JSON in
  `/data/config/`; the new endpoint is a thin same-origin GET."
- `HAZ-90-1` … `HAZ-90-12` — listed above.
- `RISK-90-1` … `RISK-90-12` — one per hazard.
- `RC-90-1` … `RC-90-12` — one per risk control listed above.
- `SEC-90-A` (vendored-asset SRI + hash check), `SEC-90-B` (anonymous
  endpoint return-value gate to RFC1918 hosts), `SEC-90-C` (cache file
  permission + atomic rewrite).
- `THREAT-90-1` (stale cache routes heartbeat to wrong host),
  `THREAT-90-2` (vendored asset compromise),
  `THREAT-90-3` (anonymous endpoint as recon vector).
- `SC-90-1` … `SC-90-N` — controls mapping to the threats above.
- `TC-90-AC-1` … `TC-90-AC-21` — one test case per acceptance
  criterion above.

## Deployment Impact

- **Yocto rebuild needed**: very likely **no**. The new vendored static
  asset lives under `app/camera/camera_streamer/static/`, which the
  existing camera packagegroup ships as part of the streamer package
  via a bulk-include rule. Implementer must verify no `.bbappend`
  manifest edit is needed; if a recipe edit IS required, the
  validation matrix row "Yocto config or recipe" applies AND the
  spec's deployment impact upgrades from "no rebuild" to "rebuild
  required."
- **OTA path**: standard OTA. On first boot of the new image:
  - Camera-side: the static QR library lands under `/opt/camera/static/`;
    `setup.html` and `status.html` start serving the new partial.
  - The cache file `/data/config/server_resolved_ip` does not exist
    yet; resolver behaves as today (live retry only). On the first
    successful live resolution the cache is written.
  - Server-side: the new `/api/v1/system/network` endpoint becomes
    reachable; login and dashboard pages start rendering the
    "Server address" footer block.
  - **Cameras themselves** require no firmware update for the *server*
    side of the change. The *camera* side change is included in the
    same OTA bundle as the streamer code.
- **Hardware verification**: required (low-risk).
  - Smoke entries listed in Validation Plan above.
- **Default state on upgrade**: every camera's cache file is empty on
  first boot; no behaviour change until first live resolution. Every
  setup-complete view starts showing the IP-URL block immediately.
- **Disk-space impact**: negligible. Cache file is < 200 bytes.
  Vendored QR library is < 10 KB.
- **CPU-time impact**: zero on the camera at runtime (QR is rendered
  in the browser). Server-side: one IP lookup per `/api/v1/system/network`
  fetch — undetectable.
- **Backwards compatibility**: legacy clients that don't know about the
  new endpoint continue to work — the endpoint is additive. Legacy
  cameras without the cache file continue to work — the cache is
  optional. Pre-feature server images that receive a request that
  somehow includes new fields ignore them — no schema field is added
  to existing endpoints.

## Open Questions

(None of these are blocking; design proceeds. Implementer captures
answers in PR description.)

- **OQ-1: Where exactly on the server dashboard does the "Server
  address" block live?** Two options:
  1. Footer band on every dashboard page (always visible, low
     friction).
  2. Inside the existing Settings → System tile (out-of-the-way,
     lower discoverability).
  **Recommendation:** footer band. The whole point of the block is
  discoverability. Settings-buried fails the "find me when `.local`
  is broken" use case.
- **OQ-2: Should the camera status page's "Reach this camera" block
  also show the camera-streamer hostname (as the user-friendly name)
  in the URL line, with a tag "if hostname doesn't work, use the IP
  below"?** Slightly more wordy; trades against the Custom-style
  emphasis on the IP being the always-works link.
  **Recommendation:** show the IP URL prominently with a small "by
  hostname:" secondary line. Mirrors the setup-completion view's
  treatment for consistency.
- **OQ-3: Server-side QR rendering vs. client-side.** Spec #238 (TOTP
  2FA) leaned server-side via `qrcode[pil]`. For this spec, client-
  side keeps the camera image free of Pillow. The server side is
  free to use either; recommendation is **client-side both places**
  for consistency and to make the vendored library pay for itself
  twice.
  **Recommendation:** client-side, single vendored library.
- **OQ-4: Cache TTL.** 7 days vs. 30 days vs. "until the camera
  records an IP-different success." Longer TTL helps a camera coming
  out of long storage; shorter TTL helps a router that rotates DHCP
  every week.
  **Recommendation:** 7 days. Matches typical residential DHCP lease
  windows. The cost of being wrong is one heartbeat-cycle delay
  (HAZ-90-1 RC).
- **OQ-5: Should `_ServerResolver` write the cache from the resolver
  thread or hand it off to a sync queue?** Hand-off adds latency and
  one more thread to reason about. Direct write is < 1 ms on
  `/data/`.
  **Recommendation:** direct write from the resolver thread, with
  the OSError-tolerant wrapper from RC-90-3.
- **OQ-6: Is the Pi camera's setup-completion view the right place
  for the QR, or should we also surface it during the in-progress
  pairing step (when `setup_complete` is false but pairing is
  succeeding)?** Earlier surfaces help the operator if pairing
  itself flakes — but the IP isn't stable until after WiFi join.
  **Recommendation:** post-pairing only. The pre-pairing IP (on the
  hotspot) is `192.168.4.1` and not useful as a fallback.
- **OQ-7: Should the operator-help doc link to a video or animation?**
  Out of scope for v1 — text + screenshots is sufficient.
- **OQ-8: Should the server expose a `/api/v1/system/network` IP for
  Tailscale interfaces too** (so a remote-review user gets the
  100.x.y.z address)? This would be useful but expands scope.
  **Recommendation:** v1 returns LAN IP only; the Tailscale spec
  (`r1-local-alert-center-and-tailscale-remote-review.md`) owns the
  remote-IP surfacing.

## Implementation Guardrails

- Preserve the structural mDNS fixes already on `main` (#198, #199,
  #200, #233). Do NOT edit `discovery.py`, `_verify_publish_alive()`,
  `set_hostname()`'s goodbye broadcast, or the `_ServerResolver`
  retry loop. Cache priming is additive: it sets `_resolved_ip`
  earlier; it does not replace any existing logic.
- Preserve the **modular monolith** (ADR-0006): the new endpoint is
  in-process; no separate service.
- Preserve the **service-layer pattern** (ADR-0003): keep the API
  handler thin, helper logic lives in a small module
  (`app/server/monitor/services/network_info.py` if the implementer
  judges it warranted; otherwise inline in `system.py` is acceptable
  for ~30 lines).
- Preserve the **server↔camera control channel** (ADR-0015): no wire
  change.
- Preserve **local-first** (`mission-and-goals.md`): no outbound
  network calls.
- Preserve **read-only rootfs**: the camera-side cache is in `/data/`,
  not in the source tree. Already standard pattern.
- Vendor the QR library once, hash-pin it, license-review it before
  merge. Add a CI check (or extend an existing one) that asserts
  the file's SHA matches the value recorded in
  `docs/cybersecurity/third-party-libraries.md`.
- Tests + docs ship in the same PR as code, per
  `engineering-standards.md`. Operator help under
  `docs/guides/network-fallback.md` lands in the same PR.
- Traceability matrix updated in the same PR; `python
  tools/traceability/check_traceability.py` must pass.
- Audit constants and event detail surface follow the existing
  pattern in `audit.py` (no PII; structured fields only; counted
  rate-limit on noisy events).
- Browser-level tests follow the existing pattern in the dashboard
  smoke suite; if no smoke harness exists for `setup.html` /
  `status.html`, the implementer adds one OR documents the manual
  verification steps in the PR.
