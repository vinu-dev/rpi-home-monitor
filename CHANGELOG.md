# Changelog

All notable changes to RPi Home Monitor are documented here.

## [Unreleased]

(Nothing yet — next release will land here.)

## [1.4.0] — 2026-04-25

Feature release. Multi-sensor camera support — any Pi-officially-supported camera sensor (OV5647 / IMX219 / IMX477 / IMX708) works out of the box on the camera image, and the per-camera dashboard Settings page renders dropdowns built from each camera's actual reported sensor capabilities. Plus single-source-of-truth release tooling. No breaking API changes.

### Added
- **Multi-sensor camera support** ([#173](https://github.com/vinu-dev/rpi-home-monitor/issues/173) — four PRs):
  - **Yocto + boot config** ([#174](https://github.com/vinu-dev/rpi-home-monitor/pull/174)) — switched the camera image from hardcoded `dtoverlay=ov5647` to firmware `camera_auto_detect=1`. Image now ships overlays for OV5647, IMX219, IMX477, IMX708. `app/camera/config/ensure-camera-overlay.sh` rewritten as an idempotent reconciler that heals stale `/boot/config.txt` lines on every boot — critical because SWUpdate's rootfs-only OTA can never overwrite the boot partition on existing field cameras. Optional `/data/config/camera-sensor` override pins a specific sensor when needed. 13-test pytest harness covers four fixtures and the four-sensor override path.
  - **Camera-side capability discovery** ([#175](https://github.com/vinu-dev/rpi-home-monitor/pull/175)) — new `camera_streamer/sensor_info.py` identifies the connected sensor via `Picamera2.global_camera_info()` (a static method that doesn't lock the camera) and looks up its modes in a hand-curated `KNOWN_SENSOR_MODES` table. `ControlHandler.get_capabilities()` returns the live sensor + modes; heartbeat embeds the same block. Generalised the OV5647-anchored hint strings across `capture.py`, `lifecycle.py`, `faults.py`, `motion_runner.py`, `motion.py`. 27 new unit tests; 404 unit tests pass overall.
  - **Server-side capability persistence** ([#176](https://github.com/vinu-dev/rpi-home-monitor/pull/176)) — `Camera` dataclass gains `sensor_model`, `sensor_modes`, `sensor_detection_method`. `accept_heartbeat` parses the new `capabilities` block defensively (per-entry rejection of malformed modes, length cap, no clobber on garbage). `_validate_update` derives valid `(width, height)` and per-resolution max fps from the live sensor — IMX219 accepts 3280×2464 and 47 fps@1080p; OV5647 rejects both with a useful message. Pre-#175 firmware leaves the record untouched (clean fallback). 11 new tests.
  - **Dashboard per-camera Settings dropdown** ([#177](https://github.com/vinu-dev/rpi-home-monitor/pull/177)) — replaced the hardcoded `_resMaxFps` map and three-option `<select>` with Alpine `<template x-for>` rendering each camera's `sensor_modes`. New "Sensor" row above the dropdown shows the live model + mode count. Mismatch banner fires when the camera's saved resolution is no longer in its current sensor's mode list (sensor swap), auto-clamps to the closest supported mode, and prompts the user to review + save. Backward-compatible with cameras still on pre-#175 firmware.

- **Single-source-of-truth release tooling** ([#178](https://github.com/vinu-dev/rpi-home-monitor/pull/178)) — new `VERSION` file at the repo root is consumed by Yocto's `DISTRO_VERSION` and `scripts/build-swu.sh`'s fallback. `scripts/release.sh` is a five-subcommand entry point (prepare/tag/build/verify/publish) for the whole release flow. `scripts/check_version_consistency.py` runs in CI's Repo Governance job and catches drift before it ships. `RELEASE.md` documents the policy and flow.

### Changed
- **OV5647-specific hint strings generalised** across `capture.py`, `lifecycle.py`, `faults.py`, `motion_runner.py`, `motion.py` — now reference all four supported sensors instead of OV5647 only. The detection-error log lines point at `dmesg | grep -iE 'imx219|ov5647|imx477|imx708'` instead of `lsmod | grep ov5647`.

### Fixed
- **PR [#92](https://github.com/vinu-dev/rpi-home-monitor/pull/92)'s incidental partial IMX219 add** is now complete. The IMX219 driver and overlay shipped in the image but no path activated them, so an IMX219 camera reported "camera missing" with a `Picamera2() → IndexError` loop in the journal. End-to-end fix landed across the four PRs above; verified in-session on three live cameras (one pre-deployment with the manual hand-patch on `.186` IMX219, `.115` IMX219, `.148` OV5647 ZeroCam — confirmed firmware auto-detect works for both EEPROM-carrying and EEPROM-less boards).

### Known follow-up
- **LUKS post-pair migration** (issue [#101](https://github.com/vinu-dev/rpi-home-monitor/issues/101) + ADR-0010) still deferred from 1.3.1. `/data` ships raw ext4. Stolen SD card exposes recordings + WiFi credentials + admin hash. Targeted for 1.4.1 with hardware validation, not bundled into 1.4.0 because the safe-shipping mitigations (feature-flag gating, atomic snapshot+rollback, container-loopback test wiring, post-OTA initramfs propagation, dropbear/Plymouth unlock for the server) are larger than the rest of this release combined and cannot be hardware-validated without risk of dev-camera data loss.

## [1.3.1] — 2026-04-22

Patch release. Fixes the prod first-boot hotspot chain, cameraless
Pi Zero 2W detection, a UX cluster around hardware faults, and
adds a brand mark. No breaking API changes.

### Added
- **Unified fault framework** (ADR-0023, [#158](https://github.com/vinu-dev/rpi-home-monitor/pull/158)) — structured `hardware_faults: [{code, severity, message, hint, context}]` on every heartbeat. Dashboard renders one compact severity-colored chip per active fault next to the ONLINE pill; hover reveals the actionable hint. Codes this release: `camera_sensor_missing`, `camera_h264_unsupported`. Flat v1.3.0 `hardware_ok`/`hardware_error` kept for back-compat.
- **Brand mark** ([bc9d6a5](https://github.com/vinu-dev/rpi-home-monitor/commit/bc9d6a5)) — new house-with-camera-aperture SVG logo in coral→violet gradient. Replaces the generic DSLR silhouette in the nav; same asset drives the favicon. Monochrome + favicon variants shipped.
- **Edit camera name + location** ([#157](https://github.com/vinu-dev/rpi-home-monitor/pull/157), [#156](https://github.com/vinu-dev/rpi-home-monitor/issues/156)) — Camera Settings modal now exposes name + location at the top. After pairing, operators can rename "Camera 1" → "Front Door" without an SSH round-trip.
- **README hero image** ([c505cd3](https://github.com/vinu-dev/rpi-home-monitor/commit/c505cd3)) — marketing banner at the top of the repo landing page.

### Fixed
- **Prod first-boot hotspot never came up** ([#153](https://github.com/vinu-dev/rpi-home-monitor/pull/153), [#155](https://github.com/vinu-dev/rpi-home-monitor/pull/155)) — two stacked bugs on prod images: `luks-first-boot.sh` never shipped in the rootfs (LUKS_ENABLED scoping bug) and the wks left `/data` raw. Fixed by formatting `/data` as ext4 at image-build time in `home-{camera,monitor}-ab-luks.wks`; LUKS migration now sequences post-pairing in a follow-up slice.
- **Cameraless Pi Zero 2W reported hardware_ok=true** ([#158](https://github.com/vinu-dev/rpi-home-monitor/pull/158)) — `/dev/video10-31` exist as libcamera subdevices regardless of sensor presence, and `dtoverlay=ov5647` registers `/dev/video14` as a Video Capture node even with the ribbon cable unplugged. `CaptureManager.check()` now chains `v4l2-ctl` Device Caps (`Video Capture` only) with `libcamera-hello --list-cameras` as the authoritative probe.
- **"All systems normal — 3/3 online"** shown even when paired cameras had no sensor attached. Tier-1 summary now counts active faults into the state: amber for warning/error, red for critical.
- **LED was dark during LUKS first-boot** ([#153](https://github.com/vinu-dev/rpi-home-monitor/pull/153)) — added a fast-blink (200 ms) LED state during cryptsetup so operators know the device is working.

### Changed
- **Build checksum aligned** ([#151](https://github.com/vinu-dev/rpi-home-monitor/pull/151)) — `LIC_FILES_CHKSUM` for AGPL-3.0-only now matches poky scarthgap's bundled file (`73f1eb20517c…`). Previous `eb1e6478…` was the upstream/FSF copy and failed `populate_lic_qa` on build.
- **python3-videodev2 recipe** ([#152](https://github.com/vinu-dev/rpi-home-monitor/pull/152)) — declares `LIC_FILES_CHKSUM` so scarthgap's `do_populate_lic` QA stops failing the camera-prod build.
- **E2E regression spec** ([#149](https://github.com/vinu-dev/rpi-home-monitor/pull/149)) — `waitForURL(/dashboard/)` before navigating onward (login was racing the redirect); stale "Device Info / Connection" selectors replaced with `nav[aria-label="Page sections"]`-backed role queries. Yocto Runtime job now installs pytest.

### Known follow-up
- **LUKS-post-pair migration** — re-key `/data` to LUKS on the next boot after pairing completes (reopens the security posture temporarily relaxed by the wks fix). Tracked as ADR follow-up.
- **/faults page + mute endpoint** — deferred per ADR-0023 §Scope.

## [1.3.0] — 2026-04-21

Feature release: camera-side motion detection, admin password reset,
expanded recordings + storage controls, control-channel mTLS
hardening, and a mobile-UI polish pass. No breaking API changes.

### Added
- **Camera-side motion detection + motion recording mode** (ADR-0021, [#92](https://github.com/vinu-dev/rpi-home-monitor/pull/92)) — new on-device motion pipeline. Recording modes are now `continuous` / `motion` / `off`; motion mode writes clips only around detected events (with a configurable pre-roll + sensitivity). Exposed per-camera in the dashboard Stream Settings modal.
- **Dashboard camera details panel + motion toggle** ([#124](https://github.com/vinu-dev/rpi-home-monitor/pull/124), [#110](https://github.com/vinu-dev/rpi-home-monitor/issues/110), [#106](https://github.com/vinu-dev/rpi-home-monitor/issues/106)) — collapsible `<details>` per paired-camera card showing recording mode, firmware, bitrate/profile/keyframe, uptime, and admin-only Health rows. Motion detection checkbox + sensitivity slider wired into the stream-settings modal. New `DELETE /api/v1/recordings` endpoint with a two-step Danger Zone UI to wipe every clip across every camera.
- **Admin password reset — slice 1** (ADR-0022, [#103](https://github.com/vinu-dev/rpi-home-monitor/pull/103)) — admins can force-reset any user's password from Settings → Users with a strict-mode confirmation dialog. Sole-admin safety rail prevents demoting/clearing the only admin. Reset-token self-service flow deferred to a later slice.
- **USB reformat for all device states** ([#125](https://github.com/vinu-dev/rpi-home-monitor/pull/125), [#107](https://github.com/vinu-dev/rpi-home-monitor/issues/107)) — Format button previously appeared only for unsupported filesystems. Now: Reformat available for supported-but-not-in-use drives and for the currently-active drive (client auto-ejects before the format request). Dialog copy adapts per state.
- **Recordings Tier-2 / Tier-3 endpoints** — `GET /api/v1/recordings/latest` (newest clip across every camera) and `GET /api/v1/recordings/recent?limit=N` (most-recent N), used by dashboard surfaces.

### Changed
- **Control-channel mTLS tightened** (ADR-0015, [#122](https://github.com/vinu-dev/rpi-home-monitor/pull/122), partial [#119](https://github.com/vinu-dev/rpi-home-monitor/issues/119)) — camera status server now loads the pairing CA (`ssl.CERT_OPTIONAL` + `load_verify_locations(ca.crt)`), and `_has_mtls_client_cert` accepts only validated peer certs. The source-IP fallback was removed so an attacker on the same LAN can't impersonate the server. Pre-pairing, the listener still accepts CERT_NONE with a warning until the CA lands.
- **Yocto license labels aligned to AGPL** ([#122](https://github.com/vinu-dev/rpi-home-monitor/pull/122), [#120](https://github.com/vinu-dev/rpi-home-monitor/issues/120)) — `monitor-server_1.0.bb` and `camera-streamer_1.0.bb` now declare `LICENSE = "AGPL-3.0-only"` with the upstream `COMMON_LICENSE_DIR` checksum.
- **Mobile Recordings layout + date-picker width** ([#123](https://github.com/vinu-dev/rpi-home-monitor/pull/123), [#104](https://github.com/vinu-dev/rpi-home-monitor/issues/104), [#105](https://github.com/vinu-dev/rpi-home-monitor/issues/105)) — Recordings player capped at 38vh on narrow viewports so the list remains reachable. Date / datetime-local inputs get a sensible min/max width. Clip-card checkbox hit area expanded to 44×44 per WCAG 2.5.5 while the visual remains 18px.
- **Camera Status page tap-target polish** ([#123](https://github.com/vinu-dev/rpi-home-monitor/pull/123), [#109](https://github.com/vinu-dev/rpi-home-monitor/issues/109)) — TOC links padded to ≥44px with `touch-action: manipulation` to kill the iOS 300ms tap delay.
- **Live View height cap** ([#98](https://github.com/vinu-dev/rpi-home-monitor/pull/98), [#97](https://github.com/vinu-dev/rpi-home-monitor/pull/97)) — player capped at 70vh with player controls; fits laptop viewports without scrolling.
- **Dashboard "N recent system events" strip** ([#102](https://github.com/vinu-dev/rpi-home-monitor/pull/102)) — now clears when the user visits `/logs` instead of hanging around until page reload.
- **Server-deploy script now ships `reset-admin-password.py`** ([#108](https://github.com/vinu-dev/rpi-home-monitor/pull/108)) under `/opt/monitor/scripts/` so on-device recovery uses the same code the CI tests exercise.
- **Release planning + local-first roadmap docs landed** (`docs/roadmap-next-2-releases.md`, `docs/specs/r1-*.md`).

### Fixed
- **Motion-mode recordings — clip spawning race** ([#94](https://github.com/vinu-dev/rpi-home-monitor/pull/94)) — scheduler now wakes on motion event + correlator accepts clips that start just after the event timestamp.
- **Fresh-camera stream-state default** (ADR-0017, [#126](https://github.com/vinu-dev/rpi-home-monitor/pull/126), [#115](https://github.com/vinu-dev/rpi-home-monitor/issues/115)) — `lifecycle._read_desired_stream_state` defaulted to `running` for a missing state file, drifting from `control.py`'s `stopped`. Aligned to `stopped` per ADR-0017 on-demand; regression test locks parity with ControlHandler.
- **Safety-rail AttributeError on admin password reset** ([#122](https://github.com/vinu-dev/rpi-home-monitor/pull/122), [#117](https://github.com/vinu-dev/rpi-home-monitor/issues/117)) — `UserService` called the non-existent `self._store.list_users()`; renamed to `get_users()` with a regression test that instantiates a real `Store` (not a MagicMock) so future renames fail loudly.
- **Nightly Validation** ([#149](https://github.com/vinu-dev/rpi-home-monitor/pull/149)) — 5+ consecutive red runs. Fixed the Browser E2E Full login race (waitForURL before navigating onward), refreshed stale camera-status section labels, and installed `pytest` in the Yocto Runtime job.
- **Mobile UI + storage triage** ([#93](https://github.com/vinu-dev/rpi-home-monitor/pull/93)) — covers [#86](https://github.com/vinu-dev/rpi-home-monitor/issues/86), [#87](https://github.com/vinu-dev/rpi-home-monitor/issues/87), [#88](https://github.com/vinu-dev/rpi-home-monitor/issues/88), [#89](https://github.com/vinu-dev/rpi-home-monitor/issues/89); confirms [#91](https://github.com/vinu-dev/rpi-home-monitor/issues/91); partial [#90](https://github.com/vinu-dev/rpi-home-monitor/issues/90).

### Security
- ADR-0022 ("no backdoors") codified. The earlier sudo-only CLI admin-recovery script was removed end-to-end in [#111](https://github.com/vinu-dev/rpi-home-monitor/pull/111); sole-admin lockout is now a hardware-only recovery path. Every auth/recovery PR in this release cites ADR-0022.
- Camera control-channel source-IP fallback removed (see Changed above).

### Known follow-up
- Server→camera direction still uses `ssl.CERT_NONE` because the camera's status-server cert is self-signed; closing this needs an ADR picking CA-signed-at-pairing vs pinned fingerprint. Tracked in [#119](https://github.com/vinu-dev/rpi-home-monitor/issues/119).
- Self-service forgot-password (reset-token) flow deferred to a future slice of [#99](https://github.com/vinu-dev/rpi-home-monitor/issues/99).
- Secrets-at-rest ([#101](https://github.com/vinu-dev/rpi-home-monitor/issues/101)) needs its own ADR before implementation.

## [1.2.1] — 2026-04-19

Quality-and-polish patch. No API changes, no migration needed.

### Fixed
- **Dashboard Recent events too long** ([#82](https://github.com/vinu-dev/rpi-home-monitor/pull/82)) — the feed rendered 8 rows, filling the viewport and making the dashboard feel like a recordings page in miniature. Dropped to 3 rows; header "All recordings →" link is the path to the full timeline.
- **Dashboard Recent activity silently empty** ([#82](https://github.com/vinu-dev/rpi-home-monitor/pull/82)) — the x-show gate `auditAdmin && auditEvents.length > 0` collapsed the section during the render tick when `auditEvents` was an empty array (initial state before the async fetch resolved). Gated on `auditAdmin` alone now, with an explicit "No recent activity yet." empty state when the list is genuinely empty.
- **WHEP proxy `AttributeError` on headerless `HTTPError`** ([#81](https://github.com/vinu-dev/rpi-home-monitor/pull/81)) — `api/webrtc.py` used `hasattr(e, "headers")` which returns True even when `e.headers is None`. Switched to `if e.headers is not None`.
- **`subprocess` import in `api/ota.py`** ([#81](https://github.com/vinu-dev/rpi-home-monitor/pull/81)) — architecture fitness tests flagged the API layer calling `subprocess.run` directly. Moved reboot scheduling to `OTAService.schedule_reboot()`; same delay, same thread name, same behaviour.

### Added
- **World-class test suite** ([#81](https://github.com/vinu-dev/rpi-home-monitor/pull/81)) — 1585 server tests + 555 camera tests (was ~1280 server). 86 → 87.8% server coverage. Adds architecture fitness tests (AST rules for CSRF on mutating routes, M2M HMAC, Store-import layering), property-based tests via Hypothesis for auth + crypto, Playwright regression journeys, and mutation testing (gated behind `vars.RUN_SERVER_MUTATION`).
- **Unified `logged_in_client` fixture** ([#81](https://github.com/vinu-dev/rpi-home-monitor/pull/81)) — replaces 13 duplicate `_login` helpers across the integration suite.

### Known follow-up
- Schemathesis fuzzing exposed ~33 pre-existing OpenAPI-vs-implementation gaps in the API contract. Reverted fuzzing to `examples` phase for v1.2.1; full fix tracked for a later contract-hardening PR.
- `browser-e2e-full` job pulled — journey specs written without a live seeded server had guessed selectors. Smoke project still runs.

## [1.2.0] — 2026-04-19

First commercial release. Bundles the OTA production-hardening work
(signed bundles, dual-transport install, dashboard performance) with
a round of release-readiness security fixes.

### Added
- **Production Yocto build targets** — `scripts/build.sh server-prod` and `camera-prod` now consume `config/<board>/local.conf.prod` which `require`s the dev config and flips `SWUPDATE_SIGNING = "1"` (see ADR-0014). Dev paths unchanged.
- **Pre-upload `.swu` inspection in both GUIs** — browser reads the CPIO header + sw-description text before sending, rejecting unsigned bundles and cross-target bundles (server .swu dropped on the camera card and vice versa) at selection time.
- **`/etc/sw-versions` stamped at install time** — `post-update.sh` parses the bundle version and writes it into the new slot so the "Current version" UI line reflects what's running, instead of the Yocto-baked `1.0.0`.
- **`must_change_password` enforced server-side** — flagged sessions get `403 must_change_password: true` from every protected endpoint; allow-list covers password-change, logout, and `/me`.
- **`requirements.lock`** — exact-version pin file for deterministic server installs (Flask 3.1.3, bcrypt 5.0.0, Jinja2 3.1.6, zeroconf 0.148.0 + transitive pins).
- **USB "In use" state** — active backing device shows the badge + eject hint instead of a clickable Use button.
- **`build-swu.sh` post-substitution check** — aborts if any `@@PLACEHOLDER@@` markers survived sed substitution.
- **Recordings page supports flat-layout clips** — loop-recorder clips (`<cam>/YYYYMMDD_HHMMSS.mp4`) now listable via `get_dates_with_clips`, `list_clips`, `get_clip_path`.
- **Signed-OTA validation record** — `docs/exec-plans/ota-signing-validation-2026-04-19.md` captures the 6/6 on-hardware tests.

### Changed
- **OTA bundle staging is atomic** — `shutil.move` swapped for `os.replace` via a per-request temp path. Concurrent uploads against the same filename no longer risk corruption.
- **`SECRET_KEY` persistence fails loudly** — if `$CONFIG_DIR/.secret_key` can't be written we raise `RuntimeError` rather than returning an ephemeral key that rotates on restart.
- **Dashboard tab-switching is instant again** — `<video>` elements in Recent Events only materialise after Play click (`x-if`, not `x-show`); `latest_across_cameras` / `recent_across_cameras` cache `rglob` results for 20 s.
- **Retention estimate cached 5 min** — `_estimate_retention_days` was walking `/data/recordings` every 10 s from the dashboard poll.

### Fixed
- `must_change_password` API-bypass (client-side flag only).
- `SECRET_KEY` silent reset on write failure.
- `shutil.move` staging race under concurrent OTA uploads.
- `build-swu.sh` shipping unresolved `@@NAME@@` placeholders on drift.
- Recordings tab empty when only flat-layout clips exist.
- Dashboard → live navigation stall (3-minute delay in field testing).
- USB Storage "Use" button clickable for active device.

### Security
- CMS/PKCS7 signed `.swu` bundles accepted by server + camera installers (ADR-0014). Unsigned + tampered bundles rejected before any write.
- `/etc/swupdate-enforce` marker + `-k cert` in `swupdate -c` give dual-defense; missing cert on a signing-enforced image is a hard fail.

## [1.1.0] — 2026-04-13

### Added
- **Dual-transport OTA, end-to-end validated** (ADR-0020) — three install paths now work on hardware:
  - Server self-upload + install via admin Settings → Updates tab.
  - Server → camera push via admin Cameras tab (uploads bundle to server inbox, relays over mTLS to the camera's :8080 OTAAgent).
  - Camera-direct upload via the camera's own status page (:443 Updates section) for admins on the LAN with the camera's password.
- **Privilege-separated camera installer** — a root-owned `camera-ota-installer.service` activated by a systemd `.path` unit watching `/var/lib/camera-ota/trigger`. Keeps `camera-streamer` unprivileged (`NoNewPrivileges=true`) while still letting OTA writes to `/dev/mmcblk0p2/3`, `fw_setenv`, and `/dev` symlinks happen with the permissions SWUpdate needs.
- **Shared installer module** `camera_streamer.ota_installer` consumed by both transports (`ota_agent.py` on :8080 and the new :443 status-server routes). Single source of truth for stage/trigger/poll. 15 unit tests + integration coverage.
- **Camera Updates UI** — collapsible section on the camera status page with file picker, upload progress, install-state display, and Reboot button. Streams directly to `/api/ota/upload` — the handler never buffers the 128 MB bundle in RAM.
- **Boot-time `/dev/monitor_standby` service** — creates the symlink against the current `boot_slot` at `Before=sysinit.target`, so SWUpdate's `check_free_space` sees the real 2 GiB partition instead of tmpfs and stops rejecting valid installs.
- **mDNS server discovery** — Server advertises itself as `homemonitor.local` via Avahi. Cameras auto-discover the server without needing a manual IP address. Camera setup page defaults to `homemonitor.local`.
- **Captive portal provisioning** — Both server and camera trigger the phone's "Sign in to network" popup on hotspot connect. Supports iOS, Android, Windows, Firefox, and Samsung captive portal detection. Manual fallback at `http://10.42.0.1` always works.
- **LED status feedback** — Onboard ACT LED shows device state:
  - Slow blink (1s) = setup mode, waiting for WiFi config
  - Fast blink (200ms) = connecting to WiFi
  - Very fast blink (100ms) = error, connection failed
  - Solid on = running normally
  - Off = service stopped
- **WiFi rescan button** — Camera setup page can re-scan for networks (briefly drops hotspot).
- **Avahi service file** for server — advertises `_homemonitor._tcp`, `_https._tcp`, and `_http._tcp` services.
- **First-boot hostname** — Server hostname set to `homemonitor` on first boot for mDNS reachability.

### Fixed
- **OTA: camera `sw-description` hardware key** — was `raspberrypi0-2w-64` but `/etc/hwrevision` on the camera image is `home-monitor-camera`. Mismatch caused `swupdate -c` to reject every camera bundle.
- **OTA: post-boot health check probed auth-protected URLs** — `swupdate-check.sh` used `wget` against `/api/v1/ota/status` on the server and a wrong port on the camera. Both returned 401/404, which `wget` treats as failure — so upgrade_available stayed at 1 and U-Boot eventually rolled back a working install. Replaced with a `curl`-based `http_alive()` that accepts any HTTP response (any status ≠ 000 proves the port bound), with 12×5s retry to cover Type=simple service startup race, and pointed the camera probe at the status server on :443.
- **OTA: `swupdate-check.service` stuck behind `network-online.target`** — the Yocto image ships `systemd-networkd-wait-online` enabled, but WiFi is managed by NetworkManager, so networkd has no interfaces and times out, never letting `network-online.target` reach ready. Changed the unit to `After=network.target` (localhost is enough) and bumped `TimeoutStartSec` to 180 s.
- **OTA: camera `ProtectSystem=strict` blocked the spool directory** — `camera-streamer` only had `ReadWritePaths=/data`, but the OTA spool lives at `/var/lib/camera-ota` by design (shared with the privileged installer via tmpfiles.d). Streamer got EROFS staging uploaded bundles. Added the spool dir to the unit's writable paths.
- **OTA: server-push OOM-killed the camera** — the first OTAAgent implementation held the mTLS HTTPS connection open while `swupdate` wrote 1.8 GB to the SD card. On the Pi Zero 2W (362 MB RAM) this pushed the kernel past OOM — camera-streamer, sshd, and getty were all killed and the box needed a physical power cycle. Made the upload handler return 202 Accepted as soon as the trigger file is written; the server polls `/ota/status` for progress. Matches the camera-direct GUI flow that was already stable.
- **OTA: server install picked a stale staged bundle** — `install_server_image()` used `os.listdir()[0]`, which returns filesystem order on Linux. A leftover bundle from a prior aborted install (`server-update-1.1.0-feat-ota-dual.swu`) beat the freshly uploaded one lexicographically (`'1' < 'd'`), so the user's upload was silently ignored and an old version installed. Now picks the newest `.swu` by mtime.
- **OTA: `/dev/monitor_standby` symlink missing on some boots** — the boot-time service runs `Before=sysinit.target` when `/boot` (home of `u-boot.env`) isn't reliably mounted, so `fw_printenv boot_slot` sometimes returned the wrong slot and the symlink pointed at the running partition, tripping SWUpdate's `check_free_space` on the next install. The privileged installer now refreshes the symlink itself before invoking swupdate — no longer relies on the boot service being timely.
- **Hotspot startup race condition** — `nmcli connection up` was called before wlan0 was ready at boot, causing "No suitable device found" error. Now waits for WiFi interface readiness (up to 30s) and retries activation (5 attempts). Explicit `ifname wlan0` passed to prevent NM from trying eth0.
- **NGINX HTTP redirect loop** — Setup wizard was inaccessible because HTTP 80 redirected to HTTPS 443, but TLS certs don't exist during first boot. HTTP now serves directly.
- **Camera WiFi scan** — Scan button now triggers a real WiFi rescan instead of showing cached results.

### Changed
- Server and camera systemd services now depend on `sys-subsystem-net-devices-wlan0.device` to ensure WiFi hardware is ready.
- Server hotspot service has `TimeoutStartSec=90` to allow for WiFi retry loop.
- Camera setup page server address field defaults to `homemonitor.local` instead of empty.

## [v1.0.6-dev] — 2026-04-10

### Added
- **Camera password authentication** — Camera status page now requires login with username/password set during provisioning. PBKDF2-SHA256 hashing (100k iterations, random 16-byte salt). Session-based auth with HttpOnly cookies and 2-hour timeout.
- **Camera setup collects credentials** — First-boot wizard now asks for admin username and password.
- **Camera `.local` URL access** — Cameras are reachable via mDNS at `http://rpi-divinu-cam-XXXX.local` (XXXX = last 4 hex of CPU serial).
- **Camera system health display** — Status page shows CPU temperature, memory usage, and uptime with color-coded thresholds.
- **Camera WiFi change** — Authenticated users can change WiFi network and password from the status page.
- **Camera password change** — Authenticated users can change the camera admin password.
- **25 new tests** — 8 for password management, 17 for session management, provisioning, and system helpers.

### Fixed
- **Server settings WiFi card hidden** — Race condition where `auth.getMe()` async call hadn't completed before settings `init()` checked user role.
- **Server settings uptime `[object Object]`** — API returns `{seconds, display}` object; JS was displaying the raw object.
- **Server settings disk "0 B"** — API returns `disk.total_gb`; JS was using `data.disk.total` (undefined).

### Changed
- **Camera templates extracted** — Inline HTML moved from `wifi_setup.py` to separate template files in `templates/`.
- Camera unique hostname set during first boot via CPU serial suffix for multi-device mDNS support.

---

## Setup Guide

### Part 1: Server Setup (RPi 4B)

1. **Power on** — Insert SD card, plug in power, wait ~60 seconds. LED starts slow blinking (setup mode).
2. **Connect to hotspot** — On your phone, connect to WiFi `HomeMonitor-Setup` (password: `homemonitor`).
3. **Setup wizard opens automatically** — Your phone should show a "Sign in to network" popup. If not, open `http://10.42.0.1` in a browser.
4. **Configure WiFi** — Select your home WiFi network, enter password, hit Connect.
5. **Set admin password** — Change the default admin password (minimum 8 characters).
6. **Complete setup** — Hit Complete. The server stops the hotspot and joins your home WiFi. LED goes solid (connected). You will lose connection to the hotspot — this is normal.
7. **Reconnect** — Connect your phone back to your home WiFi.
8. **Open dashboard** — Go to `https://homemonitor.local` (accept the self-signed cert warning). If `.local` doesn't resolve on your network, find the server IP from your router's DHCP table.
9. **Log in** — Username: `admin`, Password: what you set in step 5.

### Part 2: Camera Setup (RPi Zero 2W)

1. **Attach camera** — Connect PiHut ZeroCam ribbon cable (blue side faces the board).
2. **Power on** — Insert SD card, plug in power, wait ~90 seconds (Zero 2W is slower). LED starts slow blinking (setup mode).
3. **Connect to hotspot** — On your phone, connect to WiFi `HomeCam-Setup` (password: `homecamera`).
4. **Setup wizard opens automatically** — Your phone shows the "Sign in to network" popup. If not, open `http://10.42.0.1` in a browser.
5. **Configure WiFi** — Select your home WiFi network, enter password.
6. **Server address** — Leave as `rpi-divinu.local` (auto-discovery). Only change this if mDNS doesn't work on your network — in that case enter the server's IP address. Port: leave as `8554`.
7. **Set camera login** — Choose a username (default: `admin`) and password (min 4 characters). You'll need these to access the camera's settings page later.
8. **Save & Connect** — LED switches to fast blink (connecting), then solid on (connected). The hotspot disappears. A `.local` URL is shown (e.g., `http://rpi-divinu-cam-d8ee.local`) — bookmark it for future access. If connection fails, LED blinks rapidly and the hotspot restarts automatically for retry.

### Part 3: Pair Camera on Server

1. **Reconnect** — Connect your phone back to your home WiFi.
2. **Open dashboard** — Go to `https://homemonitor.local` and log in.
3. **Confirm camera** — The camera appears as "pending" on the Dashboard (wait up to 30 seconds, refresh if needed). Click it and hit Confirm. Give it a name and location.
4. **Streaming starts** — HLS live view + 3-minute MP4 clips begin recording automatically.

### LED Quick Reference

| LED Pattern | Server | Camera |
|-------------|--------|--------|
| Slow blink (1s on/off) | Setup mode — hotspot active, waiting for WiFi config | Same |
| Fast blink (200ms) | — | Connecting to WiFi |
| Very fast blink (100ms) | — | WiFi connection failed, retrying |
| Solid on | Running normally | Running normally, streaming to server |
| Off | Service stopped | Service stopped |

### Troubleshooting

| Problem | Solution |
|---------|----------|
| Captive portal doesn't pop up | Open `http://10.42.0.1` manually in your browser |
| `homemonitor.local` doesn't resolve | Use the server's IP address from your router's DHCP table instead |
| Camera can't find server | Enter the server IP manually instead of `homemonitor.local` during camera setup |
| Hotspot doesn't appear | Wait 60-90 seconds after power on. Check LED — slow blink means hotspot is active |
| LED stays off after boot | Service may have failed. Connect via SSH and check `journalctl -u monitor-hotspot` (server) or `journalctl -u camera-streamer` (camera) |
