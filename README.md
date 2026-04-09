# RPi Home Monitor - Yocto Build System

Custom Linux images for a Raspberry Pi home security camera system.

## System Overview

Two Raspberry Pi boards work together:

```
RPi Zero 2W + ZeroCam          RPi 4 Model B (Server)
  (camera node)          --->    (storage + web UI)
  Captures video                 Receives RTSP streams
  Streams via RTSP               Records & stores video
  Runs: camera-streamer          Serves mobile web dashboard
                                 Runs: monitor-server + nginx
```

Access the web dashboard from any phone/browser at `http://<server-ip>/`.

## Repository Structure

```
.
├── config/
│   ├── bblayers.conf            # Shared layer config (both boards)
│   ├── rpi4b/
│   │   └── local.conf           # RPi 4B server build config
│   └── zero2w/
│       └── local.conf           # RPi Zero 2W camera build config
├── meta-home-monitor/           # Custom Yocto layer (committed)
│   ├── conf/
│   │   └── layer.conf           # Layer definition
│   ├── recipes-core/images/
│   │   ├── home-monitor-image.bb    # Server image recipe
│   │   └── home-camera-image.bb     # Camera image recipe
│   ├── recipes-monitor/
│   │   └── monitor-server/      # Flask web app + nginx + systemd
│   │       ├── monitor-server_1.0.bb
│   │       └── files/
│   │           ├── app.py           # Flask monitoring server
│   │           ├── templates/
│   │           │   └── index.html   # Mobile-friendly dashboard
│   │           ├── monitor.service  # systemd unit
│   │           ├── nginx-monitor.conf
│   │           └── record.sh        # Recording cleanup cron
│   └── recipes-camera/
│       └── camera-streamer/     # RTSP camera streaming service
│           ├── camera-streamer_1.0.bb
│           └── files/
│               ├── camera-stream.sh     # ffmpeg RTSP streamer
│               ├── camera.conf          # Camera settings (edit after flash)
│               └── camera-streamer.service
├── scripts/
│   ├── setup-env.sh             # One-time host setup (Ubuntu 24.04)
│   └── build.sh                 # Build script for both boards
├── poky/                        # (cloned at build time, gitignored)
├── meta-raspberrypi/            # (cloned at build time, gitignored)
├── meta-openembedded/           # (cloned at build time, gitignored)
├── downloads/                   # (shared download cache, gitignored)
└── sstate-cache/                # (shared build cache, gitignored)
```

**What's in git:** `config/`, `meta-home-monitor/`, `scripts/` -- everything you wrote.
**What's NOT in git:** Upstream layers, build dirs, caches, images -- cloned/generated at build time.

Both boards share the same `downloads/` and `sstate-cache/` directories, so common packages (kernel toolchain, glibc, systemd, etc.) are only downloaded and compiled once.

## Quick Start

### 1. Set up a build machine

Recommended: Ubuntu 24.04 VM with 8+ cores, 32GB RAM, 200GB disk.

```bash
git clone git@github.com:vinu-engineer/rpi-home-monitor.git ~/yocto
cd ~/yocto
./scripts/setup-env.sh
```

If on Ubuntu 24.04, you also need:
```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

### 2. Build images

```bash
# Build the RPi 4B server image
./scripts/build.sh server

# Build the RPi Zero 2W camera image
./scripts/build.sh camera

# Build both
./scripts/build.sh all
```

First build takes 2-4 hours (depends on CPU). Second board is much faster since the sstate-cache is shared.

### 3. Find the images

After build completes:

| Board | Image location |
|-------|---------------|
| RPi 4B | `build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-raspberrypi4-64.rootfs.wic.bz2` |
| Zero 2W | `build-zero2w/tmp/deploy/images/raspberrypi0-2w-64/home-camera-image-raspberrypi0-2w-64.rootfs.wic.bz2` |

### 4. Flash to SD card

```bash
# RPi 4B server
bzcat build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-*.wic.bz2 \
  | sudo dd of=/dev/sdX bs=4M status=progress

# RPi Zero 2W camera
bzcat build-zero2w/tmp/deploy/images/raspberrypi0-2w-64/home-camera-image-*.wic.bz2 \
  | sudo dd of=/dev/sdY bs=4M status=progress
```

On Windows, use [balenaEtcher](https://etcher.balena.io/) -- decompress the `.wic.bz2` first, then flash the `.wic` file.

## How Multi-Machine Builds Work

Both boards use the **same Yocto layers** and **same `bblayers.conf`**. The only difference is `local.conf` which sets `MACHINE` and board-specific options.

```
config/bblayers.conf        <-- shared (identical layers for both)
config/rpi4b/local.conf     <-- MACHINE="raspberrypi4-64", GPU_MEM=128, 1GB extra space
config/zero2w/local.conf    <-- MACHINE="raspberrypi0-2w-64", GPU_MEM=64, 256MB extra space
```

Each board gets its own build directory (`build/` vs `build-zero2w/`) but they share:
- `downloads/` -- source tarballs (fetched once)
- `sstate-cache/` -- compiled artifacts (native tools compiled once, reused)

This means the second build reuses most of the heavy compilation (gcc, glibc, python3, etc.).

## Useful Commands

All commands assume you're in `~/yocto`.

```bash
# Enter the server build environment (needed for bitbake commands)
source poky/oe-init-build-env build

