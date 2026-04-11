# ADR-0010: LUKS Data Partition Encryption

## Status
Proposed

## Context
Both server and camera store sensitive data on the `/data` partition: video recordings, WiFi credentials, user passwords (hashed), session secrets, the CA private key (server), and client certs (camera). Physical theft of an SD card should not expose this data.

Neither device has a TPM, HSM, or secure enclave — the RPi 4B and Zero 2W have no hardware key storage. Neither has AES hardware acceleration. The LUKS key must be derived from something the device knows or the user provides, and the cipher must perform well in software.

### Hardware crypto performance (measured 2026-04-11)

| Cipher (OpenSSL) | Server (Cortex-A72) | Camera (Cortex-A53) |
|-------------------|---------------------|---------------------|
| AES-256-CBC | ~30 MB/s | ~33 MB/s |
| ChaCha20-Poly1305 | ~268 MB/s | ~165 MB/s |

Adiantum (`xchacha20,aes-adiantum`) benchmarks from published RPi tests:
- Cortex-A72: ~177 MB/s encrypt, ~177 MB/s decrypt (cryptsetup benchmark)
- Cortex-A53: ~122 MB/s encrypt, ~122 MB/s decrypt

**Neither board has hardware AES.** Google mandates Adiantum for Android devices without AES acceleration. Raspberry Pi OS documentation recommends Adiantum for all pre-RPi 5 models.

**Kernel state:** The RPi kernel has `CONFIG_CRYPTO_ADIANTUM=m` available, but the current Yocto build does **not** include it. A kernel config fragment is required.

### Memory constraints for LUKS unlock

| | Server | Camera |
|---|--------|--------|
| Total RAM | 8 GB | 512 MB |
| Available | 7.7 GB | 183 MB (256 MB CMA for GPU) |
| LUKS argon2id default | ~1 GB | ~1 GB (**will OOM**) |
| Safe argon2id | 1 GB | **64 MB** (leaves 119 MB for initramfs) |

**Critical:** LUKS2 argon2id memory is stored in the LUKS header. A volume created with 1 GB memory cost **cannot be opened** on the camera (183 MB available). Must use `--pbkdf-force-iterations` (not `--iter-time`) to avoid benchmark variance across devices.

This decision is coupled with ADR-0008 (the data partition survives A/B updates — LUKS must work with both rootfs slots) and ADR-0009 (the CA key and client certs live on `/data` and must be protected; `pairing_secret` is used for camera key derivation).

## Decision

Use **LUKS2 with Adiantum cipher** (`xchacha20,aes-adiantum-plain64`). Server uses a **user-provided passphrase** with optional auto-unlock keyfile. Camera uses a **server-derived key** provisioned during pairing (ADR-0009).

---

### 1. Encryption parameters

**Server:**
```bash
cryptsetup luksFormat --type luks2 \
  --cipher xchacha20,aes-adiantum-plain64 \
  --hash sha256 \
  --key-size 256 \
  --pbkdf argon2id \
  --pbkdf-memory 1048576 \
  --pbkdf-force-iterations 4 \
  --pbkdf-parallel 4 \
  /dev/mmcblk0p4
```

**Camera:**
```bash
cryptsetup luksFormat --type luks2 \
  --cipher xchacha20,aes-adiantum-plain64 \
  --hash sha256 \
  --key-size 256 \
  --pbkdf argon2id \
  --pbkdf-memory 65536 \
  --pbkdf-force-iterations 4 \
  --pbkdf-parallel 1 \
  /dev/mmcblk0p4
```

| Parameter | Server | Camera | Why |
|-----------|--------|--------|-----|
| Cipher | xchacha20,aes-adiantum-plain64 | Same | 2-3.5x faster than AES on ARM without hw accel |
| KDF | argon2id | Same | Memory-hard, blocks GPU brute-force |
| KDF memory | 1 GB | **64 MB** | Camera has 183 MB available; 64 MB is RFC 9106 constrained recommendation |
| KDF iterations | 4 | 4 | Use `--pbkdf-force-iterations` to ensure consistency across devices |
| KDF parallelism | 4 | **1** | Camera: minimize memory pressure during unlock |
| Key size | 256-bit | 256-bit | Standard for Adiantum |

