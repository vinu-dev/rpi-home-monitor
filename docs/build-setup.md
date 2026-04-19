# Build Machine Setup

Version: 1.2
Date: 2026-04-09

How to set up a fresh machine to build Home Monitor OS images.

For the full operator flow after the machine is ready, including release,
recovery, and OTA signing steps, use
[Release Operator Runbook](./release-runbook.md).

---

## 1. Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| OS | Ubuntu 22.04 or 24.04 LTS | Ubuntu 24.04 LTS |
| CPU | 4 cores | 8+ cores |
| RAM | 16 GB | 32 GB |
| Disk | 100 GB free | 200 GB+ SSD |
| Swap | 4 GB | 8 GB |
| Internet | Required for first build | Cached after first build |

GCP, AWS, or a local VM all work. The setup script handles everything.

---

## 2. One-Command Setup

```bash
git clone git@github.com:vinu-dev/rpi-home-monitor.git ~/yocto
cd ~/yocto
./scripts/setup-env.sh
```

This single script:
1. Installs all Yocto build dependencies (apt packages)
2. Installs Python test dependencies (pytest, pytest-cov)
3. Sets the locale to `en_US.UTF-8`
4. Creates an 8 GB swap file if one is not already present
5. Fixes the Ubuntu 24.04 AppArmor restriction for bitbake

After it completes, you are ready to build.

---

## 3. What Gets Installed

### System Packages (apt)

```text
gawk wget git diffstat unzip texinfo gcc build-essential chrpath socat
cpio python3 python3-pip python3-pexpect xz-utils debianutils iputils-ping
python3-git python3-jinja2 libegl1 libsdl1.2-dev pylint xterm
python3-subunit mesa-common-dev zstd liblz4-tool file locales
lz4 libacl1
```

