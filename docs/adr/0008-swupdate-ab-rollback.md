# ADR-0008: SWUpdate with A/B Rollback and Multi-Mode Delivery

## Status
Proposed

## Context
The partition layout (WKS files) provisions A/B root partitions on both server and camera, but no update engine, image format, delivery pipeline, or rollback mechanism is implemented. The OTA API (`ota.py`) stages `.swu` files and tracks status, but the install path is stubbed. The camera `ota_agent.py` is a placeholder.

### Hardware (measured 2026-04-11)

| | Server (RPi 4B) | Camera (Zero 2W) |
|---|-----------------|-------------------|
| RAM | 8 GB (7.7 GB avail) | 512 MB (183 MB avail, 256 MB CMA) |
| SD card | 64 GB | 64 GB |
| rootfs used | 436 MB | 296 MB |
| CPU | Cortex-A72 quad | Cortex-A53 quad |
| Bootloader | RPi firmware (no U-Boot yet) | Same |
| SWUpdate | Not installed | Not installed |
| AES-256-CBC | ~30 MB/s (no hw accel) | ~33 MB/s |
| ChaCha20-Poly1305 | ~268 MB/s | ~165 MB/s |

Both boards boot via RPi firmware (`start.elf` → `kernel8.img`). Neither has U-Boot or SWUpdate installed. `cryptsetup` is present on both.

### Partition layout (updated in this ADR)

Both devices use 64 GB SD cards with the following layout:

| Partition | Size | Filesystem | Purpose |
|-----------|------|------------|---------|
| boot | 512 MB | vfat | U-Boot, kernel, DTBs, config.txt, U-Boot env |
| rootfsA | 8 GB | ext4 | Active root filesystem |
| rootfsB | 8 GB | ext4 | Standby (OTA target) |
| data | ~47 GB (`--grow`) | ext4 (LUKS in prod) | Recordings, config, certs, logs, updates |

Rootfs images are built at content size (~600 MB server, ~400 MB camera) and expanded via `resize2fs` on first boot. OTA `.swu` bundles contain the compact image, not the full 8 GB.

### Design goals
1. An update engine that writes to the inactive partition without touching the running system.
2. A signed image format the build system can produce.
3. A rollback mechanism using U-Boot boot counting.
4. Multiple delivery modes through one verification pipeline.
5. Two artifact types: full-system (A/B rootfs) and app-only (Python app).
6. A camera-side agent that receives pushes from the server.

Coupled with ADR-0009 (mTLS — OTA push uses the paired TLS channel) and ADR-0010 (LUKS — data partition survives updates, unlockable by both rootfs slots).

## Decision

Use **SWUpdate** as the update engine on both devices, with **U-Boot** for A/B boot selection and automatic rollback. Support **multiple delivery modes** through a **single signed verification and install pipeline**. Support **two artifact types**: full-system A/B rootfs updates and app-only symlink-swap updates.

---

### 1. U-Boot boot chain

Switch both boards from direct RPi firmware kernel boot to U-Boot.

**Boot flow:**
```
RPi GPU firmware (start.elf)
  → applies config.txt, device tree overlays (camera dtoverlay=imx219, etc.)
  → loads u-boot.bin (instead of kernel8.img)
    → U-Boot reads env, selects A or B rootfs slot
      → loads kernel + DTB from selected slot
        → Linux boots
```

RPi firmware still runs (baked into GPU ROM), processes `config.txt`, applies all device tree overlays, then hands the resolved DTB to U-Boot. Camera stack (libcamera-vid, V4L2) is unaffected — confirmed by Home Assistant OS which runs U-Boot on RPi 3/4 with cameras.

**config.txt change:**
```ini
kernel=u-boot.bin
```

**U-Boot environment variables:**
```
boot_order=A B              # or "B A" after update to B
boot_count=0                # incremented each boot by U-Boot
upgrade_available=0          # set to 1 after swupdate
bootlimit=3                  # max failed boots before rollback
bootdelay=0                  # no delay — device boots unattended
```

**Rollback logic (executed by U-Boot before kernel load):**
```
if upgrade_available == 1:
    boot_count += 1
    if boot_count >= bootlimit:
        swap boot_order         # e.g., "B A" → "A B"
        upgrade_available = 0
        run altbootcmd           # boot the known-good slot
    else:
        boot boot_order[0]       # try the updated slot
else:
    boot boot_order[0]           # normal boot
```

