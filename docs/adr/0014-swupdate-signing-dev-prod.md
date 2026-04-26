# ADR-0014: OTA Bundle Signing Policy

**Status:** Accepted (revised 2026-04-26 — see "Update — 1.4.1: signing always-on")
**Date:** 2026-04-13

## Context

SWUpdate is compiled with `CONFIG_SIGNED_IMAGES=y` and `CONFIG_SIGALG_CMS=y` (ADR-0008). This means the swupdate daemon refuses to start unless a public certificate file is passed via `-k`. On first hardware test with v1.1.0 dev images, the daemon crashed at every boot with:

```
Error: SWUpdate is built for signed images, provide a public key file.
```

The OTA signing certificate was never installed into the image — `build-swu.sh --sign` generated the keypair only when explicitly invoked, and had no mechanism to bake the cert into the Yocto build. Requiring a cert for dev builds creates significant friction: every developer must generate a keypair, stage it before building, sign every test bundle, and cannot easily test unsigned changes.

## Decision

OTA bundle signing is **disabled for dev builds** and **enabled for production builds**, controlled by a single `SWUPDATE_SIGNING` variable in `local.conf`.

- **`SWUPDATE_SIGNING = "0"` (default):** `CONFIG_SIGNED_IMAGES` is patched out of the swupdate defconfig at build time. The daemon accepts any bundle without signature verification. No cert is required in the image or at build time.
- **`SWUPDATE_SIGNING = "1"` (prod):** `CONFIG_SIGNED_IMAGES=y` and `CONFIG_SIGALG_CMS=y` remain in the defconfig. The public signing certificate (`swupdate-public.crt`) is baked into the image at `/etc/swupdate-public.crt`. The swupdate daemon is configured via `/etc/swupdate/conf.d/00-home-monitor` to pass `-k /etc/swupdate-public.crt` at startup.

## Current validation status

- **Dev signing bypass:** actively used and intentional
- **Production signing design:** implemented in the build/config pipeline
- **Production signing on real hardware:** not yet fully validated end-to-end

The repo must not describe production OTA signing as fully proven until that hardware validation is complete. See `docs/update-roadmap.md`.

## Implementation

**`meta-home-monitor/recipes-support/swupdate/swupdate_%.bbappend`**
- `SWUPDATE_SIGNING ??= "0"` — default off
- `do_configure:prepend()` — patches defconfig to strip signing lines when `SWUPDATE_SIGNING != "1"`
- `do_install:append()` — conditionally installs cert and conf.d signing args

**`config/rpi4b/local.conf` and `config/zero2w/local.conf`**
- `SWUPDATE_SIGNING = "0"` set explicitly (matches default)
- Comment explains how to flip to `"1"` for prod

**`scripts/generate-ota-keys.sh`**
- One-time script: generates an ECDSA P-256 keypair in `~/.monitor-keys/`, stages the public cert into an ignored generated path for local builds
- Must be run before building with `SWUPDATE_SIGNING = "1"`

**`scripts/build.sh`**
- Guards prod builds: if `SWUPDATE_SIGNING = "1"` is enabled for the target, stages the operator's local cert before invoking bitbake

**`scripts/build-swu.sh`**
- Requires pre-generated keys (no inline keygen); fails fast with a clear message if `~/.monitor-keys/ota-signing.key` is missing

## Key files in the image (when SWUPDATE_SIGNING=1)

| File | Purpose |
|------|---------|
| `/etc/swupdate-public.crt` | OTA signing certificate — verifies bundle signatures |
| `/etc/swupdate/conf.d/00-home-monitor` | Sets `SWUPDATE_ARGS="-v -k /etc/swupdate-public.crt"` |
| `/etc/swupdate.cfg` | Runtime configuration (loglevel, identify block) |
| `/etc/hwrevision` | Hardware ID checked against `hardware-compatibility` in sw-description |
| `/etc/sw-versions` | Installed software version, updated by post-install scripts |

## Key pair management

