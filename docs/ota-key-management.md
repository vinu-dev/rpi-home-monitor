# OTA Signing Key Management

Version: 1.2
Date: 2026-04-19

This document defines how the production OTA signing keypair is backed up,
rotated, recovered, and wired into GitHub Actions without ever committing the
private key to git.

Default model:

- self-hosted operators generate and own their own keypair
- dev builds stay unsigned
- GitHub secret-based signing is optional maintainer automation, not the default user path

For the operator-facing release and recovery sequence, use
[Release Operator Runbook](./release-runbook.md).

---

## 1. Active Key Material

The active OTA signing keypair lives only on the build operator machine:

- Private key: `~/.monitor-keys/ota-signing.key`
- Public verification cert: `~/.monitor-keys/ota-signing.crt`

Production builds stage the operator's public certificate into an ignored
generated path before bitbake runs:

- `meta-home-monitor/recipes-support/swupdate/files/generated/swupdate-public.crt`

Devices verify OTA bundles with the baked-in copy at:

- `/etc/swupdate-public.crt`

---

## 2. Encrypted Backup Workflow

Create an encrypted backup of the current OTA signing keypair:

```bash
./scripts/backup-ota-keys.sh --generate-passphrase-file ~/.monitor-keys/ota-backup-passphrase.txt
```

This produces:

- `~/.monitor-keys/backups/ota-signing-backup-<timestamp>.tar.gz.enc`
- `~/.monitor-keys/backups/ota-signing-backup-<timestamp>.tar.gz.enc.sha256`
- `~/.monitor-keys/ota-backup-passphrase.txt`

The encrypted archive is safe to store in:

- a private repository
- a password manager attachment
- encrypted cloud storage
- an offline USB drive

The passphrase must be stored separately from the encrypted archive.

Recommended operator workflow:

1. Generate the backup.
2. Move the passphrase into a password manager or other secure secret store.
3. Remove the plaintext passphrase file from disk after verifying it is stored safely elsewhere.
4. Copy the encrypted archive to at least two locations:
   - one online private location
   - one offline backup location

---

## 3. Recovery Workflow

To restore a backup onto a build machine:

```bash
./scripts/restore-ota-keys.sh \
  --input ~/.monitor-keys/backups/ota-signing-backup-<timestamp>.tar.gz.enc \
  --passphrase-file ~/.monitor-keys/ota-backup-passphrase.txt
```

This restores:

- `~/.monitor-keys/ota-signing.key`
- `~/.monitor-keys/ota-signing.crt`
- `~/.monitor-keys/metadata.txt`

After restore:

1. confirm the cert fingerprint
2. confirm it matches the repo public cert
3. rebuild production images before field deployment if the keypair was rotated

---

## 4. Rotation Workflow

Rotate the OTA signing keypair only as a deliberate release action or after suspected compromise.

Rotation steps:

1. Back up the current keypair first.
2. Delete the current local keypair:

```bash
rm -f ~/.monitor-keys/ota-signing.key ~/.monitor-keys/ota-signing.crt
```

3. Generate a new keypair:

```bash
./scripts/generate-ota-keys.sh
```

4. Rebuild production images so devices carry the new verification cert.
5. Update GitHub Actions secrets if you use the optional maintainer automation path.
6. Create a fresh encrypted backup of the rotated keypair.

Important:

- old devices with the old baked-in cert will not trust bundles signed by the new keypair
- key rotation is therefore coupled to image rollout planning

---

## 5. GitHub Actions Secret-Based Signing

The repo can optionally use these GitHub Actions secrets:

- `OTA_SIGNING_KEY`
- `OTA_SIGNING_CERT`
- `OTA_BACKUP_RECOVERY_PASSPHRASE`

To publish the current local keypair into the repo secrets for maintainer automation:

```bash
./scripts/publish-ota-github-secrets.sh \
  --repo vinu-dev/rpi-home-monitor \
  --recovery-passphrase-file ~/.monitor-keys/ota-backup-passphrase.txt
```

The smoke workflow is:

- [.github/workflows/ota-signing-smoke.yml](../.github/workflows/ota-signing-smoke.yml)

The emergency recovery workflow is:

- [.github/workflows/ota-key-recovery.yml](../.github/workflows/ota-key-recovery.yml)

What it proves:

1. the secrets can be restored into `~/.monitor-keys/`
2. the secret cert is usable by OpenSSL CMS in GitHub Actions
3. `scripts/build-swu.sh --sign` can generate a signed `.swu` bundle in GitHub Actions

This does not replace real hardware OTA validation. It only validates optional secret-based signing plumbing.

### 5.1 Emergency Recovery From GitHub Secrets

If the local keypair and local encrypted backup are both lost, recovery can still be performed from GitHub Actions secrets.

Use the manual workflow:

- `OTA Key Recovery`

Recommended protection model:

1. keep the workflow behind a protected GitHub environment named `ota-key-recovery`
2. require manual approval before jobs in that environment can start
3. download the emitted encrypted artifact
4. decrypt it locally with the stored recovery passphrase

The recovery workflow does **not** expose the raw private key in normal logs. It emits an encrypted backup artifact instead.

---

## 6. Non-Negotiable Rules

- Never commit `ota-signing.key`.
- Never store the plaintext private key in any git repository.
- Never store the passphrase in the same location as the encrypted backup archive.
- Never rotate the signing key without rebuilding production images and updating GitHub secrets.
- Treat loss of the private key or passphrase as a production incident.