**Yocto integration:**
- `u-boot-rpi` recipe from `meta-raspberrypi` (already available)
- `u-boot-fw-utils` for `fw_printenv`/`fw_setenv` from Linux userspace
- U-Boot environment stored on boot partition (FAT32)
- SWUpdate built with `CONFIG_UBOOT=y` for native integration
- Boot delay set to 0 — no interactive prompt

---

### 2. Artifact types

#### 2a. Full-system bundle (`.swu`)

Primary production update. Contains a compressed rootfs image written to the inactive A/B slot.

```
hm-server-pi4-full-2.1.0.swu
├── sw-description              # libconfig manifest (signed)
├── sw-description.sig          # Ed25519 detached signature
└── home-monitor-rootfs.ext4.gz # compressed rootfs (~150-200 MB)
```

Camera: `hm-camera-zero2w-full-0.9.0.swu` (~80-100 MB compressed).

The rootfs image is built at content size. After SWUpdate writes it to the inactive slot, a first-boot service runs `resize2fs` to fill the 8 GB partition.

#### 2b. App-only bundle (`.tar.zst` + detached signature)

Lighter update for dev/testing. Replaces only the Python application layer.

```
hm-server-pi4-app-2.1.1.tar.zst
├── monitor/                    # Flask app (routes, services, templates, static)
└── metadata.json               # version, compat, migration hooks

hm-server-pi4-app-2.1.1.tar.zst.sig   # Ed25519 detached signature
```

**Install mechanism — symlink swap:**
```
/opt/monitor/
├── releases/
│   ├── 2.1.0/                  # previous version
│   └── 2.1.1/                  # new version (extracted from bundle)
└── current -> releases/2.1.1/  # atomic symlink swap
```

App-only bundles:
- Extract to `/opt/monitor/releases/<version>/` (or `/opt/camera/releases/<version>/`)
- Atomic activation: `ln -sfn releases/<version> /tmp/current && mv -T /tmp/current /opt/monitor/current`
- Rollback: re-point symlink to previous version + restart service
- Restart application only (`systemctl restart monitor`) — no reboot
- Keep last 3 versions; prune older ones
- Do not modify kernel, system packages, init system, or systemd units

SWUpdate delivers this via its `files` handler (extract to path) + `shellscript` handler (symlink swap + service restart).

---

### 3. Artifact naming and metadata

**Naming convention:**
```
hm-<target>-<type>-<version>.<ext>

Examples:
  hm-server-pi4-full-2.1.0.swu
  hm-server-pi4-app-2.1.1.tar.zst
  hm-camera-zero2w-full-0.9.0.swu
  hm-camera-zero2w-app-0.9.4.tar.zst
```

**Required metadata** (in `sw-description` for `.swu`, in `metadata.json` for app-only):

| Field | Example | Purpose |
|-------|---------|---------|
| `artifact_type` | `full-system` or `app-only` | Determines install handler |
| `target_device` | `server-pi4` or `camera-zero2w` | Prevents cross-device install |
| `hardware_compat` | `["rpi4-rev1.5"]` | Rejects incompatible hardware |
| `version` | `2.1.0` | Semver |
| `channel` | `stable`, `beta`, `dev` | Filters available updates |
| `min_base_version` | `2.0.0` | Minimum installed version |
| `rollback_supported` | `true` | Whether rollback is safe |
| `migration_hook` | `v3` | Schema migration version if needed |

---

### 4. Signing — one trust model for all artifacts

- **Algorithm**: Ed25519
- **Build machine** holds the private key (`swupdate-signing.key`) — never on devices, never in repo, CI secret
- **Devices** hold the public key at `/etc/swupdate-public.pem` (baked into rootfs at build time)
- **Full-system bundles**: SWUpdate verifies `sw-description.sig`, then checks hashes of each included image
- **App-only bundles**: Detached `.sig` file verified against the same public key before extraction
- **Trust anchor**: The signing key is the only trust. The delivery mechanism (USB, upload, network, SCP) is never proof of trust. All paths go through identical verification.

---

### 5. Staging pipeline

All delivery modes feed into the same directory structure:

**Server and camera:**
```
/data/update/
├── inbox/      # untrusted — raw uploads, USB copies, SCP drops, downloads
├── staging/    # verified — signature valid, metadata checked, installable
└── history/    # audit trail — manifests, results, logs, rollback refs
```

**Rules:**
- `inbox` = untrusted. Verification succeeds → move to `staging`. Verification fails → delete from `inbox`, log error to `history`
- Only `staging` files are installable
- No installs from `/tmp`, arbitrary paths, or directly from USB mount points
- `history` retains metadata and results for last 10 updates
- Space check before accepting into `inbox`: reject if free space on `/data` would drop below 2 GB (server) or 1 GB (camera) after staging