- **Private key:** `~/.monitor-keys/ota-signing.key` — never committed, stays on build machine
- **Generated cert staging path:** `meta-home-monitor/recipes-support/swupdate/files/generated/swupdate-public.crt` — ignored by git, created from the operator's local cert at build time
- **Key rotation:** delete `~/.monitor-keys/ota-signing.{key,crt}`, re-run `generate-ota-keys.sh`, rebuild image and redeploy cert

## Signing algorithm

The production SWUpdate path uses **CMS / PKCS7 with an ECDSA P-256 certificate**.

This is deliberate:
- `build-swu.sh --sign` signs `sw-description` via `openssl cms -sign`
- the SWUpdate daemon verifies that CMS signature against `/etc/swupdate-public.crt`
- the repo previously documented Ed25519 here, but OpenSSL CMS signing did not validate cleanly in the tested build path

Detached signatures for non-SWUpdate artifacts can still use different tooling, but the validated `.swu` flow in this repo is certificate-based CMS signing with ECDSA P-256.

## Consequences

**Positive:**
- Dev builds iterate freely — no key management, no signing step
- Full OTA signing infrastructure remains in the codebase and works for prod
- One variable flip (`SWUPDATE_SIGNING = "1"`) enables production hardening
- No separate dev/prod recipes or distro configs needed
- Self-hosted users can own their own trust chain instead of depending on a repo-held private key

**Negative:**
- Dev images accept unsigned (potentially malicious) OTA bundles — acceptable since dev devices are on a trusted LAN and credentials are known
- Build machine must run `generate-ota-keys.sh` before first prod build — documented in build-setup.md and release-runbook.md

## Alternatives considered

**Separate dev/prod distro configs (`home-monitor-dev.conf` / `home-monitor-prod.conf`):** Would require duplicating the entire distro conf or using inheritance. Adds complexity for a single-variable difference. Rejected.

**Always enable signing, commit a "dev" cert+key to the repo:** The key would be public (anyone could sign malicious bundles for dev devices). Violates the principle of never committing private keys. Rejected.

**Single defconfig with signing disabled permanently:** Would require re-enabling for prod builds via a manual local.conf change with no guardrails. Using `SWUPDATE_SIGNING` is explicit and self-documenting. Rejected.

## Update — 1.4.1: signing always-on

The original split (dev unsigned / prod signed) created an asymmetry that
the team paid for during 1.4.0 hardware validation: a dev SWU could not
be installed on a prod-flashed device, and a prod SWU could not be
exercised on a dev-flashed device without a parallel signing rehearsal.
Worse, "unsigned dev images" trained reviewers to think of signing as a
"prod-only thing" — which is the wrong intuition. Signing is a security
control; it should be the default.

As of 1.4.1, the policy is **signing always-on**. Both dev and prod
builds:

- compile swupdate with `CONFIG_SIGNED_IMAGES=y` and `CONFIG_SIGALG_CMS=y`
- ship `/etc/swupdate-public.crt` and `/etc/swupdate-enforce`
- accept only CMS-signed `.swu` bundles at install time

The only meaningful axis between dev and prod images is now
`debug-tweaks` + dev tools (gdb, strace, tcpdump, root SSH). Signing,
auth, hardening posture are identical.

Concretely:

- Distro default flipped to `SWUPDATE_SIGNING ?= "1"` in
  `meta-home-monitor/conf/distro/home-monitor.conf`.
- `config/{rpi4b,zero2w}/local.conf` no longer set `SWUPDATE_SIGNING="0"`.
  The distro default applies.
- `scripts/build.sh` auto-applies `--sign` to every target (not just
  `*-prod`).
- The dev/prod naming on `local.conf.prod` is preserved for clarity but
  the signing line in `local.conf.prod` is now redundant with the distro
  default.

The original "Negative" point — "Dev images accept unsigned bundles" —
no longer applies. Dev developers must run `generate-ota-keys.sh` once
the same as prod operators do; the cost is small and well-contained.