**Important:** Always create LUKS containers **on the target device** or with parameters matching the target's constraints. Never create on a more powerful machine — the argon2id memory parameter stored in the LUKS header must be satisfiable at unlock time.

---

### 2. Kernel config fragment (Yocto)

The current kernel lacks Adiantum. Add a kernel config fragment in `meta-home-monitor`:

```
# meta-home-monitor/recipes-kernel/linux/linux-raspberrypi/adiantum.cfg
CONFIG_CRYPTO_ADIANTUM=y
CONFIG_CRYPTO_NHPOLY1305_NEON=y
CONFIG_CRYPTO_CHACHA20_NEON=y
CONFIG_DM_CRYPT=y
```

Build as `=y` (built-in, not module) to ensure crypto is available in initramfs for boot-time unlock without needing to load modules.

---

### 3. Server: passphrase-based unlock

**First boot provisioning:**
1. Setup wizard (already exists for user creation) adds a "Disk Encryption" step
2. User enters a passphrase (minimum 12 characters, strength meter shown)
3. First-boot systemd service:
   ```bash
   cryptsetup luksFormat /dev/mmcblk0p4 --type luks2 \
     --cipher xchacha20,aes-adiantum-plain64 --key-size 256 \
     --pbkdf argon2id --pbkdf-memory 1048576 --pbkdf-force-iterations 4
   cryptsetup luksOpen /dev/mmcblk0p4 data
   mkfs.ext4 /dev/mapper/data
   mount /dev/mapper/data /data
   ```
4. Passphrase stored nowhere on device — user must remember it

**Boot unlock flow:**
```
U-Boot → kernel → initramfs
  ├─ If upgrade_available=1 → health check first (ADR-0008)
  ├─ cryptsetup luksOpen /dev/mmcblk0p4 data
  │   └─ Passphrase source (in priority order):
  │       1. Keyfile in initramfs (auto-unlock, if configured)
  │       2. Network unlock via SSH (dropbear in initramfs)
  │       3. Plymouth passphrase prompt (HDMI/serial console)
  └─ mount /dev/mapper/data /data → continue boot
```

**Auto-unlock option:**
For users who prioritize availability (server in a locked room), an optional keyfile can be embedded in the initramfs:
- Enabled via setup wizard checkbox: "Unlock automatically on boot"
- Keyfile: 256-bit random, stored at `/etc/cryptsetup-keys.d/data.key` in initramfs
- LUKS keyslot 1 holds the keyfile; keyslot 0 holds the passphrase (recovery)
- Trade-off clearly documented: auto-unlock means SD card theft exposes data (keyfile on same card)

**Dropbear SSH unlock:**
For headless server after power outage, admin SSHs into initramfs to enter passphrase:
- `dropbear` runs in initramfs on port 2222
- Admin: `ssh -p 2222 root@server-ip` → enters passphrase → boot continues
- Only available if auto-unlock is disabled

---

### 4. Camera: server-derived key

The camera is headless — no keyboard, must boot unattended after power outages.

**Key derivation:**
```python
camera_luks_key = HKDF-SHA256(
    ikm  = pairing_secret,          # 32 bytes random, from pairing (ADR-0009)
    salt = camera_cpu_serial,        # /proc/cpuinfo serial (unique per device)
    info = "home-monitor-camera-luks-v1"
)
```

