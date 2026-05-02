# ADR-0020: Dual-Transport OTA Updates

**Status:** Accepted
**Date:** 2026-04-18
**Deciders:** Vinu
**Relates to:** ADR-0008 (SWUpdate A/B rollback), ADR-0009 (mTLS pairing), ADR-0014 (signing), ADR-0015 (control channel)

## Context

ADR-0008 established the on-device **install layer**: SWUpdate performs an A/B partition swap, `post-update.sh` flips `boot_slot` against the live `/dev/monitor_standby`, U-Boot rolls back on bootlimit. This part is identical on server and camera — they share the same bundle format, the same `sw-description.*` template, and the same `post-update.sh`.

What was missing was a **transport layer**. The first OTA slice shipped with:

- `POST /api/v1/ota/server/upload` — direct browser upload to the server, verify, stage, install. Works.
- `POST /api/v1/ota/camera/<id>/push` — **stub**. It set `ota_status[cam_id] = "pending"` and logged an audit line. The bundle never left the server.
- No UI anywhere — admins had to curl the endpoints from a shell.

The user's practical ask is "I should be able to update both boxes from one screen." That means:

1. A single place in the web UI where an admin drops a `.swu` for the server or for any camera.
2. The server must be able to hand a bundle to a camera that has no public HTTP entry point (the camera's :443 is login-protected and unsuitable for 150 MB multipart uploads from a browser running on the admin's laptop crossing the WAN into the home LAN).
3. Install-side behaviour must be identical — a bundle installed by the camera must go through the exact same verify / preinst / write / postinst path whether the admin uploaded it to the camera directly or the server relayed it.

## Decision

**Separate the OTA pipeline into two layers with different scaling properties:**

