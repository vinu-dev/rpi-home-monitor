# Hardware Setup Guide

Version: 1.0
Date: 2026-04-09

This guide covers what to buy, how to assemble, and how to flash your
RPi Home Monitor system. Follow it step by step.

---

## 1. Shopping List

### 1.1 Server (RPi 4 Model B)

| Item | Spec | Notes |
|------|------|-------|
| Raspberry Pi 4 Model B | **4GB RAM** (minimum) | 8GB recommended if running multiple cameras |
| MicroSD card | 32GB+ Class 10 / A2 | Holds the OS (rootfs + boot). 32GB is plenty |
| USB SSD or HDD | 256GB+ | **Strongly recommended** for recording storage. MicroSD wears out under constant writes |
| USB 3.0 to SATA adapter | For USB SSD | Get one with UASP support for best throughput |
| Official RPi 4 power supply | USB-C, 5.1V / 3A (15W) | **Use the official PSU.** Cheap chargers cause undervoltage and random crashes |
| RPi 4 case with fan | Passive or active cooling | The server runs 24/7 — a heatsink-only case works but a fan is safer |
| Ethernet cable (Cat5e+) | 1m or longer | **Wired connection strongly recommended** for the server |
| MicroSD card reader | USB-A or USB-C | For flashing the image from your PC |

**Optional but recommended:**
| Item | Notes |
|------|-------|
| RPi 4 PoE+ HAT | Powers the Pi over Ethernet — one less cable |
| UPS HAT or mini UPS | Keeps the server alive during brief power cuts |

### 1.2 Camera Node (RPi Zero 2W + ZeroCam)

Buy one set per camera location.

| Item | Spec | Notes |
|------|------|-------|
| Raspberry Pi Zero 2W | ARM Cortex-A53, 512MB RAM | Enough CPU for 1080p encoding (WiFi is 2.4GHz only — 802.11 b/g/n) |
| PiHut ZeroCam (or RPi Camera Module 3) | CSI ribbon cable, 1080p | Any RPi-compatible CSI camera works. ZeroCam is compact and affordable |
| CSI ribbon cable | **22-pin to 22-pin** for Zero 2W | The Zero uses a **smaller CSI connector** than the full-size Pi. Most cameras ship with the wrong cable |
| MicroSD card | 16GB+ Class 10 | Camera OS is small (~112MB). 16GB is more than enough |
| Official RPi Zero power supply | Micro-USB, 5V / 2.5A | Or any quality 5V/2.5A micro-USB PSU |
| Zero 2W case | With camera cutout | Needs a hole for the CSI cable or lens |
| MicroSD card reader | USB-A or USB-C | For flashing |

**Optional:**
| Item | Notes |
|------|-------|
| Camera mount / bracket | 3D-printed or bought — for wall/ceiling mounting |
| Longer CSI ribbon cable | 30cm or 60cm if the camera needs to be separated from the board |
| Weatherproof enclosure | For outdoor cameras (must be IP65+ rated) |

### 1.3 Where to Buy

| Store | URL |
|-------|-----|
| Raspberry Pi Official | https://www.raspberrypi.com/products/ |
| The Pi Hut | https://thepihut.com |
| Pimoroni | https://shop.pimoroni.com |
| Adafruit | https://www.adafruit.com |
| Amazon | Search by exact part name |

**Tip:** RPi stock can be limited. Check https://rpilocator.com for real-time availability.

---

## 2. Assembly

### 2.1 Server (RPi 4B)

```
                    Ethernet (to router)
                        │
┌───────────────────────┤
│   Raspberry Pi 4B     │
│                       │
│  ┌─────┐   ┌─────┐   │  USB 3.0 ──── USB SSD (recordings)
│  │HDMI0│   │HDMI1│   │
│  └─────┘   └─────┘   │  USB 2.0 ──── (keyboard, for initial setup only)
│                       │
│  microSD slot (bottom)│  USB-C ────── Power supply (5.1V/3A)
└───────────────────────┘
```

**Steps:**

1. **Insert the microSD card** into the slot on the underside of the Pi 4B.
   - The card clicks in. Contacts face the PCB.
   - Don't flash the image yet — see Section 3.

2. **Attach the case and heatsinks.**
   - Apply thermal pads or heatsink to the CPU (the large silver chip).
   - If your case has a fan, connect it to GPIO pins 4 (+5V) and 6 (GND).

3. **Connect Ethernet cable** from the Pi to your router/switch.
   - WiFi works but wired is much more reliable for a 24/7 server receiving video streams.

4. **Connect USB SSD** (optional but recommended).
   - Plug into one of the **blue USB 3.0 ports** (the ones closer to the Ethernet jack).
   - The SSD will be mounted at `/data` and used for all recordings.
   - If no USB SSD: recordings go to the microSD card (will wear out faster).

