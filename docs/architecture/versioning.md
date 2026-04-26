# Versioning — end-to-end SSOT policy

**Status:** Draft for review (do not implement yet)
**Date:** 2026-04-26
**Related:** ADR-0014 (signing), ADR-0008 (A/B OTA), `scripts/release.sh`, `scripts/check_version_consistency.py`

---

## 0. Why this document exists

Three distinct version strings have surfaced for the same release on the same camera at the same moment, depending on which page you look at:

| Surface | Value seen | Reality |
|---|---|---|
| `/etc/os-release` (kernel boot banner, dashboard footer) | `1.4.2` | correct |
| Server dashboard `firmware_version` for the camera | `v1.4.2-dev` | **stale** (carried over from a prior OTA test on the same `cam-id`) |
| Camera local status page `firmware_version` | `1.0.0` | **wrong** (read from a hardcoded `/etc/sw-versions`) |

Each of those numbers was produced by a different code path reading a different file. The user-facing inconsistency is the visible symptom; the underlying cause is that **we never decided which file is canonical at runtime, and three readers ended up trusting three different files.** The repo-side SSOT (`VERSION`) is healthy. The image-side SSOT is missing.

Goal of this document: pick one image-side SSOT, route every reader through it, and add CI guards so future drift fails the build.

This is a design-only document. Implementation lands as a follow-up after sign-off.

---

## A. Scope of "version" in this system

We have several version concepts in flight today. The proposed policy is **a single product version moves the camera and server together; everything else is either derived or a debugging breadcrumb.**

| Concept | Today | Proposed | Rationale |
|---|---|---|---|
| Product release version | `VERSION` file at repo root | **same — already SSOT** | Already wired; `release.sh` enforces semver; CHANGELOG headings checked. |
| OS image version (Yocto) | `DISTRO_VERSION = ${@open(VERSION).read()}` | same | Already correct via `meta-home-monitor/conf/distro/home-monitor.conf:17`. |
| `BUILD_ID` in `/etc/os-release` | `BUILD_ID = "${DISTRO_VERSION}"` | same | Pinned by distro conf to avoid DATETIME-based reproducibility breakage. |
| Camera-streamer Python pkg | `setup.py: version="1.0.0"` (frozen) | **clarify intent**: leave frozen, add a comment | Already documented in `check_version_consistency.py` as intentional. The comment is currently in the consistency-check script, not in `setup.py` where someone editing the recipe would see it. |
| Monitor server Python pkg | same | same | same |
| Kernel version | poky's stock | unchanged | We don't ship custom kernels; we pin via `PREFERRED_VERSION_linux-raspberrypi` but kernel version doesn't appear in user-facing surfaces. |
| U-Boot version | poky/meta-rpi stock | unchanged | Not user-visible. |
| SWU bundle version | `version = "@@VERSION@@";` substituted by `build-swu.sh` from VERSION file | same | Already correct. |
| SWU filename | `<component>-update-${VERSION}-{prod,dev}.swu` | same | Already correct via `build-swu.sh` and `build.sh`. |
| SD card image filename | `<component>-image-${VERSION}-{prod,dev}.wic.bz2` | same | Already correct via Yocto deploy + `release-stage-*.sh`. |
| Git tag | `vX.Y.Z` | same | `release.sh tag` enforces. |
| CHANGELOG heading | `## [X.Y.Z] — YYYY-MM-DD` | same | `release.sh prepare` writes; `check_version_consistency.py` validates. |
| `/etc/os-release VERSION_ID` | `VERSION_ID = "${DISTRO_VERSION}"` | same | Already correct via `os-release.bbappend`. |
| `/etc/os-release PRETTY_NAME` | `"Home Monitor OS ${DISTRO_VERSION} (${DISTRO_CODENAME})"` | same | Already correct. |
| **`/etc/sw-versions`** | **static `home-monitor 1.0.0`** | **template at build time + write at OTA post-install** | **THE BUG.** |
| Camera/server displayed `firmware_version` | reads `/etc/sw-versions` first line | **read `/etc/os-release VERSION_ID`** | Single image-side SSOT, single helper. |
| Avahi mDNS TXT record `version=1.0` | static `1.0` | **leave as-is** but document it's protocol-version, not release-version | Already noted in `check_version_consistency.py`'s exclusion list. |

