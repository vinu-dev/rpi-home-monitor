# `feat/server-docker-runtime` — Server image with container runtime

> **Branch status:** experimental. **DO NOT** ship via the production
> OTA path. The `.swu` produced from this branch is intended to be
> manually flashed to a dev RPi 4B for evaluation only.

## What

Adds an opt-in container runtime to the **server** image (RPi 4B) so users
can run optional add-on apps (Claude automation, RADIUS, generic
docker-compose stacks) without modifying the core monitor stack.

The **camera** image (RPi Zero 2W) is untouched. 176 MB usable RAM does
not fit a container runtime.

## Decision: Podman, not Docker

| Criterion | Docker (docker-moby) | Podman | Winner |
| --- | --- | --- | --- |
| Idle RAM | ~80–150 MB resident dockerd | ~0 (no daemon) | Podman |
| Daemon attack surface | root daemon w/ socket | none (rootless by default) | Podman |
| Systemd integration | runs alongside systemd | native (`generate systemd`) | Podman |
| `docker` CLI compat | first class | `podman-docker` shim provides `/usr/bin/docker` | tie |
| `docker-compose` support | first class | `podman compose` (built-in) | tie |
| Ecosystem familiarity | huge | growing | Docker |
| Memory headroom on Pi 4B (4 GB) | tight after monitor stack | comfortable | Podman |

We chose **Podman**. The RAM and attack-surface wins matter more for a
24/7 home appliance than ecosystem familiarity, and `podman-docker`
provides enough CLI compatibility that user docker-compose files just
work.

## What changed in `meta-home-monitor`

```
config/bblayers.conf
    + meta-virtualization
    + meta-openembedded/meta-filesystems   (LAYERDEPENDS of meta-virt)

config/rpi4b/local.conf
    + DISTRO_FEATURES:append = " virtualization"
    (deliberately NOT in meta-home-monitor/conf/distro/home-monitor.conf
    because the camera build shares that file)

meta-home-monitor/recipes-core/images/home-monitor-image.inc
    + IMAGE_INSTALL of podman, podman-docker, cni-plugins, fuse-overlayfs,
      slirp4netns, conmon, crun, netavark, aardvark-dns, hm-stacks
      (gated on `virtualization` in DISTRO_FEATURES)
    + IMAGE_ROOTFS_EXTRA_SPACE:append:raspberrypi4-64 = " + 262144"

meta-home-monitor/recipes-kernel/linux/linux-raspberrypi/docker.cfg
    NEW kernel config fragment (cgroupv2, namespaces, overlayfs,
    bridge-netfilter, FUSE, seccomp). Pulled in only on raspberrypi4-64
    via SRC_URI:append:raspberrypi4-64 in the existing bbappend.

meta-home-monitor/recipes-extended/podman/
    NEW files:
      podman_%.bbappend            — drops in /etc/containers/storage.conf
                                     pinning graphroot to /data/containers
                                     and a tmpfiles.d entry to mkdir it
      files/storage.conf
      files/hm-containers.tmpfiles.conf
      hm-stacks_1.0.bb             — ships the systemd units below
      files/hm-stacks@.service     — template, one instance per stack
      files/hm-stacks-restore.service
      files/hm-stacks-restore.sh
      README.md                    — operator-facing convention docs
```

## How to deploy this image (manual, NOT the OTA path)

The production OTA path (`/scripts/build-swu.sh --sign` → GitHub releases
→ device pulls signed `.swu`) is reserved for tagged production releases.
This branch produces an **unsigned dev `.swu`**. To install it on a dev
server:

```sh
# On the build VM:
ls -la /home/vinu_emailme/exp-server-docker/server-update-docker-ready-*.swu

# Copy to the target dev server:
scp .../server-update-docker-ready-YYYYMMDD.swu pi@<server-ip>:/tmp/

# On the target (must be a dev server with SWUPDATE_SIGNING=0 in its
# fstab — production servers will reject unsigned bundles):
ssh pi@<server-ip>
sudo swupdate -v -i /tmp/server-update-docker-ready-YYYYMMDD.swu
sudo reboot

# After reboot the device boots into the *other* slot. Verify:
podman --version
ls /etc/containers/storage.conf
systemctl list-unit-files | grep hm-stacks
```

## How to roll back

`swupdate` is A/B. The previous (1.3.1) rootfs is still on the inactive
slot. To revert:

```sh
# Inspect current/inactive slot from u-boot env:
sudo fw_printenv mender_boot_part 2>/dev/null || sudo fw_printenv BOOT_ORDER

# Force a single boot of the other slot:
sudo fw_setenv BOOT_ORDER "B A"   # or vice-versa, depending on current
sudo reboot
```

If the dev image bricks the device, factory-flash the production v1.3.1
`.swu` from `/home/vinu_emailme/ota-dev-server/server-update-v1.3.1.swu`
via SD-card re-imaging (the wic.bz2 in
`/home/vinu_emailme/ota-dev-server/build/tmp-glibc/deploy/images/raspberrypi4-64/`
is the canonical recovery artefact).

## Verifying camera isolation

The bblayers change adds `meta-virtualization` to `BBLAYERS`, but layer
parsing alone does NOT install packages, change rootfs contents, or alter
kernel config. Container packages and DISTRO_FEATURES are gated as
follows:

| Gate | Where | Effect on camera (`home-monitor-camera`) |
| --- | --- | --- |
| `DISTRO_FEATURES:append = " virtualization"` | `config/rpi4b/local.conf` | Not present in `config/zero2w/local.conf` → no-op for camera |
| `IMAGE_INSTALL` of podman et al. | `home-monitor-image.inc` (server only) | Camera uses `home-camera-image.inc` → no-op |
| `SRC_URI += docker.cfg` | `linux-raspberrypi_%.bbappend` with `:raspberrypi4-64` override | Camera MACHINE is `home-monitor-camera` → no-op |

`bitbake -e home-camera-image-dev` should produce identical `IMAGE_INSTALL`
and `DISTRO_FEATURES` values vs. the `ota-dev-camera` baseline.