# Enter the camera build environment
source poky/oe-init-build-env build-zero2w

# Rebuild after changing a recipe
bitbake monitor-server          # just the server app
bitbake camera-streamer         # just the camera streamer
bitbake home-monitor-image      # full server image
bitbake home-camera-image       # full camera image

# Rebuild a single package from scratch
bitbake -c cleansstate monitor-server && bitbake monitor-server

# Check what packages are in an image
bitbake -e home-monitor-image | grep ^IMAGE_INSTALL=

# List all available recipes
bitbake-layers show-recipes | grep -i camera

# Check image size
ls -lh build/tmp/deploy/images/raspberrypi4-64/*.wic.bz2

# Check disk usage
du -sh build/ build-zero2w/ downloads/ sstate-cache/
```

## Modifying the Application

### Server app (RPi 4B)

Edit files under `meta-home-monitor/recipes-monitor/monitor-server/files/`:

| File | Purpose |
|------|---------|
| `app.py` | Flask server -- camera management, recording, REST API |
| `templates/index.html` | Web dashboard (mobile-friendly, dark theme) |
| `nginx-monitor.conf` | Reverse proxy config (port 80 -> Flask 5000) |
| `monitor.service` | systemd unit (auto-starts on boot) |
| `record.sh` | Cleanup cron (7-day video, 3-day snapshot retention) |

After editing, rebuild:
```bash
source poky/oe-init-build-env build
bitbake monitor-server -c cleansstate && bitbake home-monitor-image
```

### Camera streamer (Zero 2W)

Edit files under `meta-home-monitor/recipes-camera/camera-streamer/files/`:

| File | Purpose |
|------|---------|
| `camera-stream.sh` | ffmpeg capture + RTSP stream to server |
| `camera.conf` | Server IP, resolution, FPS (edit on device after flash) |
| `camera-streamer.service` | systemd unit (auto-starts on boot) |

After editing, rebuild:
```bash
source poky/oe-init-build-env build-zero2w
bitbake camera-streamer -c cleansstate && bitbake home-camera-image
```

### Adding new packages

1. Add the package name to the image recipe (`.bb` file under `recipes-core/images/`)
2. Rebuild the image: `bitbake home-monitor-image` or `bitbake home-camera-image`

Example -- add `vim` to the server:
```bash
# In meta-home-monitor/recipes-core/images/home-monitor-image.bb, add:
IMAGE_INSTALL += " vim "
```

## Post-Flash Configuration

### RPi 4B Server
- Default login: `root` (no password, debug-tweaks enabled)
- Web UI: `http://<server-ip>/` (port 80)
- SSH: `ssh root@<server-ip>`
- Monitor service: `systemctl status monitor`
- Recordings: `/opt/monitor/recordings/`
- Snapshots: `/opt/monitor/snapshots/`

### RPi Zero 2W Camera
- Default login: `root` (no password)
- SSH: `ssh root@<camera-ip>`
- Edit camera config: `nano /opt/camera/camera.conf` (set SERVER_IP to your RPi 4B's IP)
- Restart stream: `systemctl restart camera-streamer`
- Check stream: `systemctl status camera-streamer`

### WiFi Setup (both boards)
```bash
nmcli device wifi connect "YourSSID" password "YourPassword"
```

## Build Configuration Details

### Yocto Release
- **Scarthgap** (5.0 LTS) -- latest stable long-term support

### Server Image Packages
Base: openssh, wpa-supplicant, networkmanager, systemd
Video: ffmpeg, gstreamer (base/good/bad/libav), v4l-utils
Web: nginx, python3-flask, python3-jinja2
Utils: htop, nano, curl, wget, rsync, cronie, logrotate

### Camera Image Packages
Base: openssh, wpa-supplicant, networkmanager, systemd
Camera: ffmpeg, v4l-utils, libcamera, libcamera-apps
Utils: htop, nano, curl

## Troubleshooting

```bash
# Build fails with "No space left on device"
df -h /home       # need ~100GB free for both builds
du -sh sstate-cache/ downloads/

# Build fails with AppArmor error (Ubuntu 24.04)
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0

# Build fails with missing layer
bitbake-layers show-layers    # verify all layers are detected

# Check build errors
cat build/tmp/log/cooker/raspberrypi4-64/console-latest.log | tail -100

# Clean everything and start fresh (nuclear option)
rm -rf build/tmp build-zero2w/tmp
# Keep downloads/ and sstate-cache/ to avoid re-downloading
```