**Decision: single product version** (`VERSION` file). Camera and server move in lockstep on the same release tag. We don't support mixed-version operation (e.g. server on 1.5 + camera on 1.4) as a first-class scenario — the OTA flow is per-component but releases are cut as a matched pair.

---

## B. Audit — every place version is read or displayed today

Receipts for every surface, with file paths, current behaviour, and the source it should derive from. Compiled against `main` at `95f64ef`.

### Repo-side (build inputs)

| Surface | File / path | Current | Derives from |
|---|---|---|---|
| Single source of truth | `VERSION` (one line, semver `X.Y.Z`) | manual, `release.sh prepare` writes | — (this is the SSOT) |
| Yocto distro conf | `meta-home-monitor/conf/distro/home-monitor.conf:17` | `DISTRO_VERSION := ${@open(... + '/../VERSION').read().strip()}` | `VERSION` file (correct) |
| Yocto distro conf | `meta-home-monitor/conf/distro/home-monitor.conf:26` | `BUILD_ID = "${DISTRO_VERSION}"` | DISTRO_VERSION (correct) |
| os-release recipe | `meta-home-monitor/recipes-core/os-release/os-release.bbappend:11-12` | `VERSION_ID = "${DISTRO_VERSION}"`; `PRETTY_NAME = "Home Monitor OS ${DISTRO_VERSION} (${DISTRO_CODENAME})"` | DISTRO_VERSION (correct) |
| **sw-versions recipe** | `meta-home-monitor/recipes-core/sw-versions/sw-versions_1.0.bb` | `install -m 0644 ${WORKDIR}/sw-versions ${D}${sysconfdir}/sw-versions` (static file) | **STATIC `home-monitor 1.0.0`** — bug |
| sw-versions baseline | `meta-home-monitor/recipes-core/sw-versions/files/sw-versions` | `home-monitor 1.0.0` literal | — (the literal that ships) |
| Camera Python pkg | `app/camera/setup.py:5` | `version="1.0.0"` (frozen by policy) | — (intentionally frozen) |
| Server Python pkg | `app/server/setup.py:5` | `version="1.0.0"` (frozen) | — (same) |
| CHANGELOG | `CHANGELOG.md` | `## [X.Y.Z] — YYYY-MM-DD` headings, `release.sh prepare` writes | `VERSION` file at release-cut time |
| Git tag | `git tag vX.Y.Z` | `release.sh tag` creates from `VERSION` | `VERSION` file |
| GitHub release | `gh release create vX.Y.Z` | matches tag; assets named with `${VERSION}` | git tag |
| SWU sw-description | `swupdate/sw-description.{server,camera}:3` | `version = "@@VERSION@@";` substituted by build-swu.sh | `VERSION` file (via `build-swu.sh:108-111`) |
| SWU filename | `${REPO_DIR}/${TARGET}-update-${VERSION}.swu` | `build-swu.sh` and `build.sh` | `VERSION` file or git describe |
| SD card image filename | Yocto deploy convention `${IMAGE}-${MACHINE}.rootfs.wic.bz2`; renamed by release-stage to `${COMPONENT}-image-${VERSION}-${PROFILE}.wic.bz2` | release-stage scripts | `VERSION` file |

### Image-side (runtime files baked into the rootfs)

| File | Current content (v1.4.2 prod) | Read by | Updated by |
|---|---|---|---|
| `/etc/os-release` | `VERSION_ID="1.4.2"`, `BUILD_ID="1.4.2"`, `PRETTY_NAME="Home Monitor OS 1.4.2 (phase1)"` | systemd, motd/issue, kernel boot banner, getty header | Yocto build (correct) |
| **`/etc/sw-versions`** | **`home-monitor 1.0.0`** (regardless of release) | `_get_firmware_version()` in two places | Yocto build (currently static); SWUpdate post-install hook (currently nobody updates it on OTA either — see §J) |
| `/etc/swupdate.cfg` | `identify: ( { name = "home-monitor"; value = "1.0.0"; } );` (static) | swupdate daemon's identify check | Yocto build (currently static, but **functionally** part of the SWU-acceptance gate, not user-facing — see §H below) |
| `/etc/swupdate-public.crt` + `/etc/swupdate-enforce` | per ADR-0014 (signing always-on since 1.4.1) | swupdate daemon | Yocto build |
| `/etc/hwrevision` | hardware compatibility key (separate concept, not release-version) | swupdate `hardware-compatibility` matcher | Yocto build |