5. **Do NOT connect HDMI or keyboard** unless you need them for debugging.
   - The system is headless — all management is via the web dashboard or SSH.

6. **Connect the power supply last.**
   - Plug USB-C into the Pi. It boots automatically.

### 2.2 Camera Node (Zero 2W + ZeroCam)

```
┌─────────────────────┐
│  RPi Zero 2W        │
│                     │
│  ┌───┐  CSI ─────── ZeroCam (camera module)
│  │   │              │
│  └───┘              │
│                     │
│  micro-USB (PWR)    │  ◄── Power supply (5V/2.5A)
│  micro-USB (DATA)   │  ◄── Not used (OTG port)
│  mini-HDMI          │  ◄── Not used (headless)
│  microSD (bottom)   │
└─────────────────────┘
```

**Steps:**

1. **Connect the CSI ribbon cable to the camera module.**
   - Lift the black plastic clip on the camera module's connector.
   - Slide the ribbon cable in — **contacts facing the PCB** (blue backing faces you).
   - Push the clip down to lock it.

2. **Connect the CSI ribbon cable to the Zero 2W.**
   - The Zero 2W has a **smaller CSI connector** (22-pin) between the HDMI and the corner.
   - Lift the clip, insert cable (contacts toward PCB), close clip.
   - **Important:** Use the correct cable. The Zero needs a 22-pin-to-22-pin cable. Standard RPi cameras ship with a 15-pin-to-22-pin cable — you may need an adapter or the right cable.

3. **Insert the microSD card** (underside of the board). Don't flash yet.

4. **Put in the case.** Route the CSI cable through the camera slot.

5. **Connect power last.** Use the **PWR** micro-USB port (the one closest to the corner, furthest from the HDMI port). The other micro-USB is for data/OTG and is not used.

---

## 3. Flash the OS Image

### 3.1 Download or Build

**Option A: Download pre-built images**

