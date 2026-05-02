# Release Operator Runbook

Version: 1.0
Date: 2026-04-14

This runbook is the operator-facing path for building and releasing software
updates for Home Monitor.

Use this document when you need to:

- build and sign a normal release
- recover onto a fresh build VM
- recover the OTA signing key onto a new machine
- understand which parts are manual vs automated

If this file conflicts with scattered older instructions, this file wins.

---

## 1. Principle

This repository is designed for self-hosted operators.

That means:

- each operator can generate and own their own OTA signing keypair
- dev builds do not require signing
- production trust is rooted in the operator's own keypair, not a shared project private key

Optional maintainer automation through GitHub Actions can exist, but it is not
the default path users should depend on.

---

## 2. What Is Automated vs Manual

### Automated by scripts

- Yocto build environment setup: `./scripts/setup-env.sh`
- Image builds: `./scripts/build.sh`
- SWUpdate bundle creation: `./scripts/build-swu.sh --sign`
- OTA key generation: `./scripts/generate-ota-keys.sh`
- OTA key backup: `./scripts/backup-ota-keys.sh`
- OTA key restore: `./scripts/restore-ota-keys.sh`
- Optional publish of OTA signing secrets to GitHub: `./scripts/publish-ota-github-secrets.sh`

### Still manual on purpose

- creating or approving the release branch / PR / tag
- deciding when a build is release-worthy
- merging the default branch
- running the GitHub emergency key recovery workflow
- downloading the encrypted recovery artifact from GitHub
- copying restored signing keys to a new VM
- final hardware validation and release sign-off

Bridge-phase note:

- until the full hardware lab is installed, the repo uses SSH-based hardware validation as an interim step
- the active hardware lab rollout plan is tracked in [Hardware Lab Rollout](../exec-plans/hardware-lab-rollout.md)

---

## 3. Current Truth

- Dev builds intentionally bypass OTA signing with `SWUPDATE_SIGNING = "0"`.
- Production builds are intended to require signed OTA bundles.
- Self-hosted operators are expected to generate and own their own OTA signing keypair.
- Signed `.swu` bundle creation is validated on the build VM.
- Production OTA install/reboot/rollback is **not yet fully validated on real hardware**.
- Do not describe production OTA as field-proven until the reboot/rollback path is validated on devices.

Supporting status documents:

- [Update Roadmap](../history/planning/update-roadmap.md)
- [OTA Signing Key Management](./ota-key-management.md)

---

## 4. Normal Self-Hosted Release Path

### 4.1 Preconditions

1. Work is merged to `main`.
2. Tests and validation required by [Development Guide](./development-guide.md) have passed.
3. You are ready to generate or use your own OTA signing keypair.

### 4.2 Prepare or verify the build machine

```bash
git clone git@github.com:vinu-dev/rpi-home-monitor.git ~/yocto
cd ~/yocto
./scripts/setup-env.sh
```

### 4.3 Generate your own OTA signing keypair

Run this once on your operator machine:

```bash
./scripts/generate-ota-keys.sh
```

This creates:

- `~/.monitor-keys/ota-signing.key`
- `~/.monitor-keys/ota-signing.crt`

Production builds automatically stage your local public certificate into an
ignored build-time path inside the repo so it gets baked into the image.

### 4.4 Build production images

```bash
./scripts/build.sh server-prod
./scripts/build.sh camera-prod
```

### 4.5 Build signed SWUpdate bundles

```bash
./scripts/build-swu.sh --target server --rootfs <server-rootfs.ext4.gz> --sign
./scripts/build-swu.sh --target camera --rootfs <camera-rootfs.ext4.gz> --sign
```

### 4.6 Release validation

Minimum release checks:

1. `ruff check .`
2. `ruff format --check .`
3. server tests
4. camera tests
5. required Yocto parse / graph / full build checks
6. hardware smoke validation
7. OTA-specific validation for any release that claims OTA readiness

If the release claims production OTA readiness, also require:

1. signed server update applied on hardware
2. signed camera update applied on hardware
3. post-reboot devices came back correctly
4. rollback behavior validated for a failed update case

---

## 5. New VM Recovery Path

Use this when the old build VM is gone but your local operator machine still exists.