```
┌─────────────── TRANSPORT (how a .swu reaches a device) ─────────────┐
│                                                                     │
│  Server:  browser → POST /api/v1/ota/server/upload → /data/ota/…   │
│  Camera:  browser → POST /api/v1/ota/camera/<id>/upload →          │
│             server /data/ota/inbox/camera-<id>/… →                  │
│             POST /api/v1/ota/camera/<id>/push →                     │
│             mTLS stream to https://<camera-ip>:8080/ota/upload     │
│  USB:     scan mounted USB → import → server inbox → (above)       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                 ↓
┌──────────────── INSTALL (identical on server and camera) ───────────┐
│                                                                     │
│   verify CMS signature (swupdate -c -k …pem)                       │
│   swupdate -i <bundle> → raw write to /dev/monitor_standby          │
│   post-update.sh preinst/postinst: compute standby from boot_slot,  │
│     carry network state, flip U-Boot env                            │
│   reboot → bootlimit rollback if new rootfs fails health            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Install layer is shared by contract, not by code.** Server's `OTAService` and camera's `OTAAgent` are independent implementations — the server has Flask + sqlite available, the camera is pure stdlib on a 512 MB box. What makes them "the same" is the contract: both run `swupdate -c` to verify, both run `swupdate -i` to install, both rely on the same `sw-description` + `post-update.sh` shipped inside the bundle. The bundle is the interface.

**Transport layer is new.** The camera already exposes an OTA endpoint at `https://<camera-ip>:8080/ota/upload` (mTLS, pairing CA). The server's `CameraOTAClient` reuses the pairing cert material (`server.crt` + `server.key`) that `CameraControlClient` uses for the control channel (ADR-0015), and streams the bundle straight from disk to the camera — never loading the full 150 MB into RAM on either side.

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/ota/status` | login | all devices, unified view |
| POST | `/api/v1/ota/server/upload` | admin+CSRF | multipart .swu for server |
| POST | `/api/v1/ota/server/install` | admin+CSRF | install staged server bundle |
| POST | `/api/v1/ota/camera/<id>/upload` | admin+CSRF | multipart .swu for camera |
| POST | `/api/v1/ota/camera/<id>/push` | admin+CSRF | async relay to camera, 202 |
| GET | `/api/v1/ota/camera/<id>/live-status` | login | proxy camera's own /ota/status |
| GET | `/api/v1/ota/usb/scan` | admin | find .swu on mounted USBs |
| POST | `/api/v1/ota/usb/import` | admin+CSRF | import USB bundle |

`push` returns 202 immediately and runs the actual upload on a background thread — a 150 MB bundle over 2.4 GHz WiFi is ~40 s, well past gunicorn's default worker timeout. The UI polls `/api/v1/ota/status` at 1.5 s while anything is in flight, 5 s when idle.

### UI

A new **Updates** tab in Settings, admin-only. One card for the server, one card per paired camera. Each card:

- Current firmware version.
- File picker (`accept=".swu"`). Chosen file uploads immediately to its device-specific inbox.
- **Install & Reboot** (server) or **Push & Install** (camera) button — enabled only when a bundle is staged and (camera only) the camera is online.
- Progress bar driven by the polled status, fed from the server-side shadow status during upload and from `/live-status` during verify/install.

No separate "choose a camera" step — the card **is** the device, matching the existing Settings pattern for Recording and Storage.

## Consequences

**Positive**

- A single place to operate all OTA. No `curl` in admin muscle memory.
- The camera's OTA agent needs no changes — the transport contract it advertised is now actually consumed.
- Bundle format, signing, install, rollback are shared by construction. A sig-verify fix lands in one `post-update.sh`; we don't have to keep two install engines in sync.
- mTLS from pairing is reused — no new secret to rotate, no new trust anchor.

**Negative**

- The push is only as reliable as the WiFi link between server and camera. A dropped TCP connection mid-stream fails the whole push; the admin must retry. (Industry pattern — SWUpdate on the camera is transactional via A/B so a half-arrived bundle is safely discarded.)
- Server disk carries a per-camera inbox under `/data/ota/inbox/camera-<id>/`. At ~150 MB per bundle times N cameras this is bounded by how many cameras an admin is staging simultaneously; inbox is cleared on successful push and on subsequent re-upload.
- The server briefly holds a second copy of the bundle (own staged server bundle + camera inbox bundle) during simultaneous server+camera updates. Acceptable on the 128 GB class hardware we target.

## Alternatives considered

**A. Add a file-upload form to the camera's own login page (:443).** Would give per-device direct upload without going through the server. Rejected for now: (i) camera's status_server is `BaseHTTPRequestHandler`, adding streamed multipart + session-auth + CSRF is non-trivial surgery; (ii) admin UX is worse — you'd have to navigate to each camera's IP separately; (iii) bundles from the server side are already signed and staged, relaying them over the existing mTLS channel is cheaper than re-uploading from the admin's laptop for every camera. We can add this later if a camera is ever orphaned from its server.

**B. Extract a shared `ota-core` Python package used by both server and camera.** Attractive on paper. Rejected: server and camera have different runtime constraints (Flask+sqlite vs pure-stdlib on 512 MB), and the actually-shared logic is three subprocess invocations (`swupdate -c`, `swupdate -i`, optional disk-space check). Sharing a package would add packaging and release coupling for ~60 lines of real overlap. The bundle contract is the right abstraction boundary.

**C. Peer-to-peer BitTorrent-style fan-out for multi-camera fleets.** Out of scope — this deployment is a home server with 1–4 cameras. Direct push is O(N) in cameras but N is tiny.

## Implementation notes

- `CameraOTAClient` (`app/server/monitor/services/camera_ota_client.py`) wraps `http.client.HTTPSConnection` with the server's mTLS context. It streams the bundle in 256 KiB chunks and invokes a `progress_cb` for UI polling.
- The push thread pattern mirrors how existing long-running jobs are handled. No task queue (celery, rq) — those would be overkill for 1–2 concurrent OTAs on a single-user home system.
- Status is stored in `OTAService._status` keyed by device id. The camera has its own authoritative OTA state in `status.json` under the spool; `GET /live-status` proxies it for the verify/install phases where only the camera knows what's happening.
- Audit events: `OTA_CAMERA_UPLOAD`, `OTA_CAMERA_PUSH`, `OTA_CAMERA_INSTALL_COMPLETE`, `OTA_CAMERA_INSTALL_FAILED` are added alongside the existing server events.

## Post-implementation amendments (2026-04-18)

The first implementation exposed four design issues that have since been
fixed. Recording them here so the ADR reflects what shipped, not the
original plan:

**1. Privilege separation on the camera.** The camera-streamer runs
as the `camera` user with `NoNewPrivileges=true`. SWUpdate needs root
(`/dev/monitor_standby` symlink refresh, ext4 mount of the standby
slot, `fw_setenv`), so `camera-streamer` cannot exec it directly.
Implemented as a file-IPC protocol: camera-streamer stages the bundle
at `/var/lib/camera-ota/staging/update.swu` and writes
`/var/lib/camera-ota/trigger`. A systemd `.path` unit
(`camera-ota-installer.path`) watches the trigger and fires the
root-owned `camera-ota-installer.service` oneshot, which is the only
place `swupdate -i` runs on the camera. Status and progress flow back
through `status.json` in the same spool. Alternative A from the
original ADR (camera-direct upload on :443) also uses this protocol —
the upload handler stages and triggers, then returns immediately.

**2. Async upload handshake.** The first OTAAgent implementation
blocked on `wait_for_completion()` before returning 200, keeping the
mTLS HTTPS connection open for the full 2–3 min install. On a Pi Zero
2W (362 MB RAM) the combination of `swupdate` (~250 MB RSS), the live
mTLS socket, and `camera-streamer` pushed the kernel past OOM: we
saw `camera-streamer`, `sshd`, and `getty` killed mid-install twice
during integration testing, leaving the device unreachable until a
physical power cycle. OTAAgent now returns `202 Accepted` as soon as
the trigger is written and the server polls `/ota/status` for
terminal state. Camera-direct upload on :443 uses the same async
shape. `CameraOTAClient.push_bundle()` on the server handles both
the upload phase and the polling phase, mapping camera install
progress into the second half of its status bar.

**3. Alternative A (camera-direct upload on :443) shipped.** The
original ADR rejected this on UX and complexity grounds. We shipped it
anyway because it turned out to be cheaper than expected once
`ota_installer.py` existed as a shared module — the :443 status
server just gets three new routes (`POST /api/ota/upload`,
`GET /api/ota/status`, `POST /api/ota/reboot`) that delegate to the
same stage/trigger/poll primitives. It covers a real failure mode:
an orphaned camera whose server has been factory-reset can still be
updated by an admin on the LAN with the camera's password.

**4. A `check_free_space` prerequisite.** SWUpdate stats the install
device *before* preinst can run. If `/dev/monitor_standby` is missing
it falls back to `/tmp` (a tmpfs) and rejects the install with a
bogus "not enough free space" error sized against tmpfs, not the real
2 GiB slot. Added `monitor-standby-symlink.service` (runs
`Before=sysinit.target` to create the symlink based on `boot_slot`)
and, as a belt-and-braces safeguard, the privileged installer itself
refreshes the symlink before invoking `swupdate` — so the protocol is
self-healing even if the boot service raced the `/boot` mount.

**Validation.** All three transports exercised on real hardware
(Pi 4B server + Pi Zero 2W camera): server GUI upload+install with
swupdate-check auto-confirmation, server→camera push without OOM
(camera stayed responsive throughout), camera-direct GUI upload with
trigger protocol firing the privileged installer. Pairing, heartbeat,
and streaming survived every reboot. See the CHANGELOG `Fixed`
entries for the seven bugs found during this validation.