Go to [GitHub Releases](https://github.com/vinu-dev/rpi-home-monitor/releases) and download:
- `home-monitor-image-dev-*.wic.bz2` — Server (RPi 4B) development image
- `home-camera-image-dev-*.wic.bz2` — Camera (Zero 2W) development image

For production:
- `home-monitor-image-prod-*.wic.bz2` — Server production (hardened)
- `home-camera-image-prod-*.wic.bz2` — Camera production (hardened)

**Option B: Build from source**

See [README.md](../README.md) for build instructions. You need a Linux build machine
(Ubuntu 24.04, 8+ cores, 32GB RAM, 200GB disk).

```bash
./scripts/build.sh server-dev      # Server dev image
./scripts/build.sh camera-dev      # Camera dev image
```

### 3.2 Flash to MicroSD Card

**Linux / macOS:**

```bash
# Find your SD card device (DO NOT use your system disk!)
lsblk

# Flash (replace /dev/sdX with your SD card)
bzcat home-monitor-image-dev-raspberrypi4-64.wic.bz2 | sudo dd of=/dev/sdX bs=4M status=progress
sync
```

**Windows:**

1. Decompress the `.wic.bz2` file using [7-Zip](https://7-zip.org/) to get a `.wic` file.
2. Flash with [balenaEtcher](https://etcher.balena.io/):
   - Select the `.wic` file
   - Select your SD card
   - Click "Flash!"

**Repeat for each board** — server image on the server's SD card, camera image on the camera's SD card.

**WARNING:** Double-check the target device. `dd` and Etcher will overwrite anything without asking. Select the correct SD card, not your system drive.

---

## 4. First Boot — Server (RPi 4B)

### 4.1 Power On

1. Insert the flashed microSD into the RPi 4B.
2. Connect Ethernet.
3. Connect USB SSD (if using).
4. Plug in power. Wait 30-60 seconds for first boot.

### 4.2 Find the Server on Your Network

The server advertises itself via mDNS. From any computer on the same LAN:

```bash
# Linux/macOS
ping home-monitor.local

# If mDNS doesn't work, check your router's DHCP lease table for the Pi's IP
# Or connect a monitor + keyboard temporarily and run:
ip addr show eth0
```

### 4.3 SSH In (Dev Image Only)

```bash
ssh root@home-monitor.local
# Dev image: no password required (debug-tweaks enabled)
```

### 4.4 Verify the System

```bash
# Check OS branding
cat /etc/os-release
# Should show: Home Monitor OS 1.0.0

# Check services
systemctl status monitor
systemctl status nginx

# Check network
ip addr
nmcli device status

# Check storage
lsblk
df -h
```

### 4.5 Connect WiFi (If Not Using Ethernet)

```bash
# Scan for networks
nmcli device wifi list

# Connect
nmcli device wifi connect "YOUR_SSID" password "YOUR_PASSWORD"

# Verify
nmcli connection show
```

### 4.6 Set Up USB SSD for Recordings (Optional)

If you connected a USB SSD for recording storage:

```bash
# Find the SSD
lsblk
# Should appear as /dev/sda

# The system auto-formats and mounts the /data partition on first boot.
# If it doesn't, manually set up:
mkfs.ext4 -L data /dev/sda1
mount /dev/sda1 /data

# Verify
ls /data/
# Should contain: recordings/ config/ certs/ logs/ live/
```

### 4.7 Access the Web Dashboard

From your phone or laptop browser:

```
https://home-monitor.local
```

- **Accept the self-signed certificate warning** (the system generates its own CA on first boot).
- **Production images** will prompt a first-boot setup wizard (create admin account, set timezone, set hostname).
- **Dev images** have a pre-configured admin account for testing.

---

## 5. First Boot — Camera (RPi Zero 2W)

### 5.1 Power On

1. Insert the flashed microSD into the Zero 2W.
2. Verify the CSI cable is seated properly.
3. Plug in power. Wait 30-60 seconds.

### 5.2 Connect Camera to WiFi

On first boot, the camera starts a WiFi hotspot for provisioning:

1. Connect your phone to WiFi: **HomeCam-Setup** (password: `homecamera`)
2. The setup wizard opens automatically (captive portal). If not, go to `http://10.42.0.1`
3. Enter your **home WiFi SSID** and **password**
4. Enter the **server address** (default: `rpi-divinu.local`)
5. Set **camera login credentials** — username (default: `admin`) and password (min 4 characters). These protect the camera's status page.
6. Click **Save & Connect**
7. The camera LED changes: fast blink (connecting) → solid (connected)
8. On success, the wizard shows the camera's `.local` URL (e.g., `https://rpi-divinu-cam-d8ee.local`)

### 5.3 Verify Camera Hardware

```bash
# Check if the camera is detected
v4l2-ctl --list-devices
# Should show /dev/video0

# Check supported formats
v4l2-ctl --list-formats-ext -d /dev/video0

# Quick capture test (single JPEG frame)
libcamera-jpeg -o /tmp/test.jpg --width 1920 --height 1080
# View the file to confirm the camera works
ls -la /tmp/test.jpg
```

### 5.4 Camera Discovery

The camera advertises itself on the local network via Avahi/mDNS. The server
discovers it automatically.

1. Check the server's web dashboard — the camera should appear as **"Pending"** in the camera list.
2. Click **"Confirm"** to pair the camera.
3. The server issues a client certificate (mTLS) to the camera.
4. The camera begins streaming to the server over RTSPS.

The camera also has its own status page at `https://rpi-divinu-cam-XXXX.local` (where XXXX is derived from the camera's serial number). Login with the credentials you set during setup to view device status, change WiFi, or update the password.

### 5.5 Verify Streaming

On the **server**, check that the stream is being received:

```bash
# Check recorder service
systemctl status monitor
journalctl -u monitor -f | grep -i camera

# Check for recording files
ls /data/recordings/
```

On the **camera**, check that the stream is running:

```bash
systemctl status camera-streamer
journalctl -u camera-streamer -f
```

---

## 6. Network Setup

### 6.1 Recommended Network Topology

```
Internet ─── Router ─── Switch ─── RPi 4B Server (wired)
                │
                └── WiFi AP ─── RPi Zero 2W Camera #1
                           ─── RPi Zero 2W Camera #2
                           ─── Phone (dashboard access)
```

### 6.2 Network Requirements

| Requirement | Detail |
|-------------|--------|
| Same LAN | Server and all cameras must be on the same local network |
| WiFi band | Cameras connect on 2.4GHz only (Pi Zero 2W has no 5GHz radio). The server (Pi 4B) supports both — use 5GHz for the server side if your AP is dual-band, leaving 2.4GHz airtime for the cameras |
| Bandwidth | ~4 Mbps per 1080p/25fps camera stream |
| mDNS | Router must allow multicast (most home routers do) |
| No internet required | The system works fully offline. Internet only needed for OTA updates |

### 6.3 Port Reference

| Port | Protocol | Used By | Purpose |
|------|----------|---------|---------|
| 443 | HTTPS | Server | Web dashboard |
| 8554 | RTSPS | Server | Receives camera streams |
| 22 | SSH | Both | Remote access (dev images only) |
| 80 | HTTP | Camera | First-boot setup wizard + authenticated status page |
| 5353 | mDNS | Both | Auto-discovery |

### 6.4 Static IP (Recommended for Server)

Assign a static IP to the server via your router's DHCP reservation, or on the Pi:

```bash
nmcli connection modify "Wired connection 1" \
  ipv4.method manual \
  ipv4.addresses 192.168.1.100/24 \
  ipv4.gateway 192.168.1.1 \
  ipv4.dns "192.168.1.1,8.8.8.8"

nmcli connection up "Wired connection 1"
```

---

## 7. Troubleshooting

### 7.1 Server Won't Boot

| Symptom | Cause | Fix |
|---------|-------|-----|
| No green LED activity | Bad SD card or corrupt image | Re-flash the image |
| Red LED only | Power issue | Use official 5.1V/3A PSU |
| Rainbow screen on monitor | Firmware not found | Re-flash, ensure `.wic` not `.wic.bz2` was flashed |
| Kernel panic on screen | Corrupted rootfs | Re-flash the image |

### 7.2 Camera Not Detected

| Symptom | Cause | Fix |
|---------|-------|-----|
| `v4l2-ctl --list-devices` shows nothing | CSI cable not seated | Reseat cable — contacts toward PCB, clip locked |
| `libcamera-jpeg` fails | Wrong cable type | Zero 2W needs 22-pin cable, not standard 15-pin |
| Camera detected but image is black | Lens cap / IR filter | Remove any lens protector |
| Camera detected but image is distorted | Loose cable | Reseat both ends firmly |

### 7.3 Camera Not Appearing on Server

| Symptom | Cause | Fix |
|---------|-------|-----|
| Camera not in pending list | Different network | Ensure same WiFi SSID / LAN as server |
| Camera not in pending list | mDNS blocked | Check router allows multicast traffic |
| Camera shows "offline" | Stream failed | Check `journalctl -u camera-streamer -f` on camera |
| Camera shows "pending" | Not confirmed yet | Click "Confirm" in the web dashboard |

### 7.4 Web Dashboard Not Loading

| Symptom | Cause | Fix |
|---------|-------|-----|
| Connection refused | nginx not running | `systemctl start nginx` |
| 502 Bad Gateway | Flask app crashed | `systemctl restart monitor` and check logs |
| Certificate error | Self-signed cert | Accept the browser warning (expected) |
| Can't reach `home-monitor.local` | mDNS not working | Use IP address directly |

### 7.5 Poor Video Quality or Lag

| Symptom | Cause | Fix |
|---------|-------|-----|
| Choppy live view | WiFi congestion | Move camera closer to the AP. The Zero 2W is 2.4GHz-only, so move other 2.4GHz-capable devices to 5GHz to free up 2.4GHz airtime, or pick a less-crowded 2.4GHz channel on the AP |
| High latency (>5s) | HLS segment delay | Expected — HLS has 2-6s latency by design |
| Low FPS | CPU throttling | Check `vcgencmd measure_temp` — add cooling if >70C |
| Blocky video | Low bitrate | Increase bitrate in camera config (trade-off: more bandwidth) |

---

## 8. Maintenance

### 8.1 Regular Checks

| Task | Frequency | Command |
|------|-----------|---------|
| Check disk usage | Weekly | `df -h /data` |
| Check CPU temperature | Monthly | `vcgencmd measure_temp` |
| Check service health | As needed | Dashboard → System Health page |
| Check logs for errors | As needed | `journalctl -u monitor --since "1 hour ago"` |

### 8.2 SD Card Health

MicroSD cards degrade with writes. Signs of failure:
- Random reboots
- Read-only filesystem errors
- Slow boot times

**Mitigation:**
- Use a USB SSD for `/data` (recordings) — this is where 99% of writes go.
- Use a high-endurance SD card (Samsung PRO Endurance, SanDisk MAX Endurance).
- The OS uses read-only rootfs with A/B partitions — minimal SD writes.

### 8.3 Firmware Updates (OTA)

> **Current status:** dev builds are the primary tested update path right now. Production signing and full production OTA validation are not yet fully proven on real hardware. See [update-roadmap.md](./update-roadmap.md).

```bash
# Production build: sign the update
./scripts/build-swu.sh --target server --rootfs build/tmp-glibc/deploy/images/raspberrypi4-64/home-monitor-image-prod-raspberrypi4-64.rootfs.ext4.gz --sign

# Upload via the web dashboard: System → OTA Update
# Or push via API:
curl -X POST https://home-monitor.local/api/v1/ota/upload \
  -F "image=@signed-update.swu"
```

The system uses A/B partitions with automatic rollback if the new image fails to boot.

---

## 9. Physical Security Tips

- **Mount cameras out of easy reach** (2.5m+ height). Use tamper-resistant screws.
- **Hide the server** in a closet, utility room, or locked cabinet.
- **Use PoE** where possible — one cable for power + data, harder to unplug accidentally.
- **Label cables** on both ends if you have multiple cameras.
- **Secure the USB SSD** to the server case or shelf — a dangling drive can get knocked off.
- **Keep a backup SD card** with a known-good image, in case you need to recover quickly.