### 5.1 Create a new VM

Use Ubuntu 22.04 or 24.04 LTS with the requirements from [Build Machine Setup](./build-setup.md).

### 5.2 Recreate the repo

```bash
git clone git@github.com:vinu-dev/rpi-home-monitor.git ~/yocto
cd ~/yocto
./scripts/setup-env.sh
```

### 5.3 Restore or copy your OTA signing keypair to the VM

If the key is still present locally:

```bash
scp ~/.monitor-keys/ota-signing.key ~/.monitor-keys/ota-signing.crt <vm-user>@<vm-host>:~/.monitor-keys/
```

If the key must be restored first, use Section 6 or Section 7 below.

### 5.4 Build and sign on the new VM

```bash
./scripts/build.sh server-prod
./scripts/build.sh camera-prod
./scripts/build-swu.sh --target server --rootfs <server-rootfs.ext4.gz> --sign
./scripts/build-swu.sh --target camera --rootfs <camera-rootfs.ext4.gz> --sign
```

---

## 6. Lost Local Key But Encrypted Backup Exists

### 6.1 Restore locally

```bash
./scripts/restore-ota-keys.sh \
  --input ~/.monitor-keys/backups/ota-signing-backup-<timestamp>.tar.gz.enc \
  --passphrase-file ~/.monitor-keys/ota-backup-passphrase.txt
```

### 6.2 Copy to the build VM

```bash
scp ~/.monitor-keys/ota-signing.key ~/.monitor-keys/ota-signing.crt <vm-user>@<vm-host>:~/.monitor-keys/
```

Then continue with Section 4.4.

---

## 7. Lost Local Key And Lost Encrypted Backup

Use this only as an emergency path.

Prerequisites:

- the GitHub recovery workflow must exist on the default branch
- GitHub secrets must exist:
  - `OTA_SIGNING_KEY`
  - `OTA_SIGNING_CERT`
  - `OTA_BACKUP_RECOVERY_PASSPHRASE`
- you must still possess the recovery passphrase in your secure store

### 7.1 Run the GitHub recovery workflow

In GitHub Actions, run:

- `OTA Key Recovery`

### 7.2 Download the encrypted recovery artifact

Download the artifact to your local operator machine.

### 7.3 Restore the key locally

```bash
./scripts/restore-ota-keys.sh \
  --input <downloaded-encrypted-artifact>.tar.gz.enc \
  --passphrase-file ~/.monitor-keys/ota-backup-passphrase.txt
```

### 7.4 Copy the restored key to the new VM

```bash
scp ~/.monitor-keys/ota-signing.key ~/.monitor-keys/ota-signing.crt <vm-user>@<vm-host>:~/.monitor-keys/
```

Then continue with Section 4.4.

Important:

- GitHub secrets are a controlled emergency copy, not the primary operator workflow.
- Do not rely on GitHub secrets as your only recovery plan.

---

## 8. If The Recovery Passphrase Is Lost

If you lose all of the following:

- the local private key
- the encrypted backup
- the recovery passphrase

then the current signing keypair is no longer safely recoverable for operator use.

At that point:

1. treat it as a signing incident
2. generate a new OTA signing keypair
3. rebuild production images with the new cert
4. republish GitHub secrets if you use the optional maintainer automation path
5. create a fresh encrypted backup and store the new passphrase safely

---

## 9. Optional Maintainer Automation Path

The repo can optionally store OTA signing material in GitHub Actions secrets.

This is for:

- maintainer-operated CI signing
- emergency recovery
- release automation

It is not required for normal self-hosted users.

If you choose to use it:

```bash
./scripts/publish-ota-github-secrets.sh --repo <owner>/<repo> --recovery-passphrase-file ~/.monitor-keys/ota-backup-passphrase.txt
```

---

## 10. Resumption Notes For Future Operators And AI Agents

When continuing release work after interruption:

1. read this runbook first
2. read [Update Roadmap](../history/planning/update-roadmap.md) for current truth
3. read the active exec plan in [docs/exec-plans/](../exec-plans/)
4. record the exact current state before starting the next long-running step

If a release or OTA task spans multiple sessions, update the active exec plan before stopping.
