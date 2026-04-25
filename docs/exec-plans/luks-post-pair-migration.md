# LUKS post-pair migration — exec plan (target 1.4.1)

This is the deferred follow-up from CHANGELOG 1.3.1 §"Known follow-up"
and the gap captured in issue #101. ADR-0010 is the design reference;
this doc is the operational rollout plan, including the safety
mitigations needed because hardware validation is high-cost (data-loss
risk on any device that holds recordings or pairing state).

## Why deferred from 1.4.0

The work is genuinely 2–3 focused days for a senior engineer:

- Yocto: kernel config fragment for Adiantum + dm-crypt
- First-boot service: a multi-stage atomic re-key flow on the camera
  (HKDF-derived key) and on the server (passphrase or keyfile)
- SWUpdate post-install hook: propagate the keyfile / regenerate the
  key derivation into the new initramfs on every OTA so subsequent
  reboots can still unlock the data partition
- Server unlock UX: dropbear-in-initramfs and/or Plymouth prompt
- LUKS header backup landing on /boot
- Container-loopback test wiring on the build VM
- Issue #101 update; release notes; runbook

Bundling that into 1.4.0 with no hardware test gate would have shipped
a half-baked migration to opt-in users. The user-explicit mitigation
list (feature flag, atomic snapshot, container test) is sound, but
implementing all of it well takes longer than the rest of the 1.4.0
work combined.

## Scope (1.4.1)

### Required (hard blockers for the release)

1. **Adiantum kernel fragment** —
   `meta-home-monitor/recipes-kernel/linux/linux-raspberrypi/files/adiantum.cfg`
   ```
   CONFIG_CRYPTO_ADIANTUM=y
   CONFIG_CRYPTO_NHPOLY1305_NEON=y
   CONFIG_CRYPTO_CHACHA20_NEON=y
   CONFIG_DM_CRYPT=y
   CONFIG_BLK_DEV_DM=y
   ```
   `=y` not `=m` — must be available in initramfs without module load.
   Wire via `linux-raspberrypi_%.bbappend` adding the fragment via
   `SRC_URI` and the `kernel-yocto.bbclass` mechanism.

2. **First-boot-after-pairing service** (server + camera variants):
   - `meta-home-monitor/recipes-core/luks-post-pair/luks-post-pair.bb`
   - Service: `luks-post-pair-migrate.service` — Type=oneshot,
     `ConditionPathExists=/data/config/luks-migration-enabled` (the
     opt-in feature flag), runs `luks-post-pair-migrate.sh`, runs
     ONCE (creates `/data/.luks-migrated` marker).
   - Script: pre-flight checks → snapshot → format new LUKS volume →
     copy → verify → swap → reboot. Detailed below.

3. **SWUpdate post-install hook** —
   `swupdate/post-update.sh` augments existing flow:
   - When upgrading INTO the new image, if `/data/.luks-migrated`
     exists (data is already encrypted), regenerate the keyfile in
     the new rootfs's initramfs (via `mkinitramfs --add-key`) before
     swapping the boot slot.

4. **Server unlock**:
   - **Pick the simplest:** keyfile-only with admin opt-in, NOT
     dropbear/Plymouth. Trade-off documented (keyfile on same SD
     card → physical theft still exposes data; protects against
     non-physical SD-clone scenarios).
   - **Why pick simpler:** dropbear-in-initramfs on RPi 4B has its
     own can of worms (network up in initramfs, custom IP config,
     password vs key auth, port mapping). Hardware test surface
     too large for one release.

5. **Camera unlock**: HKDF from `pairing_secret + CPU serial`
   (ADR-0010 §4). Keyfile written into initramfs after derivation
   succeeds.

6. **LUKS header backup** to `/boot/luks-header.bak` (FAT32 boot
   partition, persists across rootfs swaps).

### Mandatory safety mitigations (per user direction, recorded here)

a) **Feature flag gating.** Migration runs only if
   `/data/config/luks-migration-enabled` exists. New 1.4.1 installs
   do NOT auto-migrate. Existing installs upgrading to 1.4.1 do NOT
   auto-migrate. Operator opts in by creating the file (CLI or
   admin-only dashboard control).

b) **Pre-flight checks** (any failure → abort with audit log entry,
   no destructive action):
   - free space on `/data` ≥ 2× currently-used space
   - free space on `/boot` ≥ 2 MB (header backup)
   - test-create + test-mount a 1 MB LUKS file in `/tmp` first; abort
     if Adiantum cipher fails to load
   - `fsck -n /dev/mmcblk0p4` clean
   - pairing-secret file exists (ADR-0009)
   - `/etc/sw-versions` shows we're running the post-migration image
     (refuse to migrate from a partial-OTA state)