These are the [Yocto Project required packages](https://docs.yoctoproject.org/ref-manual/system-requirements.html#required-packages-for-the-build-host) for Ubuntu.

### Python Test Packages (pip)

```text
pytest >= 8.0
pytest-cov >= 5.0
flask >= 3.0
bcrypt >= 4.0
```

Installed automatically by the setup script for running unit tests.

---

## 4. Building Images

### 4.1 Build Commands

```bash
# Development images (debug-tweaks, root SSH, dev tools)
./scripts/build.sh server-dev      # RPi 4B
./scripts/build.sh camera-dev      # RPi Zero 2W

# Production images (hardened, no root password, no debug)
./scripts/build.sh server-prod     # RPi 4B
./scripts/build.sh camera-prod     # RPi Zero 2W

# Both boards at once
./scripts/build.sh all-dev
./scripts/build.sh all-prod
```

### 4.2 What the Build Script Does

1. Clones Yocto layers (poky, meta-raspberrypi, meta-openembedded) if not present
2. Checks out the `scarthgap` branch on all layers
3. Sources the Yocto build environment
4. Copies the correct `local.conf` and `bblayers.conf` into the build directory
5. Sets CPU thread count for parallel build
6. Runs `bitbake` to build the image

### 4.3 Build Output

| Target | Image Location |
|--------|---------------|
| server-dev | `build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-dev-*.wic.bz2` |
| server-prod | `build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-prod-*.wic.bz2` |
| camera-dev | `build-zero2w/tmp-glibc/deploy/images/raspberrypi0-2w-64/home-camera-image-dev-*.wic.bz2` |
| camera-prod | `build-zero2w/tmp-glibc/deploy/images/raspberrypi0-2w-64/home-camera-image-prod-*.wic.bz2` |

### 4.4 Build Times

| Scenario | Time |
|----------|------|
| First build (server) | 2-4 hours |
| Second board (camera) | 30-60 min (shared sstate-cache) |
| Rebuild after app change | 5-15 min |
| Rebuild after config-only change | 2-5 min |

---

## 5. Custom Distro: `home-monitor`

We use a custom distribution instead of the reference `poky` distro. This is industry best practice for product development.

**What the distro controls** (in `meta-home-monitor/conf/distro/home-monitor.conf`):
- Init system: systemd (not sysvinit)
- Core features: usrmerge, WiFi, seccomp, PAM, zeroconf
- Package format: deb
- License policy: commercial + firmware blobs accepted
- Version pinning: kernel 6.6.x, Python 3.12.x, OpenSSL 3.5.x
- Build settings: SPDX license manifests, rm_work

**What local.conf controls** (machine-specific only):
- `MACHINE` - which board or project-owned machine variant to build for
- `GPU_MEM` - GPU memory split
- `MACHINE_EXTRA_RRECOMMENDS` - WiFi firmware for a specific chip
- CPU threads for parallel build

### Multi-Machine Build

Both boards share `bblayers.conf` and the `home-monitor` distro. Only `local.conf` differs:

```text
config/bblayers.conf                shared (identical layers for both)
config/rpi4b/local.conf             MACHINE="raspberrypi4-64", GPU_MEM=128
config/rpi4b/local.conf.prod        require local.conf; SWUPDATE_SIGNING="1"
config/zero2w/local.conf            MACHINE="home-monitor-camera", GPU_MEM=64
config/zero2w/local.conf.prod       require local.conf; SWUPDATE_SIGNING="1"
```

Prod configs **inherit** the dev config via `require local.conf` and override
only the signing policy. That way a change to machine/kernel settings in the
dev `local.conf` propagates to prod automatically — no drift between the two.
`build.sh server-prod` / `camera-prod` / `all-prod` select the prod layer.
See [ADR-0014](adr/0014-swupdate-signing-dev-prod.md) for the signing contract
and [OTA Key Management](ota-key-management.md) for the per-user keypair policy.

The `home-monitor-camera` machine in `meta-home-monitor/conf/machine/`
extends `raspberrypi0-2w-64` and carries the permanent OV5647 sensor
policy for the PiHut ZeroCam.

Yocto still publishes the final camera image artifacts under the upstream
`raspberrypi0-2w-64` deploy directory, so use that path when collecting
`.wic.bz2` and rootfs outputs from the build VM.

Shared `downloads/` and `sstate-cache/` mean the second board reuses most compiled artifacts.

---

## 6. Dev vs Production Images

| Feature | Dev Image | Prod Image |
|---------|-----------|------------|
| Root login | Yes (no password) | No (locked) |
| SSH | Root SSH open | Key-only SSH |
| Debug tools | gdb, strace, tcpdump | None |
| debug-tweaks | Enabled | Disabled |
| First-boot wizard | Skipped | Required |
| Use case | Development, testing | Real devices |

---

## 7. Development Workflow

### Fast iteration (app changes - seconds)

```bash
rsync -av app/server/monitor/ root@<rpi4b-ip>:/opt/monitor/monitor/
ssh root@<rpi4b-ip> systemctl restart monitor
```

### Full image rebuild (OS/package changes)

```bash
./scripts/build.sh server-dev
```

---

## 8. Useful Commands

```bash
# Build environments
source poky/oe-init-build-env build          # server
source poky/oe-init-build-env build-zero2w   # camera

# Rebuild specific packages
bitbake monitor-server -c cleansstate && bitbake home-monitor-image-dev
bitbake camera-streamer -c cleansstate && bitbake home-camera-image-dev

# Run unit tests
cd app/server && pytest
cd app/camera && pytest

# On the device
systemctl status monitor
journalctl -u monitor -f
cat /etc/os-release                          # Shows "Home Monitor OS 1.0.0"
nmcli device wifi connect "SSID" password "pass"
```

---

## 9. Troubleshooting

### Build fails with "No space left on device"

Yocto builds need about 100 GB. Free up disk or resize your VM disk.

```bash
# Check disk usage
df -h
# Clean old build artifacts
cd ~/yocto/build && rm -rf tmp/work
```

### Build fails with "sanity check" errors

Missing host packages. Re-run the setup script:

```bash
./scripts/setup-env.sh
```

### Build fails with AppArmor error (Ubuntu 24.04)

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
# Make persistent:
echo "kernel.apparmor_restrict_unprivileged_userns=0" | sudo tee /etc/sysctl.d/99-yocto.conf
```

The setup script does this automatically.

### Production signing note

Production builds do not require a repo-committed signing certificate.
Instead, `./scripts/build.sh` stages the operator's local certificate from
`~/.monitor-keys/ota-signing.crt` into an ignored generated path before the
Yocto build starts.

### Slow builds

- Increase CPU cores by editing `BB_NUMBER_THREADS` and `PARALLEL_MAKE` in `local.conf`
- Add more RAM or swap
- Use an SSD, not an HDD
- Do not run other heavy processes during the build

### "do_fetch" failures

Network issues downloading source tarballs. Retry:

```bash
bitbake home-monitor-image-dev
```

Bitbake resumes where it left off. Downloaded sources are cached in `downloads/`.
