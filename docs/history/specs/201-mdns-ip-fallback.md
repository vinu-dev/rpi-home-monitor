# Feature Spec: QR-Code IP Fallback On Setup-Completion Page

Tracking issue: #201 (sub-fix 5 of #90). Branch: `feature/201-mdns-ip-fallback`.

## Title

Render a scannable QR code on the camera's first-boot setup-completion page
that encodes `https://<ip>:443`, alongside the existing `https://<hostname>.local`
URL, so a phone user can reach the device by IP when their router or AP
blocks/rate-limits multicast and `.local` resolution fails.

## Goal

A user finishes the camera's first-boot WiFi setup. The current "Camera
Address" card on the setup-completion view shows `https://<hostname>.local`
and that's it (`app/camera/camera_streamer/templates/setup.html:114,172,273`).
On networks where mDNS works, this is fine. On networks that block multicast
(documented root cause in #90 §4 — common consumer routers and managed APs),
the user has no on-device path to the camera until they look up its IP from
their router's admin UI or wait for the dashboard to discover it later. That
is the gap.

This feature closes the gap by adding a QR code to the same card. The QR
encodes `https://<live-wlan0-ip>:443`, which the user scans with their phone
camera. The phone opens the camera's status page directly, bypassing mDNS
entirely. The text URL (`.local`) is preserved unchanged for users on
mDNS-friendly networks; the QR is an *addition*, not a replacement.

Concretely the feature delivers:

- A small inline QR-code SVG rendered in the `#cam-address-card` section of
  `setup.html` whenever `/api/status` reports a non-empty `ip_address`.
- One new field, `ip_address`, in the WiFi-setup-server's `/api/status`
  JSON response (`app/camera/camera_streamer/wifi_setup.py:308-328`),
  populated from `wifi.get_ip_address("wlan0")`
  (`app/camera/camera_streamer/wifi.py:270-284`).
- A vendored, MIT-licensed, pure-JS QR generator (~5-10 KB minified)
  inlined into `setup.html` via the existing template-substitution path
  (`wifi_setup.py:401-407`). No new HTTP route, no new static asset
  served, no Yocto rebuild.
- A failure path: when `ip_address` is empty (DHCP slow, hostap mode
  still up, link down), the QR slot renders a "Resolving IP…" placeholder
  matching the existing "Resolving…" idiom for the text URL
  (`setup.html:173`). No JS exception, no broken layout.
- A second render path on the **result view** (`setup.html:140-154,266-278`),
  so a user who reconnects to the hotspot post-success and lands on the
  `view-result` panel also sees the QR + IP URL.

The acceptance criterion in the issue body for "Browser-side test asserts
the QR appears on the page, encodes a valid URL, and decodes back to the
camera's actual IP" is satisfied through a combination of: (a) a contract-
level integration test on `/api/status` that asserts `ip_address` is
populated and reflects `wifi.get_ip_address` mocking, (b) a static-HTML
test on the served `setup.html` that asserts the QR container element +
data-target attribute are present and that the inlined library is
non-empty, and (c) a node-side decode-round-trip unit test that feeds the
generator the same IP and asserts the produced SVG decodes back to the
expected URL. See AC-9, AC-12, AC-13.

## Context

Existing code this feature must build on:

- `app/camera/camera_streamer/templates/setup.html:101-137` — the
  "CONNECTING VIEW" container `#view-connecting` that hosts the
  `#cam-address-card` (lines 112-122). Today this card renders a single
  monospace text URL (`#cam-address-display`, line 114) and a "Login:
  admin / your password" hint (line 120). The QR slot is added inside
  this card, between the text URL block and the login-hint paragraph.
- `app/camera/camera_streamer/templates/setup.html:139-154` — the
  "RESULT VIEW" container `#view-result`, shown when a returning user
  reconnects to the hotspot after a successful setup. Lines 273-276
  build `var url = d.hostname ? 'https://' + d.hostname + '.local' : '';`
  and inject it into `#result-msg`. The QR slot also goes here.
- `app/camera/camera_streamer/templates/setup.html:157-294` — the
  inline `<script>` block. New JS additions land here; no external
  script tags. The script already calls `fetch('/api/status')` on page
  load (line 267) and on result-view branches (line 270 onwards). The
  QR-render function reuses this existing fetch result; it does NOT
  add a second HTTP round-trip.
- `app/camera/camera_streamer/wifi_setup.py:308-328` — `do_GET` handler
  for `/api/status`. Today it returns `status, error, setup_complete,
  camera_id, hostname`. The new field `ip_address` is added here,
  populated by `wifi.get_ip_address(WIFI_INTERFACE)` (interface name
  via the existing module constant in `wifi.py`, default `"wlan0"`).
  Empty string when unavailable (matches existing `hostname` semantics
  on failure — line 297).
- `app/camera/camera_streamer/wifi_setup.py:401-410` — `_serve_setup_page`,
  which loads `setup.html` and substitutes `{{CAMERA_ID}}` and
  `{{HOSTNAME}}`. **The vendored QR library is injected here via a new
  `{{QRCODE_LIB}}` substitution.** The library content is loaded once at
  module import via `_load_template("vendor/qrcode.min.js")` (or the
  equivalent path-aware loader; see Module Impact). This avoids reading
  the library file on every request without adding an HTTP route.
- `app/camera/camera_streamer/wifi.py:270-284` — `get_ip_address(interface)`,
  which already calls `nmcli -t -f IP4.ADDRESS device show <iface>` and
  strips the CIDR suffix, returning `""` on any subprocess or parse
  failure. **This is the single source of truth for the camera's own
  IP.** No new IP-discovery code is added.
- `app/camera/camera_streamer/wifi.py:287-298` — `get_hostname()`,
  used today by `/api/status` (line 319). The new field sits next to
  this one in the response dict; both are best-effort empty-string-on-
  failure.
- `app/camera/camera_streamer/status_server.py:843-899` — the **post-
  pairing**, authenticated `/api/status` endpoint. Note: this is a
  DIFFERENT server from the wifi-setup-server (`wifi_setup.py`). The
  post-pairing one already returns `ip_address` (the dashboard reads it
  there per #90 sub-fix 1). **This spec touches only the wifi-setup
  server**, which is the one the user reaches during first-boot before
  pairing. The two share `wifi.get_ip_address` as the implementation.
- `app/camera/tests/contracts/test_api_contracts.py:265-345` — the
  existing pattern for testing the wifi-setup `/api/status` endpoint.
  New test cases (AC-12, AC-13) extend this file. Sets up a real
  `WifiSetupServer`, hits `/api/status` over HTTP, asserts JSON shape.
- `app/camera/tests/integration/test_wifi_setup.py` — the existing
  integration test file for setup-stamp + lifecycle. New rendered-HTML
  tests for the served `setup.html` body land here OR in a new
  `test_wifi_setup_html.py` (Implementer's call; both satisfy the
  validation matrix).
- `app/camera/requirements.txt` — currently `numpy` only. **No QR
  Python library is added.** All QR generation happens client-side in
  the inlined JS.
- `meta-home-monitor/recipes-core/images/home-camera-image.inc:27-30` —
  the camera image manifest. **Unchanged.** No new package, no new
  recipe, no Yocto rebuild required.

ADR / spec cross-references:

- ADR-0009 (camera-pairing mTLS) — pairing trust is established via the
  existing 6-digit-PIN flow on the post-setup status page. The QR on
  the *setup* page does NOT carry pairing material and does NOT alter
  the pairing trust model. The QR target is the same status-page URL
  that today's text URL points to; trust-on-first-use TLS is unchanged.
- Spec parent: issue #90 root-cause analysis §4 ("mDNS is multicast and
  many networks block or rate-limit multicast"). Sub-fix 1 (IP visible
  on dashboard / authenticated status page, shipped) plus this sub-fix
  5 (IP reachable from setup completion via QR) cover both pre-pairing
  and post-pairing IP-fallback paths.

## User-Facing Behavior

### Primary path

1. User installs the camera, powers it on, joins the `HomeCam-Setup`
   hotspot from their phone, opens `http://10.42.0.1/setup`.
2. User fills in WiFi SSID + password + server IP, clicks **Save & Connect**.
3. The setup view transitions to `#view-connecting` (existing behaviour);
   the card displays "Connecting to WiFi…" and the existing text URL
   `https://<hostname>.local`.
4. The hotspot drops, the camera joins the user's home WiFi, DHCP
   completes. Phone reconnects to the home WiFi (not yet returning to
   the camera).
5. Within ~5-10 s of the camera obtaining a DHCP lease, the next
   `/api/status` poll on the camera (only fires while the user is on the
   hotspot) returns `ip_address`. **For users who scan the QR before
   reconnecting their phone to home WiFi:** the rendered card is the
   one served at the moment of `/api/connect` response, populated with
   `ip_address` if the camera obtained DHCP before the hotspot dropped,
   else empty (handled by the failure path below).
6. **Returning-user variant:** the user reconnects their phone to the
   hotspot after the camera comes back up (the hotspot reappears if the
   join failed; the user may retry). On the returning visit the result
   view fetches `/api/status` (line 267) and renders the QR + IP from
   the now-populated response. This is the realistic happy path for
   "user keeps the hotspot open until they see confirmation."
7. User points their phone camera at the QR. Phone opens
   `https://192.168.1.42:443` (or whatever the camera's actual IP is).
8. Browser warns about the self-signed TLS certificate (pre-existing
   behaviour — same warning the user would see typing the URL by hand;
   resolved post-pairing via the TOFU model in ADR-0009). User accepts
   the warning, lands on the camera's status page, follows the existing
   pairing flow ("get a PIN from the server dashboard, enter it here").

### Failure states

- **IP not yet acquired (DHCP slow, link down, NetworkManager not yet
  reporting):** `wifi.get_ip_address` returns `""`. The QR slot renders
  a placeholder element with the text "Resolving IP…" and no QR image.
  The text URL above ("`https://<hostname>.local`" or "Resolving…") is
  unchanged. No JS exception; the layout stays stable.
- **Hostname empty AND IP empty:** the existing card already shows
  "Resolving…" for the text URL (line 173). The QR slot also shows
  "Resolving IP…". The user can reload after a few seconds.
- **`/api/status` request fails entirely (camera-side server crash,
  network drop):** `fetch` rejects → existing catch-block path (line
  254-257 currently calls `showConnecting('')` on connect failure).
  The QR slot stays in its initial empty state ("Resolving IP…"). No
  silent broken UI; the existing "If it failed, the HomeCam-Setup
  hotspot will reappear in ~30 seconds" hint (line 134-136) covers the
  recovery path.
- **QR library failed to load (template substitution returned empty,
  malformed JS, browser parse error):** the JS render function is
  guarded by `typeof QRCode !== 'undefined'`. If the library isn't
  available, the QR slot stays empty and the text URL renders normally.
  The failure is logged to the browser console only; the page stays
  usable. AC-14 covers this.
- **IP address present but is a link-local 169.254.x.x (DHCP failed,
  auto-IP fallback):** the QR encodes the link-local IP. The user's
  phone on home WiFi cannot reach a link-local on a different subnet.
  This is a degenerate case (camera failed to get DHCP) where no
  client-side fix helps. The QR truthfully encodes what the camera
  has; the user's recourse is to fix DHCP. Documented in Operator
  Notes; not a code path we suppress (suppressing would silently hide
  a real problem).
- **IPv6 address returned by `nmcli` instead of IPv4:** `wifi.get_ip_address`
  today filters to `IP4.ADDRESS` only, so this case cannot occur via
  that helper. If a future change adds `IP6.ADDRESS`, the QR formatter
  must wrap a colon-bearing host in `[brackets]` per RFC 3986
  (e.g. `https://[fe80::1]:443`). Not in scope for v1; AC-7 explicitly
  asserts IPv4-only.
- **Multi-interface camera (Ethernet + WiFi):** `wifi.get_ip_address`
  is hardcoded to `wlan0`; that's the only IP the QR encodes. If a
  future product variant adds Ethernet (`eth0`) the spec needs to
  decide on interface preference. Out of scope for v1; the current
  product is WiFi-only.

### Edge cases

- **User scans the QR while still on the hotspot:** the URL
  `https://<wlan0-ip>:443` is on the user's home subnet, NOT on the
  hotspot subnet (`10.42.0.0/24`). The phone request will time out
  until the user reconnects to home WiFi. The "Next Steps" copy
  already instructs "Connect your phone back to your home WiFi, then
  open the address above" (line 117, 127). No additional copy needed
  for the QR — the existing instructions cover both the text URL and
  the QR.
- **Re-render after `/api/status` polls populate `ip_address`
  mid-session:** the JS function `renderQrIfPossible(d.ip_address)` is
  idempotent — calling it twice with the same IP is a no-op (the slot's
  `data-rendered-for` attribute is checked first). Calling it with a
  new IP replaces the QR. AC-10 covers this.
- **User refreshes `/setup` after connection:** the page re-fetches
  `/api/status`, which now returns `setup_complete=true` and a non-
  empty `ip_address`. The result view renders the QR + IP. AC-11 covers
  this.

## Acceptance Criteria

Each criterion names its verification mechanism. Implementer maps these
to `TC-201-AC-N` in the traceability matrix.

- AC-1: `/api/status` on the wifi-setup-server (`wifi_setup.py:308`)
  returns a new `ip_address` field whose value is the result of
  `wifi.get_ip_address("wlan0")` at request time, or `""` if that helper
  raises or returns empty.
  **[contract test in `test_api_contracts.py`, mocked
  `wifi.get_ip_address`]**
- AC-2: The `ip_address` field is `""` (not omitted, not `None`) when
  the camera has no DHCP lease yet. JSON shape is stable across success
  and failure paths.
  **[contract test]**
- AC-3: The serving handler `_serve_setup_page` substitutes
  `{{QRCODE_LIB}}` with the contents of the vendored QR JS library at
  import time (loaded once, not per request) and writes the resulting
  HTML body in one response.
  **[unit test on `_serve_setup_page` body output: assert
  `setup.html`'s QR-related sentinel string is present in the served
  bytes; assert the library marker (e.g. function name `QRCode`) is
  present]**
- AC-4: The vendored QR library file lives under
  `app/camera/camera_streamer/templates/vendor/qrcode.min.js` (or the
  Implementer-chosen sibling path), is single-file, MIT-licensed,
  ≤ 15 KB minified, and has its license header preserved verbatim at
  the top of the file. The file is committed to the repo (no build
  step, no npm install at deploy time).
  **[repo-level: file exists, license check via
  `python tools/licenses/check_third_party.py` if present, else manual
  spec-acceptance step in PR description]**
- AC-5: A new `#cam-address-qr` element exists inside `#cam-address-card`,
  positioned between the existing text URL block and the login-hint
  paragraph. It carries `data-target-url=""` initially.
  **[unit test on rendered HTML: BeautifulSoup parses the `#cam-address-card`
  and asserts the `#cam-address-qr` child is present in the documented
  position]**
- AC-6: When `d.ip_address` is non-empty, the JS function
  `renderQrIfPossible(d.ip_address)` populates `#cam-address-qr` with
  an inline SVG (or canvas) QR encoding `https://<ip_address>:443` and
  sets `data-target-url` and `data-rendered-for` to that URL.
  **[node-side unit test: import the JS function via a thin test
  harness OR a Python test that runs the rendered HTML through
  `pytest-playwright` (see OQ-2); assert `data-target-url` matches the
  expected URL]**
- AC-7: The encoded URL is exactly `https://<ip_address>:443` —
  literal scheme `https`, literal port `443`, no path, no query, no
  fragment, IPv4-only (no `[brackets]` for v1).
  **[unit test, parametrized over `192.168.1.42`, `10.0.0.1`,
  `172.20.5.7`]**
- AC-8: When `d.ip_address` is empty (initial state, or DHCP slow), the
  `#cam-address-qr` element renders the placeholder text "Resolving IP…"
  and contains no `<svg>` / `<canvas>` child. `data-rendered-for`
  remains empty.
  **[unit test on rendered HTML with mocked `/api/status` returning
  empty `ip_address`]**
- AC-9: The browser-side acceptance from the issue body — "QR decodes
  back to the camera's actual IP at the time the page renders" — is
  verified by a node-side decode-round-trip test: feed the generator a
  known IP, parse the produced SVG/canvas with a QR decoder library
  (test-only dependency, e.g. `jsQR`), assert the decoded text equals
  `https://<ip>:443`. The test does NOT require headless-browser
  infrastructure.
  **[node-side unit test, NEW test harness file under
  `app/camera/tests/`; OR equivalent Python test if the Implementer
  chooses to drive Playwright per OQ-2]**
- AC-10: Calling `renderQrIfPossible(ip)` twice with the same IP does
  not produce a duplicate QR (idempotency); calling it with a new IP
  replaces the previous QR.
  **[unit test on the JS function via the same harness used for AC-9]**
- AC-11: The result view (`#view-result`) ALSO renders the QR via the
  same `renderQrIfPossible` path when `d.setup_complete` is true and
  `d.ip_address` is non-empty. The result view's existing text URL
  (`https://<hostname>.local`, line 273) remains unchanged in shape;
  the QR is added inside `#result-msg` or as a sibling card below.
  **[unit test on rendered HTML for the result-view branch]**
- AC-12: `python tools/traceability/check_traceability.py` passes with
  the new `REQ:` annotations on `wifi_setup.py` (added field), the new
  vendored JS file (`REQ:` in a comment header), and the new test files.
  **[CI gate]**
- AC-13: `ruff check .` and `ruff format --check .` pass on
  `wifi_setup.py` and any new Python test files.
  **[CI gate]**
- AC-14: When the QR library is empty / malformed (test substitutes a
  broken library at template-substitution time), the served page still
  renders, the text URL still appears, and the QR slot stays empty.
  No JS exception causes the rest of the script to abort.
  **[unit test on rendered HTML with a malformed library substitution;
  assert `#cam-address-display` still gets populated by the existing
  `showConnecting()` path]**
- AC-15: No regressions in existing `test_api_contracts.py` cases for
  the wifi-setup `/api/status` (AC-1's new field is purely additive;
  unknown-field-tolerant clients see no change).
  **[contract suite passes unchanged]**
- AC-16: Hardware smoke: on a real Pi Zero 2W, completing first-boot
  setup against a live home WiFi, the user sees the QR on the
  result-view, scans it with an iOS or Android stock camera app, and
  the phone opens the camera's status-page URL (modulo the expected
  TLS warning).
  **[manual smoke entry added to `scripts/smoke-test.sh` runbook]**

## Non-Goals

- **Replacing the `.local` text URL.** The text URL stays. The QR is
  an addition. Users on mDNS-friendly networks who already type
  `homecam.local` keep doing so.
- **A separate "find my devices" desktop client.** Option (2) in the
  issue body. Out of scope; revisit only on real demand.
- **Pushing `/etc/hosts` entries to consuming devices.** Option (3) in
  the issue body. Out of scope (requires operator privileges on the
  consumer device).
- **QR codes anywhere other than the setup-completion page.** No QR
  on the dashboard, no QR on the post-pairing status page, no QR in
  emails or notifications. The dashboard already shows the camera IP
  textually (sub-fix 1 of #90, shipped); no UX gain to adding QR there.
- **Encoding pairing material in the QR.** The QR encodes only the
  camera's reachable URL, not the 6-digit PIN, not any credentials,
  not any session token. Pairing remains a manual PIN-entry flow per
  ADR-0009.
- **Encoding the URL with HTTP basic-auth credentials in the userinfo
  section** (e.g. `https://admin:pass@<ip>:443`). Hard-no on security
  grounds: the user just configured the admin password on the previous
  page; embedding it in a QR code visible on a hotspot-served HTML
  page is a credential-leak vector.
- **A Python QR library on the camera side.** The camera image stays
  numpy-only (`requirements.txt`). All QR generation is client-side
  inlined JS. This avoids a Yocto rebuild for a pure-UI feature.
- **Server-side QR generation in the Flask server.** The setup page
  is served by the camera's own `WifiSetupServer` (a stdlib
  `http.server.BaseHTTPRequestHandler`, not Flask). Mixing in a server-
  side QR pipeline would couple the two unnecessarily.
- **A new HTTP route to serve the QR library** (e.g. `/static/qrcode.min.js`).
  Not added; the library is inlined in the `setup.html` body via
  template substitution. Reasons: zero-route surface, zero state, no
  cache-control concerns, no MIME-sniffing concerns. The cost is one
  extra ~10 KB on each `/setup` page load — acceptable for a page
  served at most a few times per camera lifetime.
- **IPv6 support.** v1 is IPv4-only (per AC-7). Adding IPv6 means
  bracket-quoting the host and surfacing it from `nmcli` IP6.ADDRESS;
  defer until product carries IPv6.
- **Multi-interface preference logic** (Ethernet vs WiFi). v1 uses the
  existing `wlan0` IP. The product is WiFi-only today.
- **Auto-refresh of the QR if the camera's DHCP lease changes
  mid-session.** The page re-fetches `/api/status` on the existing
  poll cadence (today: once on load + once on result-view re-entry).
  No background polling is added.
- **Decoding-round-trip in a real browser via Playwright as the only
  acceptance.** AC-9 admits a node-side decoder OR a Python-driven
  Playwright run; either passes. Implementer picks the cheaper path
  given current test infrastructure (see OQ-2).
- **Custom branding / logo overlay in the QR center.** Out of scope.
  Plain QR, max compatibility with stock phone-camera scanners.
- **Persisting any QR-related state to disk.** No new files under
  `/data`. No new config keys.

## Module / File Impact List

**New code:**

- `app/camera/camera_streamer/templates/vendor/qrcode.min.js` (new) —
  vendored QR generator. Recommended candidate: **`qrcode-generator`**
  by Kazuhiko Arase (MIT, ~10 KB minified, no dependencies, exposes
  `qrcode(typeNumber, errorCorrectionLevel).addData(...).make()` and
  `createSvgTag()`). Implementer free to choose an equivalent MIT-or-
  similar single-file lib if smaller, provided the library
  signature is wrapped in a thin `window.QRCode` adapter so the
  call-site in `setup.html` is library-agnostic. License header
  preserved verbatim.
- `app/camera/tests/integration/test_wifi_setup_html.py` (new, OR
  extension of existing `test_wifi_setup.py`) — rendered-HTML tests
  per AC-3, AC-5, AC-6, AC-8, AC-11, AC-14. Uses `WifiSetupServer`
  fixture from `conftest.py:169` plus stdlib `http.client` and
  `bs4.BeautifulSoup` (already a server-side test dep) to parse the
  served HTML.
- `app/camera/tests/unit/test_qr_render.py` (new) — node-side unit
  tests for AC-6, AC-7, AC-9, AC-10. Driven via either:
  - a pytest-managed `node` subprocess that runs the JS function with
    a test harness file checked into `app/camera/tests/unit/qr_harness.js`,
    OR
  - a pytest-playwright headless run that loads the served `setup.html`
    and exercises the JS function in a real browser context.

  Implementer chooses one; both meet AC-9 (see OQ-2).

**Modified code:**

- `app/camera/camera_streamer/wifi_setup.py:308-328` — `do_GET`
  handler for `/api/status`: add `"ip_address": wifi.get_ip_address(
  WIFI_INTERFACE)` to the JSON response dict. The constant
  `WIFI_INTERFACE` already exists in `wifi.py`; if it's not exported,
  pass the literal `"wlan0"` for parity with `_get_setup_status_payload`
  callers elsewhere. Add `# REQ: SWR-201-A` annotation.
- `app/camera/camera_streamer/wifi_setup.py:401-410` —
  `_serve_setup_page`: substitute `{{QRCODE_LIB}}` with the contents
  of the vendored library, loaded once at module import via a new
  module-level constant (e.g. `_QRCODE_LIB = _load_template(
  "vendor/qrcode.min.js")` after `_load_template` is generalised
  if needed; see Implementer Guardrails). Add `# REQ: SWR-201-B`
  annotation.
- `app/camera/camera_streamer/templates/setup.html:112-122` — extend
  `#cam-address-card` with a new child:
  ```html
  <div id="cam-address-qr" data-target-url="" data-rendered-for=""
       style="margin:12px auto;text-align:center;min-height:160px;
              display:flex;align-items:center;justify-content:center;
              color:#8090b0;font-size:0.85em">
    Resolving IP…
  </div>
  ```
  Position: between `#cam-address-display` (line 114) and the
  "Connect your phone back to your home WiFi" paragraph (line 116).
  Inline styling matches the card's existing dark-theme palette;
  no new CSS classes added.
- `app/camera/camera_streamer/templates/setup.html:140-154` — extend
  `#view-result` with the same `#cam-address-qr` mirror element OR
  reuse the same ID inside a result-card sibling (Implementer's
  choice; AC-11 only requires the QR appear when the result view is
  active and `ip_address` is populated). Recommended: a single
  shared element repositioned via JS, OR two distinct IDs
  (`#cam-address-qr-connecting` and `#cam-address-qr-result`) with
  the render function targeting whichever is currently visible.
- `app/camera/camera_streamer/templates/setup.html:157-294` — extend
  the inline `<script>`:
  - At the top, inject `{{QRCODE_LIB}}` substitution marker so the
    library defines `window.QRCode` (or the test harness's adapter)
    before the first `renderQrIfPossible` call.
  - Add `function renderQrIfPossible(ip)` that:
    - returns early if `!ip`,
    - returns early if `typeof QRCode === 'undefined'`,
    - returns early if the target slot's `data-rendered-for` already
      equals `https://<ip>:443` (idempotency, AC-10),
    - generates the QR as inline SVG sized to ~160-200 px,
    - replaces the slot's child nodes with the SVG,
    - sets `data-target-url` and `data-rendered-for`.
  - Call `renderQrIfPossible(d.ip_address)` inside the existing
    `fetch('/api/status')` `.then` block (line 269 onwards) on BOTH
    the `setup_complete=true` branch and the connecting/in-progress
    branch.
  - Wrap all of the above in try/catch so a generator failure does
    NOT abort the rest of the script (AC-14).

**Out-of-tree:**

- No camera-side firmware change beyond the Python edit above.
- No Yocto rebuild. `meta-home-monitor/recipes-core/images/home-camera-image.inc`
  is unchanged. `python3-qrcode` and friends are NOT pulled in.
- No new Python dependency in `app/camera/requirements.txt`.
- No new server-side change. The Flask server's existing post-pairing
  `/api/status` already exposes `ip_address` (sub-fix 1 of #90); no
  duplication.
- No data migration. The new `/api/status` field defaults to `""`
  on every request when the helper fails; no persisted state.
- No new CSS file, no `style.css` modification (the camera has no
  external stylesheet today; all CSS is inline in the templates).

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Camera Python | `pytest app/camera/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | extended `test_api_contracts.py` cases for the new `ip_address` field on the wifi-setup `/api/status` (AC-1, AC-2, AC-15) |
| Frontend / templates | rendered-HTML tests on the served `setup.html` (AC-3, AC-5, AC-8, AC-11, AC-14); node-side or Playwright JS-function tests (AC-6, AC-7, AC-9, AC-10) |
| Security-sensitive path | none touched. The change does NOT modify `app/camera/camera_streamer/pairing.py`, `wifi.py` (only **read** via the existing `get_ip_address` helper, which is not modified), `lifecycle.py`, certificate / TLS / OTA flow, or any file under `**/auth/**` / `**/secrets/**` / `**/.github/workflows/**`. The new `ip_address` field on `/api/status` is unauthenticated *by design*, matching the existing `hostname` field served on the same unauthenticated wifi-setup endpoint. The wifi-setup server only listens on the captive-portal interface (`10.42.0.1`) during first-boot, not on the camera's home-WiFi IP, so the field is reachable only by clients connected to the `HomeCam-Setup` hotspot. See Security section. |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py`, `python tools/docs/check_doc_map.py` |
| Coverage | camera `--cov-fail-under=80` (existing); the wifi_setup change is a 1-line dict addition + 1-line module-import constant — high marginal coverage |
| Yocto config | none — no recipe / image change |
| Hardware behavior | `scripts/smoke-test.sh` row "first-boot a fresh Pi Zero 2W, complete WiFi setup against a 2.4 GHz home network, scan the QR with a phone, confirm the phone opens `https://<ip>:443` modulo the expected TLS warning" (AC-16) |

Smoke-test additions (Implementer to wire concretely in
`scripts/smoke-test.sh`):

- "Flash a fresh image, boot, join `HomeCam-Setup` hotspot, fill setup
  form, save, wait for `view-connecting`, observe the QR appear within
  10 s of DHCP completing, scan with iOS Camera app, confirm Safari
  opens to `https://<ip>:443` (TLS warning expected and accepted)."
- "Repeat with the hotspot dropping mid-DHCP, reconnect to hotspot,
  observe `view-result` with the QR populated, scan and confirm."
- "Repeat on a network that DOES NOT block multicast (mDNS works);
  confirm the existing `.local` text URL still works AND the QR works
  AND nothing visually regresses."
- "Hard-negative: temporarily block the camera's DHCP (give it no
  lease), observe `#cam-address-qr` shows 'Resolving IP…' and does
  NOT crash the rest of the page; restore DHCP, refresh, observe QR."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-201-1 | The QR encodes a stale or wrong IP (e.g. the camera's previous DHCP lease, or a transient autoconf address) and the user's phone lands on someone else's device on the home network. | Major (security / UX trust — user could expose credentials to a wrong host) | Very Low (the IP is read fresh from `nmcli` at request time, not cached) | RC-201-1: `wifi.get_ip_address` calls `nmcli device show wlan0` synchronously per request and reads the current `IP4.ADDRESS`; no caching layer exists. AC-1 asserts this binding. The wifi-setup server is short-lived (only runs during first-boot), so the surface for stale data is bounded. |
| HAZ-201-2 | A malicious client on the `HomeCam-Setup` hotspot intercepts the served `setup.html` and substitutes its own QR pointing to a phishing URL. | Moderate (security) | Low (requires the attacker to be already on the hotspot, which the legitimate user is in the middle of configuring) | RC-201-2: pre-existing trust boundary; the hotspot is unauthenticated by design (the user is provisioning it). The attacker's MITM capability already lets them swap the entire setup form, capture WiFi credentials, etc. The QR does not enlarge this surface. Future hardening (WPA2-PSK on the hotspot with a per-device pre-shared key surfaced as a sticker) is tracked separately under #90 sub-fixes; out of scope here. |
| HAZ-201-3 | The QR encodes an IP on the home subnet, but the user's phone is still on the hotspot subnet (`10.42.0.0/24`) and the scan fails to load. The user concludes the QR is broken. | Minor (UX confusion) | Medium (users do not always reconnect their phone to home WiFi before scanning) | RC-201-3: existing copy "Connect your phone back to your home WiFi" (lines 117, 127) already addresses this. AC-16's hardware smoke explicitly tests both orderings (scan-then-reconnect vs reconnect-then-scan). Optional UX improvement: add a one-liner "(Reconnect your phone to home WiFi first, then scan)" directly above the QR; Implementer's call. |
| HAZ-201-4 | The vendored QR JS library has a known security CVE (e.g. SVG injection via crafted input) that is missed at vendoring time. | Minor (security; input is the camera's own IP, attacker-controlled only if `nmcli` is compromised, in which case the attacker has root and the QR is the least of the user's problems) | Very Low | RC-201-4: chosen library is small (~10 KB) and review-friendly. Implementer must (a) document the upstream version + git SHA / npm version in a `vendor/README.md` so future updates are traceable, (b) not modify the library beyond the minified blob, (c) preserve the upstream license header. The input to the library is `https://<ip>:443` — IP from `nmcli`, scheme/port literal — no operator-controlled string. SC-201-A covers the input-shape constraint. |
| HAZ-201-5 | Inlining ~10 KB of JS bloats the served `setup.html` enough to slow first-boot rendering on the Pi Zero 2W's setup-server. | Minor (UX) | Very Low (Pi Zero 2W serves the page over local hotspot; one-shot per camera lifetime; payload remains < 50 KB total) | RC-201-5: the library is minified; the page is served at most a few times in the camera's lifetime; no measurable user impact. The alternative (a separate route + cached fetch) adds complexity without gain at this scale. |
| HAZ-201-6 | The added field `ip_address` on the unauthenticated `/api/status` discloses the camera's internal IP to any scanner on the hotspot. | Minor (information disclosure) | Low (the hotspot is the user's own first-boot environment; the IP is also discoverable via the dashboard once paired) | RC-201-6: the wifi-setup server only listens on the captive-portal interface during first-boot; once setup completes, the server is torn down (`lifecycle.py` transitions to the post-setup status server on a different bind). Discoverability of the IP is the *intent* of this feature, not an unintended side-channel. SC-201-B covers the bind-scope constraint. |
| HAZ-201-7 | The camera obtains a DHCP lease on a captive-portal-style guest network (e.g. coffee shop) and the QR encodes a routable-but-untrusted IP. The user scans it and the request leaks past the captive portal. | Negligible (operator misuse — the product is a home-network device, the setup hotspot is not intended for use on guest networks) | Very Low | RC-201-7: documented in operator instructions: "set up the camera on the home network you intend to install it on." No code change. |
| HAZ-201-8 | The QR-render JS throws an exception inside the existing `fetch('/api/status').then(...)` block and aborts the rest of the script, breaking the existing failure-state handling (`view-result` for `setup_complete=false`). | Moderate (UX regression) | Low (the render function is small and exception-guarded) | RC-201-8: AC-14 explicitly tests the "library failed to load / throws" path and asserts the rest of the script keeps working. Implementer wraps the render call in a try/catch with a `console.warn` only. |
| HAZ-201-9 | The existing setup smoke test fails after the change because the setup.html body now contains `{{QRCODE_LIB}}` placeholder when read raw (without server-side substitution) and a regex assertion in the smoke trips. | Negligible (test maintenance) | Low | RC-201-9: smoke and template tests must read the **served** body (via `WifiSetupServer`), not the raw template file, exactly as `test_api_contracts.py` already does. AC-3 and AC-5 explicitly bind to the served body. |
| HAZ-201-10 | A future re-skin of `setup.html` deletes the `#cam-address-qr` element and the QR silently disappears without test failure. | Minor (UX silent regression) | Low | RC-201-10: AC-5 asserts the element exists by ID in the served HTML. A re-skin that drops it fails the test. CI gate. |

Reference `docs/risk/hazard-analysis.md` for the existing register;
this spec adds rows.

## Security

Threat-model deltas (Implementer fills concrete `THREAT-` / `SC-` IDs):

- **Sensitive paths touched:** none. The change does NOT modify
  `**/auth/**`, `**/secrets/**`, `**/.github/workflows/**`,
  `pairing.py`, `wifi.py` (read-only use of the existing
  `get_ip_address` helper), `lifecycle.py`, `wifi.py`, certificate /
  TLS / pairing / OTA flow code, or `docs/cybersecurity/**`.
- **No new persisted secret material.** The QR library is non-secret;
  the IP is non-secret within the user's home network; no credentials
  are encoded in the QR (explicit non-goal).
- **Unauthenticated-endpoint addition:** `/api/status` on the wifi-
  setup server is and remains unauthenticated. Adding `ip_address` to
  its response does not change the auth model. The setup-server
  binds only on the captive-portal interface (`10.42.0.1`) during
  first-boot; it is **not** reachable on the camera's home-WiFi IP.
  See `lifecycle.py` for the bind-scope contract.
- **No subprocess invocation in any new code.** The IP read goes
  through the existing `wifi.get_ip_address` helper, which already
  uses `subprocess.run` with a fixed argv (`["nmcli", "-t", "-f",
  "IP4.ADDRESS", "device", "show", interface]`). The new code path
  passes a literal `"wlan0"` (or the existing `WIFI_INTERFACE`
  module constant) — no operator input reaches the argv.
- **No operator-controlled string interpolation reaching the QR
  library.** The library's `addData(...)` input is constructed as
  `'https://' + ipFromApiStatus + ':443'`. `ipFromApiStatus` comes
  from `nmcli`-parsed output (server-side regex `^(?P<ip>[0-9.]+)/`),
  not from any client / form / cookie. SC-201-A pins this constraint.
- **Vendored library integrity:** Implementer documents upstream
  source (URL + version + git SHA / npm version) in a
  `app/camera/camera_streamer/templates/vendor/README.md`; the
  minified blob is committed verbatim with its license header. Future
  updates require a doc-bump in the same file (caught by code review,
  not by an automated check in v1).
- **No new outbound network calls.** The QR library is offline; no
  CDN dependency; no fetch from the rendered page beyond the existing
  `/api/status`, `/api/networks`, `/api/connect`, `/api/rescan`
  endpoints.
- **No new attack surface on the post-setup status server.** The
  post-pairing `status_server.py` is unchanged. The dashboard's
  existing IP-display path is unchanged.
- **CSRF:** the new field is on a GET endpoint; CSRF does not apply.
  The PUT/POST endpoints (`/api/connect`, `/api/rescan`) are
  unchanged.
- **No information leakage in the QR.** The QR encodes only the
  literal URL `https://<ip>:443`. It does NOT encode `Camera-ID`,
  `hostname`, MAC address, admin username, or pairing PIN.
- **Hotspot threat model unchanged.** ADR-0009 documents that the
  first-boot hotspot is unauthenticated by design; the user is
  instructed to provision in a known-good environment. The QR adds
  no new capability to a hostile observer that is not already
  available via the served HTML.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-201` — User need: "When mDNS is unreliable on my network, I want
  a one-scan way to reach the camera from my phone after first-boot
  setup, without typing an IP address by hand."
- `SYS-201` — System requirement: "The camera's first-boot setup-
  completion view shall present the camera's reachable HTTPS URL as a
  scannable QR code in addition to the existing `.local` text URL,
  with graceful fallback when the IP is not yet known."
- `SWR-201-A` — The camera's wifi-setup `/api/status` endpoint shall
  include an `ip_address` field whose value is the result of reading
  the wlan0 interface's current IPv4 address at request time, or `""`
  when unavailable.
- `SWR-201-B` — The wifi-setup server shall serve `setup.html` with a
  vendored client-side QR library inlined via template substitution,
  loaded once at module import (no per-request file read).
- `SWR-201-C` — The setup page shall render a QR code encoding
  `https://<ip>:443` whenever `/api/status` reports a non-empty
  `ip_address`, in both the connecting view and the result view.
- `SWR-201-D` — The QR render shall be idempotent (re-rendering with
  the same IP is a no-op) and shall replace cleanly when the IP
  changes.
- `SWR-201-E` — When the IP is unavailable or the QR library fails to
  load, the QR slot shall display a "Resolving IP…" placeholder
  without breaking the rest of the page.
- `SWR-201-F` — The QR shall encode IPv4-only URLs, scheme `https`,
  port `443`, no path / query / fragment, and must NOT encode
  pairing PIN, credentials, or any session token.
- `SWA-201` — Software architecture: "QR generation is client-side
  only; the camera image carries no Python QR dependency; the QR
  library is a vendored single-file MIT-licensed JS asset inlined
  into `setup.html` via the existing template-substitution path."
- `HAZ-201-1` … `HAZ-201-10` — listed above.
- `RISK-201-1` … `RISK-201-10` — one per hazard.
- `RC-201-1` … `RC-201-10` — one per risk control listed above.
- `SEC-201-A` (QR-input shape: literal scheme/port + nmcli-parsed
  IPv4 only; no operator interpolation),
  `SEC-201-B` (wifi-setup server bind scope: captive-portal interface
  only, torn down post-pairing),
  `SEC-201-C` (vendored library provenance documented in
  `vendor/README.md`).
- `THREAT-201-1` (hotspot MITM substitutes QR for phishing target —
  pre-existing trust model, no new surface),
  `THREAT-201-2` (stale IP encoding — bounded by request-time
  `nmcli` read),
  `THREAT-201-3` (vendored-library CVE — documented provenance +
  small audit surface).
- `SC-201-1` … `SC-201-N` — controls mapping to threats above.
- `TC-201-AC-1` … `TC-201-AC-16` — one test case per acceptance
  criterion above.

## Deployment Impact

- **Yocto rebuild needed: no.** No camera-side recipe change. Pure
  Python + template + vendored JS. Standard server-image OTA path.
  Camera image unchanged.
- **OTA path:** the camera-side change ships in the next camera
  firmware OTA. On rollout:
  - Newly-flashed cameras render the QR on first-boot.
  - Already-paired cameras are unaffected (the wifi-setup server only
    runs pre-pairing; once `/data/.setup-done` is present, the wifi-
    setup server isn't started — see `lifecycle.py` and
    `is_setup_complete` in `wifi_setup.py:63-67`).
  - A factory-reset of an existing camera (re-running first-boot)
    will render the QR.
- **Hardware verification: required (low-risk).**
  - Smoke entry per AC-16.
- **Default state on upgrade:** no change for any existing paired
  camera. The QR is a first-boot-only UI element; its default state
  is "render whenever IP is known," controlled by the page's own JS,
  not by any new persisted config.
- **Disk-space impact on the camera image:** ~10 KB for the vendored
  library, ~2 KB for new HTML/JS in `setup.html`. Negligible against
  the camera image's tens-of-MB total.
- **CPU-time impact:** zero on the server side beyond one extra
  `nmcli device show` call per `/api/status` poll (the same call the
  existing post-pairing status server already makes). Client-side QR
  generation is a one-shot ~5 ms operation in the user's phone
  browser; not on the critical path.
- **Backwards compatibility:** the new `ip_address` field on
  `/api/status` is additive; clients that don't read it are
  unaffected. The QR slot is a new DOM element; existing JS paths
  that don't reference it are unaffected.
- **Rollback:** revert the Implementer's PR commits. No data
  migration needed (no new persisted state).

## Open Questions

(None of these are blocking; design proceeds. Implementer captures
answers in PR description.)

- **OQ-1: Vendored QR library choice.** Recommendation:
  `qrcode-generator` by Kazuhiko Arase (MIT, ~10 KB minified, no
  deps, documented API). Alternatives the Implementer may evaluate:
  `qrious` (~10 KB, MIT, canvas-only — slightly worse for the SVG
  decode path AC-9 prefers), `nayuki/qr-code-generator` JS port
  (~6 KB, MIT — even smaller). The constraints (single-file, ≤ 15 KB,
  MIT-or-similar, SVG output preferable) are pinned in AC-4.
  **Recommendation:** `qrcode-generator` for v1 unless Implementer
  identifies a smaller equivalent.
- **OQ-2: Test infrastructure for AC-9 (round-trip decode).** The
  repo has `playwright.config.ts` at root but no Playwright tests for
  camera. Two paths:
  - **(A)** Add a node-side decoder unit test
    (`app/camera/tests/unit/test_qr_render.py` + a small
    `qr_harness.js` invoked via `subprocess.run(["node", ...])`).
    Adds a `node` dependency on the test host; CI may already have
    one; if not, document as a test-prereq.
  - **(B)** Wire pytest-playwright. Adds `playwright` to
    `app/camera/requirements-test.txt`, runs headless. Heavier setup
    but matches the repo's existing E2E posture.
  **Recommendation:** (A) for v1 if `node` is already on CI; else
  (B). Implementer chooses based on CI capability check at
  implementation time.
- **OQ-3: Single shared QR slot vs two distinct slots
  (`#cam-address-qr-connecting` and `#cam-address-qr-result`).**
  Single shared element is fewer DOM nodes and simpler JS; two slots
  is more idiomatic for the existing view-switching pattern in
  `setup.html`. AC-11 only requires the QR appear in BOTH views with
  populated IP. Implementer's choice; both pass.
- **OQ-4: Should the QR slot include text "Or scan with your phone:"
  above it?** Adds copy that may already feel obvious to users
  familiar with QR codes. Existing setup.html copy is brief.
  Recommendation: omit for v1 (let the visual cue stand on its own);
  add only if Phase 2 hardware-smoke reveals user confusion.
- **OQ-5: Should the QR also encode the `.local` URL as an
  alternative payload (multi-payload QR)?** Stock phone scanners
  generally pick the first URL in a multi-payload QR; the user-value
  of encoding both is marginal (the `.local` is right there in plain
  text). Recommendation: single-payload IP-only QR for v1.

## Implementation Guardrails

(Constraints the Implementer must respect.)

- **Do NOT add `python3-qrcode` (or any Python QR library) to
  `app/camera/requirements.txt` or to the Yocto image manifest.**
  All QR generation is client-side. This is a hard constraint to
  preserve the no-Yocto-rebuild deployment path; deviating requires
  a follow-up architect cycle.
- **Do NOT add a new HTTP route to `WifiSetupServer`.** The library
  is inlined via template substitution. Adding a static-asset route
  to a stdlib `BaseHTTPRequestHandler` invites MIME-handling and
  path-traversal foot-guns disproportionate to the gain.
- **Do NOT modify `wifi.get_ip_address`** in `wifi.py`. The helper
  is correct as-is and is on the sensitive-paths watch list (it is
  the camera's own network-plane visibility primitive). Read-only
  usage from `wifi_setup.py` is sufficient.
- **Do NOT remove or restyle the existing `.local` text URL.** The
  QR is additive; the `.local` URL stays in its current position
  with its current copy. Removing it would regress the mDNS-works
  user path.
- **Do NOT encode pairing PIN, admin credentials, hostname, MAC,
  Camera-ID, or any session token in the QR.** Hard non-goal.
- **Do NOT cache the IP address.** `wifi.get_ip_address` runs
  per-request. Caching invites the stale-IP hazard (HAZ-201-1).
- **Do NOT widen the wifi-setup server's bind scope** to add the
  field on the post-pairing status server "for symmetry." The post-
  pairing server already exposes `ip_address` (sub-fix 1 of #90);
  re-emitting it on the wifi-setup server is the surgical change.
- **Do NOT add a new CSS file or modify `style.css`.** The camera
  has no external stylesheet today; all styling is inline in
  templates. Match the convention.
- **Do NOT bundle the QR library via a build step (webpack, rollup,
  esbuild) at deploy time.** The library is committed verbatim to
  the repo as a single file. Reproducible-build constraint.
- **Do NOT add SVG rendering using innerHTML without sanitisation if
  the chosen library outputs HTML strings.** The QR library's output
  is treated as trusted (the library is vendored and audited); the
  *input* (`https://<ip>:443`) is constrained to the SC-201-A shape.
  Implementer documents this trust boundary in a code comment on the
  render call.
- **Do NOT skip AC-14 (broken-library failure path).** It is the
  guardrail that prevents the QR-feature regression from breaking
  the entire setup flow on a future library upgrade.
- **Do flip the Phase-2 default-on if Phase-1 hardware smoke (AC-16)
  passes on at least two distinct router models** (one mDNS-friendly,
  one mDNS-blocking). Single-network smoke is insufficient evidence.
  This is a release-gate guardrail, not a code constraint.