### Image-side (runtime read paths)

| Caller | File | Returns | Display surface |
|---|---|---|---|
| `app/camera/camera_streamer/heartbeat.py:102 _get_firmware_version()` | reads `/etc/sw-versions`, returns first row's second field | currently `"1.0.0"` always | sent in heartbeat → server stores in `cameras.json firmware_version` → dashboard shows |
| `app/camera/camera_streamer/heartbeat.py:273` | uses the above | — | — |
| `app/camera/camera_streamer/status_server.py:234 _get_firmware_version(sw_versions_path="/etc/sw-versions")` | same file, same parse | currently `"1.0.0"` always | camera local status page |
| `app/camera/camera_streamer/status_server.py:857-883` | uses the above | — | — |
| Server-side reads | `app/server/monitor/services/camera_service.py:245,311,514-516` (heartbeat ingest), `discovery.py:63,95,108-109,173,330-348`, `settings_service.py:57`, `api/cameras.py:220`, `api/ota.py:69,82` | reads `camera.firmware_version` or `settings.firmware_version` from store | dashboard, OTA status, cameras list |
| Avahi mDNS TXT | `app/server/config/avahi-homemonitor.service:30` | `<txt-record>version=1.0</txt-record>` (static) | mDNS browse (protocol version, not release version) |
| Captive-portal DNS | `app/camera/config/captive-portal-dnsmasq.conf` | wildcard redirect, no version | — |
| Camera setup wizard `wifi_setup.py` | does not currently display version | — | — (proposal: should show release version on the first-boot page) |

### Display surfaces (where users see version)

| User-facing surface | Source today | Source proposed |
|---|---|---|
| Boot banner / serial getty (`Home Monitor OS X.Y.Z hostname ttyS0`) | `/etc/os-release PRETTY_NAME` (correct) | unchanged |
| `cat /etc/os-release` (SSH/serial admin) | Yocto build (correct) | unchanged |
| Camera local status page → "Firmware: X.Y.Z" | `_get_firmware_version()` → `/etc/sw-versions` (broken) | new shared `firmware_version()` helper → `/etc/os-release VERSION_ID` |
| Server dashboard → cameras list → `fw vX.Y.Z` | `cameras.json firmware_version` (heartbeat-fed; today wrong because heartbeat reads broken file) | unchanged store, fed by the new shared helper on camera side; server also re-reads `/etc/os-release` for its own `settings.firmware_version` |
| Server dashboard footer / "About" page | `settings_service.py:57 → settings.firmware_version` (today driven from a settings JSON field that nobody writes — `Settings.firmware_version: str = "1.0.0"` default) | replace static default with runtime read of `/etc/os-release` |
| OTA UI "Current version" line | `api/ota.py:69 settings.firmware_version` and `api/ota.py:82 cam.firmware_version` | same store, fed correctly |

---

## C. Single-source-of-truth policy

### Repo-side SSOT — `VERSION`

Already in place. No change. Owners: `scripts/release.sh prepare` writes; humans don't touch directly. `check_version_consistency.py` enforces semver shape.

### Image-side SSOT — `/etc/os-release` (`VERSION_ID`)

**Why `/etc/os-release` and not `/etc/sw-versions`:**

- Standard Linux convention (`os-release(5)`); every Yocto-derived distro stamps it correctly via `os-release.bb`.
- Already correctly templated in our distro layer (`os-release.bbappend`).
- Already what kernel/getty/PRETTY_NAME present to the user.
- Read trivially from Python without external deps:
  ```python
  with open("/etc/os-release") as f:
      for line in f:
          k, _, v = line.strip().partition("=")
          if k == "VERSION_ID":
              return v.strip().strip('"')
  ```

**Why not `/etc/sw-versions`:**

- The file is owned by SWUpdate's own identify/version-track mechanism, not by us as a user-facing version surface. Hijacking it for runtime display creates the exact drift we're hitting now (build-time content vs OTA-time updates vs application reads, three sources, one filename).
- Even if we template it perfectly, we'd still be relying on SWUpdate's post-install hook firing on every OTA path; on fresh-flash there is no OTA, so the hook never fires. We'd need a separate first-boot writer too. Cheaper to just stop using it for display and let SWUpdate own it for what it's actually for (sw-version negotiation between bundles and devices).

### Decision