---

### 6. Delivery modes

All modes feed into `inbox` → verify → `staging` → install. Source is never trust.

#### 6a. USB / pendrive (server only)

For offline updates, field service, lab use.

```
1. User inserts USB into server
2. Server detects mount (udev rule)
3. Scans one predictable path: /media/<label>/updates/manifest.json
4. Dashboard shows available updates from manifest
5. Admin selects → copy to /data/update/inbox/
6. Verification pipeline runs
7. Never auto-installs on insert
```

USB layout:
```
/updates/
  manifest.json
  server-pi4/
    stable/
      hm-server-pi4-full-2.1.0.swu
  camera-zero2w/
    stable/
      hm-camera-zero2w-full-0.9.0.swu
```

Requirements:
- Scan only `/media/<label>/updates/` — no recursive search
- Copy to inbox before verification — do not install from mounted media
- Never auto-install on insert

#### 6b. Manual upload via dashboard (server)

Existing `POST /api/v1/ota/server/upload` enhanced:

```
1. Admin uploads bundle via dashboard
2. File saved to /data/update/inbox/
3. Verification: signature → metadata → compatibility → space check
4. Valid → moved to staging, dashboard shows "ready to install"
5. Admin confirms → install begins
```

#### 6c. Server-mediated camera update (production)

```
1. Admin triggers from dashboard: POST /api/v1/ota/camera/<id>/push
2. Server pushes bundle to camera OTA agent over mTLS (ADR-0009, port 8080)
3. Camera saves to /data/update/inbox/ (47 GB available — plenty of room)
4. Camera verifies: signature, metadata, compatibility
5. Moves to staging, installs from staging
6. Reboot, U-Boot boots updated slot
7. Health check → confirm or rollback
8. Server monitors camera re-appearance, updates firmware_version
```

#### 6d. SSH/SCP developer push (dev builds only)

```
1. Developer: scp bundle root@device:/data/update/inbox/
2. Update agent detects new file in inbox (inotify or polled)
3. Same verification pipeline — no shortcuts
4. Dev mode: can auto-trigger install after verification
```

Transport convenience, not a trust bypass.

#### 6e. Repo URL polling (future, via Suricatta)

```
1. Device configured: UPDATE_SOURCE, UPDATE_CHANNEL, AUTO_CHECK_INTERVAL
2. SWUpdate Suricatta polls repo metadata
3. Downloads matching artifact to inbox
4. Normal verification pipeline
5. Production: admin confirmation required
6. Dev: auto-install after verification
```

Designed into the pipeline from day one. Implementation deferred.

---

### 7. Full-system install flow

**Server self-update:**
```
 1. Bundle arrives in /data/update/inbox/ (via any delivery mode)
 2. Verification:
    a. Ed25519 signature on sw-description
    b. Metadata: target_device == server-pi4
    c. hardware_compat matches board revision
    d. version > current AND version >= min_base_version
    e. channel matches device config
    f. Space check: /data free > 2 GB after staging
 3. Move to /data/update/staging/
 4. Admin confirms install (auto in dev mode)
 5. swupdate -i /data/update/staging/<bundle>.swu -e stable,<inactive-slot>
    - SWUpdate verifies hashes, streams rootfs image to inactive partition
    - No intermediate unpack — handler writes directly to block device
 6. fw_setenv upgrade_available 1
    fw_setenv boot_count 0
 7. Reboot
 8. U-Boot boots updated slot, increments boot_count
 9. swupdate-check.service runs health checks:
    - Flask starts and store loads
    - GET /api/v1/ota/status returns 200
    - mediamtx process running
    - nginx responding on :443
10. On SUCCESS:
    - fw_setenv upgrade_available 0
    - fw_setenv boot_count 0
    - resize2fs /dev/mmcblk0p<N> (expand rootfs to fill 8 GB)
    - Archive result to /data/update/history/
    - Audit log: OTA_COMPLETED
11. On FAILURE (or boot_count reaches 3):
    - U-Boot runs altbootcmd → boots previous slot
    - Audit log: OTA_ROLLBACK (on next successful boot)
```

**Camera update (server-mediated):**
```
 1. Admin triggers: POST /api/v1/ota/camera/<id>/push
 2. Server sends .swu to camera OTA agent over mTLS (port 8080)
 3. Camera saves to /data/update/inbox/
 4. Camera verifies: signature, metadata, compatibility
 5. Moves to /data/update/staging/
 6. swupdate writes rootfs to inactive slot
 7. fw_setenv upgrade_available 1, boot_count 0
 8. Reboot
 9. Health check: lifecycle reaches RUNNING, RTSP stream established
10. Success: fw_setenv upgrade_available 0, resize2fs, archive history
11. Server polls camera health → updates firmware_version in cameras.json
12. Failure: U-Boot auto-rollback, camera comes back on old version
```