c) **Atomic + reversible flow:**
   1. Snapshot raw `/data` to `/boot/data-pre-luks.tar.gz` BEFORE
      anything destructive. Verify tarball integrity (`tar tzf`).
   2. Write LUKS header backup to `/boot/luks-header.bak`.
   3. Format LUKS on a fresh loopback file at `/tmp/data-new.img`;
      verify open + close + cipher.
   4. Bind-mount `/data` read-only, copy contents to mounted LUKS
      volume on a temp partition (or the new loopback once tested),
      verify checksums (sha256 sum of all files matches before/after).
   5. Only AFTER all the above succeed: perform the actual migration
      — unmount `/data`, format `/dev/mmcblk0p4` with the LUKS
      params, copy contents back from snapshot, mount via
      `/dev/mapper/data`, write the marker.
   6. On ANY failure at any stage: do not retry on subsequent boots
      (`/data/.luks-migration-failed` marker). Restore from the
      tarball if step 5 was reached. Surface error in dashboard.

d) **Logic-level testing without hardware:**
   - **Unit tests** for helper functions: key derivation, partition
     discovery, pre-flight checks. pytest under
     `app/server/tests/unit/test_luks_helpers.py` and
     `app/camera/tests/unit/test_luks_helpers.py`.
   - **Container loopback test** on the build VM: a Docker container
     with cryptsetup installed runs the migration script against a
     loopback file standing in for `/data`. Proves the script's
     real-world wiring (mount, cryptsetup, copy, swap) without
     touching hardware. CI job: `LUKS Migration Loopback`.

e) **Documentation:**
   - `docs/operations/luks-migration.md` runbook: how to opt in, how
     to verify, how to recover if abort.
   - 1.4.1 release notes: opt-in only, NOT hardware-validated, do not
     enable on production until a non-critical install has run it
     end-to-end.
   - Update issue #101 from "open" to "in progress: implementation
     merged in 1.4.1, hardware validation pending".

### Out of scope (1.4.1)

- Dropbear/Plymouth unlock UX (deferred to 1.5.0 if demand emerges)
- Auto-unlock-via-network (chicken/egg with WiFi creds on encrypted
  /data; ADR-0010 §"Network-based unlock" rejected this)
- TPM integration (no TPM on either board)
- Migration UI in the dashboard (CLI opt-in only for 1.4.1)

## Validation gate before shipping 1.4.1

- [ ] All ADR-0010 §1 cipher params produce a working LUKS volume in
      the container test (parametrized: server params + camera params)
- [ ] Migration script unit tests cover all 12 pre-flight checks
- [ ] Container test: clean migration with 100 MB of data succeeds in
      under 5 minutes (server params), under 15 minutes (camera params)
- [ ] Container test: each pre-flight check failure mode triggers
      abort + tarball preserved
- [ ] Container test: power-loss simulation (SIGKILL the script) at
      each stage leaves `/data` either fully old or fully new — never
      mid-state. Restart-after-kill correctly resumes or rolls back.
- [ ] OTA upgrade test (in container, with two loopback rootfs slots
      simulated): post-install hook propagates keyfile to new
      initramfs.
- [ ] Server LUKS open with keyfile from initramfs succeeds in the
      container.
- [ ] Camera LUKS open with HKDF-derived key from `pairing_secret +
      cpu_serial` succeeds in the container.
- [ ] Header backup written to `/boot/luks-header.bak` and is usable
      to recover from a corrupted header.
- [ ] runbook `docs/operations/luks-migration.md` written + reviewed.
- [ ] Issue #101 updated.

After all of the above are green: ship 1.4.1, document explicitly
that hardware validation has not happened, and recommend operators
test on a non-critical device first.

## Estimated effort

- Kernel fragment + Yocto wiring: 2 hours
- Migration script (server + camera variants): 1 day
- Pre-flight + safety net + container test wiring: 1 day
- SWUpdate post-install hook: 4 hours
- Documentation + release notes + ADR follow-up: 4 hours
- **Total: 2–3 days focused work**

This is the principal-engineer "do it once, do it right" estimate.
A "ship something" estimate without the safety net is half that and
risks bricking opt-in users — out of scope for this product's
self-hosted-by-individuals threat model.

## Refs

- ADR-0010 (the design — re-read before implementing)
- ADR-0008 (A/B rollback — coupling for the post-install hook)
- Issue #101 (the open security gap this closes)
- CHANGELOG 1.3.1 §"Known follow-up" (where this was first deferred)