1. **Image-side SSOT for runtime version display:** `/etc/os-release` `VERSION_ID`.
2. **`/etc/sw-versions` retained but templated** (no longer static `1.0.0`). Owned by Yocto build + SWUpdate's post-install hook. **Not used by application code** for display. Kept consistent so the SWUpdate identify mechanism continues to work.
3. **`/etc/swupdate.cfg`'s `identify` block** likewise becomes templated; we audit whether it's load-bearing for the swupdate daemon's accept/reject behaviour and act accordingly (see §J step 0).

A single shared helper module (proposal: `camera_streamer.version` and `monitor.version`) provides `release_version() -> str`. Every display path calls that helper. Both the camera package and the monitor-server package ship the same minimal parser.

---

## D. Update flow — who/when/where

### Build time (developer cuts a release)

```
$ ./scripts/release.sh prepare 1.5.0
  ├─ writes 1.5.0 to VERSION
  ├─ promotes [Unreleased] → [1.5.0] in CHANGELOG.md
  ├─ runs check_version_consistency.py
  └─ commits on release/1.5.0 branch (does NOT push, tag, or build)

(merge release/1.5.0 → main)

$ ./scripts/release.sh tag 1.5.0
  └─ git tag -a v1.5.0 -m "v1.5.0 — <headline>"

$ ./scripts/release.sh build 1.5.0     # on the build host
  └─ build.sh all-prod (and all-dev)
       ├─ bitbake reads VERSION via DISTRO_VERSION
       ├─ os-release.bbappend writes VERSION_ID + PRETTY_NAME
       ├─ sw-versions recipe writes "home-monitor 1.5.0" (NEW: templated)
       ├─ swupdate.cfg identify value writes 1.5.0 (NEW: templated)
       └─ build-swu.sh substitutes @@VERSION@@ into sw-description
```

### OTA install (on-device)

The existing `/var/lib/camera-ota/post-update.sh` (server side) and equivalent on camera get a tiny extension:

```sh
# After successful slot write, before reboot, write the new version
# string to /etc/sw-versions on the standby slot's /etc.
echo "home-monitor ${NEW_VERSION}" > /mnt/standby/etc/sw-versions
```

Caveat: this is only needed if we keep `/etc/sw-versions` as a SWUpdate identify input. For the user-facing display path, the on-disk `/etc/sw-versions` value doesn't matter once we stop reading it from app code. **Most likely we just rebuild the templated baseline at every Yocto build and don't write it at OTA time** — because the rootfs that swupdate writes already contains the new templated value. No post-install touch needed.

### Runtime (heartbeat, status page, dashboard)

```python
# new shared helper, lives in BOTH camera and server packages
def release_version() -> str:
    """Single read path for the product release version.
    Reads /etc/os-release VERSION_ID. Returns '' on parse failure
    so callers can render 'unknown' instead of crashing."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k == "VERSION_ID":
                    return v.strip().strip('"')
    except OSError:
        pass
    return ""
```

Camera heartbeat:
```python
"firmware_version": release_version(),
```

Camera status page:
```python
firmware_version = release_version()
```

Server settings:
```python
# replace default static "1.0.0" with runtime read at startup
settings.firmware_version = release_version()
```

Server stores received heartbeats unchanged (`camera.firmware_version = data["firmware_version"]`). The dashboard shows whatever the camera sent.

### Why everyone reads from `/etc/os-release` and not from a Python `__version__`

- Python package versions in `setup.py` are frozen at `1.0.0` by repo policy (recipe metadata, not user-facing — see `check_version_consistency.py` rationale).
- The "release version" is a distro-level concept, not a package concept.
- `/etc/os-release` is what `os-release(5)` is for; it survives any future package layout changes.

---

## E. Dev vs prod, signed vs unsigned, semver pre-release tags

### Same VERSION across dev and prod

| | Dev | Prod |
|---|---|---|
| `/etc/os-release VERSION_ID` | `1.5.0` | `1.5.0` |
| `/etc/os-release PRETTY_NAME` | `Home Monitor OS 1.5.0 (phase1)` | same |
| SWU filename | `*-update-1.5.0-dev.swu` | `*-update-1.5.0-prod.swu` |
| SD image filename | `*-image-1.5.0-dev.wic.bz2` | `*-image-1.5.0-prod.wic.bz2` |
| Signing | enforced (since 1.4.1, ADR-0014) | enforced |
| `debug-tweaks` | yes | no |