**Provisioning (extends pairing flow from ADR-0009):**
1. During `POST /api/v1/pair/exchange`, server generates and returns `pairing_secret` (32 random bytes)
2. Camera derives LUKS key from `pairing_secret` + CPU serial
3. Camera first-boot service formats `/data`:
   ```bash
   echo -n "$DERIVED_KEY" | cryptsetup luksFormat /dev/mmcblk0p4 --type luks2 \
     --cipher xchacha20,aes-adiantum-plain64 --key-size 256 \
     --pbkdf argon2id --pbkdf-memory 65536 --pbkdf-force-iterations 4 \
     --pbkdf-parallel 1 --key-file=-
   ```
4. Derived key stored as keyfile in initramfs for automatic unlock on boot
5. Server stores `pairing_secret` in `cameras.json` (encrypted at rest on server's own LUKS partition)

**Why this works:**
- Camera boots unattended — keyfile in initramfs unlocks `/data` automatically
- The `pairing_secret` on the server allows re-deriving the key if camera initramfs is rebuilt (e.g., after OTA update)
- The CPU serial salt binds the key to specific hardware — stealing the `pairing_secret` alone isn't enough
- The threat mitigated is **casual theft** — someone grabs the camera and reads the SD on a laptop. LUKS means they can't just mount the ext4 partition

---

### 5. Interaction with A/B updates (ADR-0008)

**Critical constraint:** OTA updates replace rootfs partitions (A or B) but never touch `/data`. The LUKS partition is stable across updates.

However, the initramfs (containing the LUKS unlock keyfile or logic) is part of the rootfs. After an OTA update:

**Server:**
- If auto-unlock enabled: SWUpdate post-install hook copies `/etc/cryptsetup-keys.d/data.key` from active initramfs to the new rootfs before reboot
- If auto-unlock disabled: passphrase prompt is always in initramfs (no copy needed)

**Camera:**
- SWUpdate post-install hook derives the key from stored `pairing_secret` (on `/data`, already mounted) + CPU serial, writes it into the new rootfs initramfs
- This happens during the update (active rootfs is running, `/data` is mounted), before the reboot into the new slot

---

### 6. WKS changes

The current WKS files (updated in ADR-0008) create the data partition with `--grow` and ext4. In production builds, the partition is created as raw (no filesystem) — LUKS formatting happens on first boot:

```
# Production
part /data --ondisk mmcblk0 --align 4096 --grow

# Dev (unchanged — no encryption for faster iteration)
part /data --ondisk mmcblk0 --fstype=ext4 --label data --align 4096 --grow
```

Dev builds keep plain ext4, consistent with ADR-0007's dev/prod split.

---

### 7. Recovery paths

**Server — forgot passphrase:**
1. If auto-unlock keyfile exists: boot normally, change passphrase via `cryptsetup luksChangeKey`
2. If passphrase truly forgotten and no keyfile: data is **unrecoverable** (this is the point of encryption)
3. Factory reset: re-flash SD card, re-run setup, data is lost

**Camera — pairing secret lost (server re-flashed):**
1. Camera can't derive LUKS key because `pairing_secret` was on server's encrypted `/data`
2. Camera must be factory-reset (re-flash SD) and re-paired
3. Camera data loss is minimal — config and certs only, no recordings

**Server — SD card corruption:**
1. First-boot service saves LUKS header backup: `cryptsetup luksHeaderBackup /dev/mmcblk0p4 --header-backup-file /boot/luks-header.bak`
2. `/boot` is unencrypted FAT32 — header alone doesn't expose data without passphrase
3. Recovery: boot from USB, restore header, unlock with passphrase

## Rationale

- **Adiantum (`xchacha20,aes-adiantum-plain64`)**: 2-3.5x faster than AES-XTS on both boards (no hardware AES). Google mandates it for Android devices without AES acceleration. RPi OS docs recommend it for pre-RPi 5. The cipher is in the upstream kernel (`CONFIG_CRYPTO_ADIANTUM`) — just needs a config fragment in Yocto
- **LUKS2**: Standard Linux disk encryption, well-supported in Yocto. No alternative has comparable maturity
- **argon2id**: Memory-hard KDF, blocks GPU-accelerated brute-force. LUKS2 default. ElcomSoft reports ~2 passwords/second even on desktop hardware — GPU acceleration is effectively blocked
- **1 GB argon2id for server**: LUKS2 default, uses <13% of 8 GB RAM. No reason to weaken it
- **64 MB argon2id for camera**: RFC 9106 "second recommended" for constrained environments. Leaves 119 MB for initramfs during boot unlock. Still strong — attacker needs 64 MB per guess per thread
- **`--pbkdf-force-iterations` not `--iter-time`**: Ensures consistent parameters regardless of which machine formats the volume. `--iter-time` benchmarks on the current machine and produces wrong values on a faster build host
- **User passphrase for server**: Without TPM, the passphrase is the only secret not stored on the device. Standard approach for FDE without hardware security modules
- **Server-derived key for camera**: Camera must boot unattended (headless, no keyboard). HKDF from `pairing_secret` + CPU serial ties encryption to the trust relationship and specific hardware
- **LUKS header backup on `/boot`**: The header backup allows recovery from SD card corruption. The passphrase is still required — the header alone is useless to an attacker

## Alternatives Considered

### AES-XTS-PLAIN64 (current ADR-0010 draft and architecture.md)
The original design specified AES. Rejected after benchmarking: ~30 MB/s vs Adiantum's ~177 MB/s on server, ~33 MB/s vs ~122 MB/s on camera. While SD card I/O (~40 MB/s) is the bottleneck, AES consumes significantly more CPU — matters when ffmpeg and mediamtx are running.

### TPM-based key sealing
Ideal — key sealed to hardware, auto-unlock without exposing key on SD card. Rejected because neither RPi 4B nor Zero 2W has a TPM. External TPM modules would increase BOM cost and complexity.

### Network-based unlock for camera (fetch key from server at boot)
Camera contacts server over mTLS, receives unlock key. Chicken-and-egg: network config (WiFi credentials) is on the encrypted `/data` partition. Would need WiFi creds in unencrypted rootfs, partially defeating the purpose.

### No encryption (rely on physical security)
A stolen SD card would expose: video recordings, WiFi password, admin password hash, CA private key (server). The CA key exposure is especially dangerous — allows forging camera certificates.

### dm-crypt without LUKS
Lower overhead but no key management (no multiple keyslots, no header backup, no argon2id). LUKS adds minimal overhead and significant operational flexibility (keyslots, recovery, migration).

### PBKDF2 instead of argon2id for camera
Would use less memory. Rejected because PBKDF2 is not memory-hard — GPU brute-force is efficient against it. Argon2id at 64 MB is still strong and fits within the camera's constraints.

## Consequences

- **Server reboot requires passphrase** (unless auto-unlock enabled) — deliberate availability/security trade-off for a home server
- **Camera encryption is weaker than server** — keyfile on same SD card means physical theft exposes data. Accepted trade-off for headless unattended boot without TPM
- **First boot is slower** — LUKS formatting + argon2id: ~10-30s on server, ~30-60s on camera (one-time)
- **Performance**: Adiantum at ~177 MB/s (server) / ~122 MB/s (camera) — far above SD card write speed (~40 MB/s). No practical throughput loss
- **Dev builds skip encryption** — faster iteration, no passphrase prompts, consistent with ADR-0007
- **Data is unrecoverable** without passphrase (server) or pairing secret (camera) — intentional, documented in setup wizard
- **OTA post-install hooks** must propagate initramfs keyfile (ADR-0008 coupling) — tested as part of the OTA validation flow
- **Kernel config fragment required** — adds `CONFIG_CRYPTO_ADIANTUM=y` and related crypto modules to the Yocto build
- **`pairing_secret` coupling** with ADR-0009 — single pairing ceremony provisions both mTLS identity and LUKS key material
