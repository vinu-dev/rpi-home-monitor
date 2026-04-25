# Releasing Home Monitor

Cutting a release is one entry point: `./scripts/release.sh`. The
script enforces the rules; humans don't hand-edit version strings.

## Version policy

- Single source of truth: `VERSION` at the repo root. One semver line.
- All other surfaces derive from it:
  - Yocto: `meta-home-monitor/conf/distro/home-monitor.conf` reads
    `VERSION` at parse time and propagates to `BUILD_ID`,
    `VERSION_ID`, `PRETTY_NAME` in `/etc/os-release`.
  - SWU: `scripts/build-swu.sh` falls back to `VERSION` when no
    `--version` is passed.
  - Release artefact filenames: derived from the git tag via
    `scripts/build.sh` (`*-prod` targets refuse to build without an
    exact tag on `HEAD`).
- `scripts/check_version_consistency.py` runs in CI (Repo Governance
  job) to catch drift.

App-package versions in `app/{camera,server}/setup.py` are
**intentionally** frozen at `1.0.0` (per the comment in
`swupdate/post-update.sh:87`). They're recipe-package versions, not
release versions; the runtime version is stamped by the OTA installer.

## Signing policy

ADR-0014. Two profiles, target-driven:

| Build target | local.conf | `SWUPDATE_SIGNING` | `--sign` |
|---|---|---|---|
| `*-dev` | `config/<board>/local.conf` | recipe default `??= "0"` | off (override with `--sign`) |
| `*-prod` | `config/<board>/local.conf.prod` (which `require local.conf` then sets `SWUPDATE_SIGNING = "1"`) | `"1"` | on (auto, see `build.sh:39-41`) |

`scripts/build.sh` infers the profile from the target name. The
operator never flips a global flag; picking `server-prod` or
`camera-prod` is the signal.

Keys live at `~/.monitor-keys/ota-signing.{key,crt}` (per-operator,
never committed). Run `./scripts/generate-ota-keys.sh` once on a fresh
build host. ADR-0014 §"Key pair management" details rotation.

## Release flow

```
# 1. Prepare a release branch, bump VERSION, promote CHANGELOG.
./scripts/release.sh prepare 1.4.0

# 2. Push, open PR, merge into main (CI must be green).
git push -u origin release/1.4.0
gh pr create --fill --base main --head release/1.4.0
# … review, CI, --admin merge if needed …

# 3. Tag from main.
git checkout main && git pull
./scripts/release.sh tag 1.4.0
git push origin v1.4.0

# 4. Build production artefacts on the build host (signed).
./scripts/release.sh build 1.4.0
# Produces:
#   server-update-v1.4.0.swu, camera-update-v1.4.0.swu  (signed)
#   build/.../*.wic.bz2 + .wic.bmap                     (server SD)
#   build-zero2w/.../*.wic.bz2 + .wic.bmap              (camera SD)
#   manifests + SBOMs (.spdx.tar.zst) per image

# 5. Static-verify the .swu signatures (no hardware required).
./scripts/release.sh verify 1.4.0

# 6. Publish: gh release create + upload all artefacts.
./scripts/release.sh publish 1.4.0
```

Each subcommand is idempotent and refuses to clobber existing state
(branch, tag, release, populated CHANGELOG entry).

## What `release.sh` does NOT do

- Push tags or branches automatically — operator runs `git push`.
- Trigger remote builds via SSH/CI — `release.sh build` runs locally on
  whichever host you invoke it on; for production releases that's the
  build host with the signing keys staged.
- Deploy to running devices — that's a separate operator decision via
  Settings → Updates in the dashboard, or per the camera OTA installer.

## What human review owns

- The CHANGELOG `[X.Y.Z]` body — `release.sh prepare` promotes the
  `[Unreleased]` heading, but the bullets underneath are written by the
  release author.
- The semver bump itself — `release.sh` enforces it's a strict bump
  from the previous tag, but doesn't guess between major/minor/patch.
- The decision to ship — release.sh produces artefacts; only an
  operator decides to publish them.

## Hardware validation expectations

Per `docs/release-runbook.md` §4.6: a release isn't ready until smoke
tests pass on real hardware (server + at least one camera). Multi-sensor
acceptance criteria are pinned in `docs/issues/173`-style language and
must be verified visually on the dashboard for each connected sensor.

`./scripts/release.sh verify` is **only** static signature
verification — confirms the bundle is signed by the expected key, not
that the bundle works. Hardware smoke is still required for a real
release; it just isn't part of this script.