The dev/prod axis is **build profile**, not version. The release version moves both profiles together. Both flavours of the 1.5.0 release report `firmware_version: 1.5.0`. The only version-related knob that ever differs is the SWU/SD filename suffix.

### Pre-release tags

Follow semver (https://semver.org/#spec-item-9):

| Form | Use case |
|---|---|
| `1.5.0-rc.1`, `1.5.0-rc.2`, … `1.5.0` | Standard release-candidate cadence on a real release branch |
| `1.5.0-dev.20260501` | Daily dev builds on `main` between releases (date-stamped, not the same as the `dev` build profile) |
| `1.5.0-hotfix.1` | Hotfix lineage off a released tag |

Reject:
- Arbitrary suffixes like `1.4.1-dev-migrate` (we shipped one of these as a one-off; explicitly mark in the doc as a recovery-only artefact, not a normal release)
- Pre-release tags that don't satisfy `release.sh validate_version` (currently strict `X.Y.Z` only — extend to allow `X.Y.Z(-PRERELEASE)?` per semver)

`release.sh prepare` validates the new version is a strict semver bump from the previous; extend to handle pre-release ordering (`1.5.0-rc.1 < 1.5.0-rc.2 < 1.5.0`).

---

## F. Camera and server follow the same rules

Same `VERSION` file drives both. Same `/etc/os-release` semantics on both. Same `release_version()` helper, replicated as identical code in `camera_streamer/version.py` and `monitor/version.py` (no shared package across the camera/server split — that's an existing architectural boundary, ADR-0006).

| Surface | Camera | Server |
|---|---|---|
| `/etc/os-release VERSION_ID` | yes (Yocto) | yes (Yocto, same distro layer) |
| `/etc/sw-versions` | yes (templated) | yes (templated) |
| Runtime read helper | `camera_streamer.version.release_version()` | `monitor.version.release_version()` |
| Heartbeat field name | `firmware_version` | (server doesn't heartbeat) |
| Local status page | renders `release_version()` | renders `release_version()` in dashboard footer |
| SWU bundle | `camera-update-X.Y.Z-{dev,prod}.swu` | `server-update-X.Y.Z-{dev,prod}.swu` |
| SD image | `camera-image-X.Y.Z-{dev,prod}.wic.bz2` | `server-image-X.Y.Z-{dev,prod}.wic.bz2` |

Mismatched-version operation (camera on 1.5, server on 1.4) is **not a supported configuration**. If a partial OTA leaves the fleet split, the user should re-OTA the lagging side. The dashboard surfaces a warning (existing pattern — `camera.firmware_version != settings.firmware_version` already triggers a banner).

---

## G. CI guardrails

Existing `scripts/check_version_consistency.py` already covers VERSION + distro conf + CHANGELOG. Extend to cover every surface in §B with a hard fail on drift.

### New checks proposed

1. **Templated `sw-versions`** — assert `meta-home-monitor/recipes-core/sw-versions/sw-versions_1.0.bb` does not have a static `install ${WORKDIR}/sw-versions` line. Pattern-match for `${DISTRO_VERSION}` somewhere in the recipe's do_install. Fails if anyone reverts to a static file.

2. **Templated `swupdate.cfg` identify** — assert `meta-home-monitor/recipes-support/swupdate/files/swupdate.cfg` (or wherever the templated copy lives post-fix) does not contain a hardcoded `value = "1.0.0"`. Recipe should `sed`-substitute `${DISTRO_VERSION}` at install time.

3. **No `/etc/sw-versions` reads in app code** — grep `app/camera/` and `app/server/` for `sw-versions`. Should return zero hits in non-test, non-doc code paths after migration.

4. **`release_version()` is the only firmware-version source** — grep for direct `/etc/os-release` reads in app code outside the helper module. Should return only the helper's own implementation.

5. **SWU filename matches embedded sw-description version** — extract `sw-description` from each built SWU, parse `version = "..."`, compare to filename's `${VERSION}` field. Hard fail on mismatch. Catches both renamed-but-not-rebuilt and rebuilt-but-not-renamed.

6. **Pre-release tag validity** — extend `validate_version` in `release.sh` to accept `X.Y.Z(-PRERELEASE)?` per semver §9; reject `dev-foo` or other non-semver suffixes.

7. **Existing test that's already in place** (just for completeness): the systemd hardening regression test from 1.4.2. Same pattern: parse the unit, assert contract.

### Coverage map (end-to-end)

```
VERSION ──┬─→ DISTRO_VERSION ─┬─→ /etc/os-release VERSION_ID    ─→ release_version() ─→ display
          │                    │
          │                    ├─→ /etc/sw-versions               ─→ swupdate identify
          │                    │
          │                    └─→ /etc/swupdate.cfg identify val ─→ swupdate accept/reject
          │
          ├─→ build-swu.sh @@VERSION@@ ─→ sw-description version ─→ swupdate sw-versions check
          │
          ├─→ SWU filename ─────────────────────────────────────→ release artefact
          ├─→ SD image filename ────────────────────────────────→ release artefact
          ├─→ git tag vX.Y.Z ───────────────────────────────────→ GitHub release tag
          └─→ CHANGELOG ## [X.Y.Z] ─────────────────────────────→ release notes

Each arrow is covered by a CI check; a missed arrow is the regression class
this design protects against.
```

---

## H. Significance of each version field — plain English

| Field | Meaning | Authoritative? |
|---|---|---|
| `VERSION` (repo root) | The canonical product version. Camera + server move together. | **Yes** (repo SSOT). |
| `DISTRO_VERSION` (Yocto) | Yocto's view of the same. Always reads `VERSION`. | Derived. |
| `/etc/os-release VERSION_ID` | Runtime view of the same. Always reads `DISTRO_VERSION`. | **Yes** (image SSOT). |
| `/etc/os-release PRETTY_NAME` | Human-friendly version string for boot banner / motd. | Derived. |
| `BUILD_ID` | Same as VERSION_ID. Pinned for build reproducibility. | Derived. |
| `/etc/sw-versions` content | Used by SWUpdate's identify mechanism to decide whether a bundle is acceptable. **Not for display.** | Derived (must equal VERSION). |
| `/etc/swupdate.cfg identify` | Same role; on-device version SWUpdate matches against bundle's hardware-compatibility expectation. | Derived. |
| Heartbeat `firmware_version` | What the camera tells the server it's running. Should match VERSION_ID. | Derived (read at heartbeat-time). |
| Server `cameras.json firmware_version` | Last value the camera sent; cached until next heartbeat. | Stale-able by design. |
| Server `settings.firmware_version` | What the server itself is running. Should match VERSION_ID at startup. | Derived (read at startup). |
| Git tag `vX.Y.Z` | Marks the immutable commit that produced a release. | Derived (matches VERSION). |
| SWU filename `*-X.Y.Z-{prod,dev}.swu` | Identifies the bundle. | Derived (matches VERSION). |
| SD image filename `*-X.Y.Z-{prod,dev}.wic.bz2` | Identifies the SD-card image. | Derived (matches VERSION). |
| CHANGELOG heading `## [X.Y.Z]` | Human release-notes entry. | Derived (matches VERSION). |
| Avahi `version=1.0` TXT | mDNS *protocol* version. **Not** product version. | Independent. |
| Camera-streamer `setup.py version="1.0.0"` | Python package recipe metadata. Frozen by policy. **Not** product version. | Independent (frozen). |
| Server `setup.py version="1.0.0"` | Same. | Independent (frozen). |
| Avahi mDNS service files | Protocol-level versions, separate concept. | Independent. |
| `/etc/hwrevision` | Hardware compatibility key (e.g. `1.0` for the rpi4b platform). **Not** product version. | Independent. |

Rule of thumb: if a number can change without a release cut, it's not the product version. Avahi's `1.0`, Python pkg `1.0.0`, hwrevision `1.0` all fit this. The product version is what `VERSION` says.

---

## I. Ownership of each file/surface

| File / surface | Owner | Updated by | Updated when | Notes |
|---|---|---|---|---|
| `VERSION` (repo) | release engineer / `release.sh` | `release.sh prepare` | release cut | One source of truth. |
| `CHANGELOG.md` | release engineer / `release.sh` | `release.sh prepare` | release cut | Promotes [Unreleased] → [X.Y.Z]. |
| Git tag `vX.Y.Z` | `release.sh tag` | `release.sh tag` | after CI green on release branch | Matches VERSION. |
| GitHub release | `release.sh publish` (or manual `gh release create` from VM) | maintainer | after build + verify | Asset names mirror VERSION. |
| `meta-home-monitor/conf/distro/home-monitor.conf` | platform/build code | manual edit (rare) | distro policy change | Reads VERSION dynamically. |
| `meta-home-monitor/recipes-core/os-release/os-release.bbappend` | platform | manual edit (rare) | distro policy change | Reads `${DISTRO_VERSION}`. |
| `meta-home-monitor/recipes-core/sw-versions/sw-versions_1.0.bb` | platform | (this design) replace static install with templated do_install | once, then never | Becomes templated. |
| `meta-home-monitor/recipes-support/swupdate/files/swupdate.cfg` | platform | (this design) make `value` templatable | once | Becomes templated. |
| `/etc/os-release` (on-device) | Yocto build | every build | every release | Single image SSOT. |
| `/etc/sw-versions` (on-device) | Yocto build | every build | every release | Same value as `/etc/os-release VERSION_ID`. |
| `/etc/swupdate.cfg` (on-device) | Yocto build | every build | every release | Same. |
| Heartbeat `firmware_version` field | `camera_streamer/heartbeat.py` | runtime | each heartbeat | `release_version()` helper. |
| Server `cameras.json firmware_version` | `monitor/services/camera_service.py:accept_heartbeat` | runtime | on heartbeat ingest | Stored unchanged. |
| Server `settings.firmware_version` | `monitor/services/settings_service.py` (and startup wiring) | startup | on each server boot | Reads `release_version()` once at process start, refreshes on SIGHUP. |
| Camera local status page | `status_server.py` | runtime | each request | `release_version()`. |
| Server dashboard footer | `monitor/templates/*.html` | runtime | each request | `settings.firmware_version`. |
| Avahi mDNS TXT | `app/server/config/avahi-homemonitor.service` | static (protocol version) | rare protocol change | Independent of release version. |

---

## J. Migration plan (implementation phase — for reference, not to be done in this PR)

This section describes what the implementation PR(s) would do. Do not implement until this design is approved.

### Step 0 — load-bearing audit (prereq, no code change)

Confirm via experiment whether the swupdate daemon's accept/reject behaviour depends on the `value` in `/etc/swupdate.cfg identify` matching anything in the bundle. If yes, the templating in step 2 must be exact; if not, the templating is purely cosmetic and can be left as documentation. Read `swupdate(8)` and the daemon source (we already have the recipe checkout on the build VM); 1-hour bench experiment.

### Step 1 — template `/etc/sw-versions`

`meta-home-monitor/recipes-core/sw-versions/sw-versions_1.0.bb`:

```bb
# remove SRC_URI / static file install
do_install() {
    install -d ${D}${sysconfdir}
    echo "home-monitor ${DISTRO_VERSION}" > ${D}${sysconfdir}/sw-versions
    chmod 0644 ${D}${sysconfdir}/sw-versions
}
```

Delete `meta-home-monitor/recipes-core/sw-versions/files/sw-versions` (or keep as `sw-versions.template` for clarity, but unused).

### Step 2 — template `/etc/swupdate.cfg identify`

`meta-home-monitor/recipes-support/swupdate/files/swupdate.cfg` becomes a `.in` template; bbappend `do_install` `sed`s `@@VERSION@@` → `${DISTRO_VERSION}`.

### Step 3 — replace `_get_firmware_version()` with `release_version()`

**Camera side** (`app/camera/camera_streamer/version.py` — new file):

```python
"""Release version reader.

Single image-side source of truth: /etc/os-release VERSION_ID.
Replaces the previous _get_firmware_version() helpers in
heartbeat.py and status_server.py, both of which read
/etc/sw-versions (an SWUpdate-internal file that ships static
'1.0.0' on fresh-flashed prod images — see CHANGELOG 1.4.3 for
the bug history)."""

import os


def release_version(os_release_path: str = "/etc/os-release") -> str:
    """Return the product release version (VERSION_ID), or '' on failure."""
    try:
        with open(os_release_path) as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key == "VERSION_ID":
                    return value.strip().strip('"')
    except OSError:
        pass
    return ""
```

`heartbeat.py`: replace `_get_firmware_version()` body with `from camera_streamer.version import release_version` and `return release_version()`. Delete the dead local helper. Same for `status_server.py`.

**Server side** (`app/server/monitor/version.py` — new file): identical helper, identical logic. Justified by ADR-0006's modular-monolith boundary (no shared library across camera/server). The two helpers are five lines each and stay in lockstep via the §G test (both must produce the same output for the same `/etc/os-release`).

### Step 4 — wire server `settings.firmware_version` to `release_version()`

`app/server/monitor/services/settings_service.py` already exposes `settings.firmware_version`. Currently the value is the dataclass default `"1.0.0"` from `models.py:Settings`. Change: at server startup (`monitor/__init__.py` factory), call `release_version()` and write to `settings.firmware_version`. On SIGHUP / restart, re-read.

### Step 5 — CI tests

Add the seven checks from §G to `scripts/check_version_consistency.py` (or split into a new `scripts/check_versioning_design.py` if the existing file is too long).

Add a unit test for `release_version()` parsing (handles quoted, unquoted, missing, malformed `/etc/os-release`).

### Step 6 — hardware verification (on dev boxes)

1. Hot-deploy the changed `camera_streamer/heartbeat.py` + `status_server.py` + `version.py` to `.115` and `.148` (already on 1.4.1-dev-migrate). Restart camera-streamer. Verify the camera's local page shows `1.4.1` instead of `1.0.0`. Verify the server's dashboard updates `firmware_version` to `1.4.1` on the next heartbeat.
2. On the server (`.245` on 1.4.1), hot-deploy the new helper + settings wiring. Restart `monitor.service`. Verify dashboard footer shows `1.4.1`.

### Step 7 — full image build + OTA test

1. Cut as 1.4.3.
2. Build dev + prod on the VM.
3. OTA `.186` (currently on 1.4.2 prod) to 1.4.3 prod. Verify on the camera's UI: firmware shows `1.4.3`. Verify the server records `firmware_version="1.4.3"` for the camera.

### Step 8 — fresh-flash test (the bug's original symptom)

1. Flash `camera-image-1.4.3-prod.wic.bz2` to a fresh SD card.
2. Boot, pair to the server.
3. Verify the camera's local status page shows `Firmware: 1.4.3` (not `1.0.0`).
4. Verify the server records `firmware_version="1.4.3"`.

That step 8 is the goalpost — when both displays read `1.4.3` on a fresh-flashed image with no prior OTA, the bug is fixed end-to-end.

---

## Open questions for review

1. **Decoupled vs single-version** — is the proposal to keep camera and server in lockstep on the same `VERSION` correct? Edge cases: an emergency camera-only fix that doesn't need a server rebuild. With single-version, that still requires bumping the server's version too (cosmetic-only build). Acceptable cost?

2. **`/etc/sw-versions` retention** — should we keep the file at all (templated) for SWUpdate's identify mechanism, or strip it once the load-bearing audit (step 0) confirms swupdate doesn't actually require it? Smaller image surface if we strip it.

3. **`release_version()` duplication across camera and server** — five lines in each, kept in lockstep by a CI test. Acceptable, or is there a shared-utility pattern we should introduce that isn't a full shared package?

4. **Server `settings.firmware_version` lifecycle** — read once at startup, or every dashboard render? Once-at-startup matches the camera-side heartbeat pattern (read fresh each time). Pick the same pattern: read fresh each time the dashboard renders the footer. Negligible cost (one syscall per page render).

5. **Pre-release tag handling** — extend `release.sh validate_version` to accept full semver pre-release? Or stay strict `X.Y.Z` and treat `dev-migrate`-style suffixes as out-of-band (manual `gh release` only)?

6. **Mismatched-version warning UX** — today the dashboard already warns when `camera.firmware_version != settings.firmware_version`. Confirm this still works once both reads point at `release_version()`. Add a regression test.

---

## What this design does NOT do

- Doesn't change which Python package versions are reported (camera-streamer's `setup.py` and monitor-server's `setup.py` stay frozen at `1.0.0` per the existing policy in `check_version_consistency.py`).
- Doesn't introduce a new shared library between camera and server (preserves ADR-0006 modular-monolith boundary).
- Doesn't change SWU bundle naming or signing posture (those are correct already as of 1.4.1).
- Doesn't change the OTA flow (slot-swap, post-install hook semantics unchanged).
- Doesn't change CHANGELOG format or release-tooling UX. Just adds checks.

---

## Sign-off needed before implementation

- [ ] Decision on §C — `/etc/os-release` vs `/etc/sw-versions` as image SSOT
- [ ] Decision on §J step 0 — load-bearing audit of swupdate.cfg's identify block
- [ ] Decision on Open Q 1 (lockstep), Q 2 (sw-versions retention), Q 3 (helper duplication)
- [ ] Approval of the §G CI guardrail set