---

### 8. App-only install flow

```
 1. Bundle arrives in inbox (upload, SCP, or USB)
 2. Verify detached .sig + metadata.json
 3. Check: target_device, version, min_base_version, channel
 4. Space check
 5. Move to staging
 6. Create: /opt/monitor/releases/<version>/
 7. Extract bundle to new directory
 8. Run migration hook if metadata specifies one
 9. Atomic symlink swap: /opt/monitor/current → releases/<version>/
10. systemctl restart monitor (or camera-streamer)
11. Health check: API responds within 30s
12. On SUCCESS:
    - Prune oldest release if >3 kept
    - Archive result to history
    - Audit log: OTA_APP_COMPLETED
13. On FAILURE:
    - Revert symlink to previous version
    - systemctl restart monitor
    - Audit log: OTA_APP_ROLLBACK
```

No reboot. Rollback is instant (symlink swap + service restart).

---

### 9. Camera OTA agent

The `OTAAgent` class in `camera_streamer/ota_agent.py`:
- HTTP server on port 8080 (nftables: server IP only)
- Requires mTLS — verifies server cert against CA (ADR-0009)
- `POST /update`: receives bundle, saves to `/data/update/inbox/`
- `GET /update/status`: returns install state and progress
- Same verification pipeline as all other delivery modes
- Memory-safe: streams upload to disk, never loads full bundle into RAM
- Audit events: `OTA_STARTED`, `OTA_COMPLETED`, `OTA_FAILED`, `OTA_ROLLBACK`

---

### 10. Space budgeting

With ~47 GB on `/data`, space is generous. Policies exist as safety nets.

**Server:**

| Check | Threshold | Action |
|-------|-----------|--------|
| Accept into inbox | `/data` free > 2 GB after copy | Reject upload with 507 |
| Begin install | `/data` free > 2 GB after staging | Reject with "insufficient space" |
| Recording headroom | `/data` free > 1 GB | StorageManager pauses recording during update |

**Camera:**

| Check | Threshold | Action |
|-------|-----------|--------|
| Accept into inbox | `/data` free > 1 GB after copy | Reject push |
| Begin install | `/data` free > 1 GB after staging | Reject |

**OTA artifact sizing:**

| Bundle type | Server | Camera |
|-------------|--------|--------|
| Full-system (.swu) | ~150-200 MB compressed | ~80-100 MB compressed |
| App-only (.tar.zst) | ~30-50 MB | ~15-25 MB |

---

### 11. API endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/v1/ota/status` | GET | login | Status for all devices |
| `/api/v1/ota/server/upload` | POST | admin | Upload bundle to inbox |
| `/api/v1/ota/server/install` | POST | admin | Install from staging |
| `/api/v1/ota/server/rollback` | POST | admin | Force rollback to previous slot/version |
| `/api/v1/ota/usb/scan` | GET | admin | Scan USB for available updates |
| `/api/v1/ota/usb/import` | POST | admin | Copy USB bundle to inbox |
| `/api/v1/ota/camera/<id>/push` | POST | admin | Push update to camera |

---

### 12. Yocto integration

| Recipe / Class | Purpose |
|----------------|---------|
| `recipes-bsp/u-boot/` | U-Boot for RPi 4B + Zero 2W, A/B boot script, env setup, `bootdelay=0` |
| `recipes-support/swupdate/swupdate_%.bbappend` | SWUpdate with Ed25519 verification, U-Boot handler (`CONFIG_UBOOT=y`) |
| `recipes-support/swupdate/swupdate-check.service` | Post-boot health check, resize2fs, `fw_setenv upgrade_available 0` |
| `classes/swupdate-image.bbclass` | Generates `.swu` from built rootfs (compact image + signed sw-description) |
| `u-boot-fw-utils` in IMAGE_INSTALL | `fw_printenv`/`fw_setenv` userspace tools |
| Kernel config fragment | `CONFIG_CRYPTO_ADIANTUM=y` (for ADR-0010 LUKS) |

---

### 13. Production vs dev policy

**Production:**
- Signed full-system A/B updates are the default
- All delivery modes require signature verification — no exceptions
- USB and upload are primary modes
- Cameras receive updates through the server only
- Admin confirmation required before any install
- Downgrade blocked unless admin explicitly overrides

**Dev:**
- App-only bundles allowed from upload, USB, and SCP
- Signature verification still required (dev signing key)
- Auto-install after verification is allowed
- Camera can receive updates via SCP in lab mode
- Downgrade allowed

**Not recommended (any build):**
- `pip install` on target
- `git pull` on target
- Unsigned bundles
- Arbitrary path installs
- Direct `ssh && swupdate -i /tmp/file` bypassing the pipeline

## Rationale

- **SWUpdate over RAUC**: SWUpdate's file-level handlers support both full-rootfs and app-only updates natively. RAUC requires a separate partition image for app updates. SWUpdate's streaming cpio format avoids staging large bundles. Suricatta provides built-in polling for future repo-based delivery. RAUC has a better RPi Yocto layer (meta-rauc-community) and is used by HAOS/Steam Deck, but its image-oriented design doesn't fit our dual-channel (full + app-only) requirement.
- **U-Boot**: Industry standard for embedded A/B boot management. Native SWUpdate integration via `fw_printenv`/`fw_setenv`. Automatic rollback via `bootlimit`/`altbootcmd`. Home Assistant OS runs U-Boot on RPi 3/4 successfully. Camera overlays applied by RPi firmware before U-Boot handoff — no conflict with libcamera. ~2s boot overhead (reduced to ~0.1s with `bootdelay=0`) is irrelevant for 24/7 devices.
- **8 GB rootfs slots**: Current usage is 436 MB (server) / 296 MB (camera). 8 GB provides 16x headroom for future package growth. On 64 GB cards, leaves ~47 GB for /data.
- **512 MB boot**: Room for U-Boot, two kernel images (if per-slot kernels needed later), DTBs, overlays, U-Boot env. Generous on a 64 GB card.
- **Multiple delivery modes, one trust model**: Signing key is the sole trust anchor. Adding a delivery mode is cheap because verification is shared. USB handles offline/field. Upload handles admin. SCP handles dev. Suricatta handles future polling.
- **App-only bundles**: Most development changes are Python-only. Full rootfs reboot for a one-line fix is wasteful. Symlink swap provides instant rollback. Same signing guarantees as full-system bundles.
- **Ed25519**: Same algorithm as existing certificate infrastructure. Small keys (32 bytes), fast verify on ARM without hardware crypto.
- **inbox/staging/history**: Clear state machine prevents accidental install of unverified bundles. History provides audit trail.

## Alternatives Considered

### RAUC
Used by Home Assistant OS and Steam Deck. Smallest binary (~512 KB). Mandatory signing. Excellent RPi Yocto layer. Rejected because its image-oriented slot model doesn't naturally support app-only file-level updates. If we only needed full-rootfs A/B, RAUC would be the better choice.

### Mender
Integrated fleet management. 6.9 MB Go binary — larger than the camera app itself. Delta updates enterprise-only. Cloud dependency. Rejected.

### RPi firmware tryboot instead of U-Boot
Avoids adding U-Boot. Rejected because: no standard boot counting, no `fw_printenv`/`fw_setenv`, no documented SWUpdate integration, requires custom rollback scripting, RPi-specific (not portable). Home Assistant OS uses tryboot only on RPi 5 (which has EEPROM bootloader), keeps U-Boot on RPi 3/4.

### Containers (Docker) for app updates
Clean OS/app separation. Rejected because Docker runtime is too heavy for Zero 2W (512 MB RAM, 183 MB available). Container overhead would compete with libcamera-vid and ffmpeg.

### OverlayFS for app updates
Read-only rootfs with writable overlay. Interesting but OverlayFS is a filesystem feature, not an update mechanism. Still needs SWUpdate/RAUC to deliver content. Adds debugging complexity.

## Consequences

- Both devices need U-Boot recipe and config — one-time setup using `meta-raspberrypi`'s `u-boot-rpi`. Zero 2W has less community U-Boot testing — may need debugging during integration.
- Boot time increases ~0.1-2s from U-Boot — irrelevant for 24/7 devices.
- Build machine needs Ed25519 signing key — CI secret.
- Five delivery modes increase test surface — mitigated by shared verification pipeline.
- App-only updates maintain up to 3 version directories (~150 MB on server, ~75 MB on camera) — negligible on 8 GB rootfs.
- Config schema migrations must be backward-compatible — rollback returns to old code reading same `/data`.
- First update requires SD card flash — no bootstrap-over-network.
- Suricatta repo polling is designed into the pipeline but implementation is deferred.
