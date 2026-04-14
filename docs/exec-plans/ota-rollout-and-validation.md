# OTA Rollout And Validation

## Goal

Finish the update workflow in the right order:

1. merge the OTA/status documentation cleanup
2. implement a safe scripted dev app-only deploy path
3. validate the real production OTA flow on hardware

This plan is the durable handoff record for the work.

## Non-Goals

- changing the product update architecture itself
- pretending production OTA is fully ready before validation proves it
- replacing the existing Yocto/OTA stack with a different framework

## Constraints

- server and camera are real Raspberry Pi devices on the local network
- dev builds may intentionally bypass signing (`SWUPDATE_SIGNING = "0"`)
- production claims must match hardware validation, not just code presence
- hardware work must remain resumable across session limits

## Context

- Repo: `C:\Users\vinun\codex\rpi-home-monitor`
- Latest merged doc/status PR: `#52`
- Latest merged `main` seen during this task: `4d701e9`
- Current working branch for resumability work: `codex/add-resumption-workflow`
- Server device: `192.168.1.245`, `root` SSH available
- Camera device: `192.168.1.186`, `root` SSH available
- Build VM:
  - host: `35.197.216.132`
  - camera workspace: `/home/vinu_emailme/yocto-camera/`
  - server workspace: `/home/vinu_emailme/yocto-server/`

## Plan

1. add durable resumption rules and an active exec plan
2. merge the OTA/status truth-fix PR and sync `main`
3. implement a scripted dev app-only deploy/update workflow
4. validate the scripted dev deploy on server and camera
5. start production OTA validation:
   - signing prerequisites
   - build artifacts
   - full-system update path
   - rollback checks
6. update docs with exact validated status

## Resumption

- Current status:
  - PR `#52` has already been merged to `main`
  - OTA/update docs now distinguish dev flow vs production readiness
  - resumability rules and exec-plan workflow have been added on this branch
  - scripted dev app deploy flow now exists at `scripts/deploy-dev-app.sh`
  - scripted dev deploy has been validated on live server and camera hardware
  - `scripts/build.sh` has been fixed to work with `oe-init-build-env` under `set -u`
  - clean production validation workspace created on the build VM at `/home/vinu_emailme/ota-validation`
  - signed `server-prod` Yocto build completed successfully in the clean validation workspace
  - signed `camera-prod` Yocto build completed successfully in the same clean validation workspace
  - OTA signing material has been rotated from the old Ed25519 assumption to the validated ECDSA P-256 CMS flow
  - signed server `.swu` packaging now succeeds from the clean validation workspace
  - signed camera `.swu` packaging now succeeds from the clean validation workspace
  - `scripts/build-swu.sh` is executable in the repo checkout and can be run directly as documented
  - encrypted local backup of the active OTA signing keypair now exists
  - GitHub Actions secrets `OTA_SIGNING_KEY` and `OTA_SIGNING_CERT` are populated for `vinu-dev/rpi-home-monitor`
  - both signed `.swu` bundles were copied to the live server and camera and installed successfully with SWUpdate
  - after reboot, neither live device returned on the expected SSH/HTTPS addresses within the current polling window
- Last completed step:
  - completed signed `server-prod` and `camera-prod` build validation on the VM with:
    - `SWUPDATE_SIGNING = "1"` in `config/rpi4b/local.conf`
    - `SWUPDATE_SIGNING = "1"` in `config/zero2w/local.conf`
    - local OTA signing cert/key copied to `~/.monitor-keys/` on the VM
    - `./scripts/build-swu.sh --target server ... --sign` producing `server-update-1.1.0-20260414.swu`
    - `./scripts/build-swu.sh --target camera ... --sign` producing `camera-update-1.1.0-20260414.swu`
    - encrypted OTA key backup generated via `scripts/backup-ota-keys.sh`
    - GitHub Actions OTA signing secrets published via `scripts/publish-ota-github-secrets.sh`
    - `swupdate -i` on both live devices reporting successful slot switches
- Next step:
  - determine whether the devices are simply on a different network surface or whether the new production images are not joining the LAN
  - if the network surface is gone, use console/recovery access to inspect boot state and rollback behavior
  - verify the signed `.swu` bundles apply correctly on server and camera once the devices are reachable again
  - record the exact validated full-system update path and remaining hardware gaps
- Branch / PR:
  - current branch: `codex/add-resumption-workflow`
  - next PR: not created yet
- Devices / environments:
  - server `root@192.168.1.245`
  - camera `root@192.168.1.186`
  - build VM `vinu_emailme@35.197.216.132`
- Commands to resume:
  - `git status --short --branch`
  - `python scripts/ai/validate_repo_ai_setup.py`
  - `python scripts/ai/check_doc_links.py`
  - `pre-commit run --files docs/ai/working-agreement.md docs/exec-plans/template.md docs/exec-plans/ota-rollout-and-validation.md scripts/deploy-dev-app.sh docs/development-guide.md docs/update-roadmap.md`
  - `bash scripts/deploy-dev-app.sh --server 192.168.1.245 --camera 192.168.1.186`
  - `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && grep -n SWUPDATE_SIGNING config/rpi4b/local.conf config/zero2w/local.conf"`
  - `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && ./scripts/build.sh server-prod"`
  - `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && ./scripts/build.sh camera-prod"`
  - `ssh vinu_emailme@35.197.216.132 "tail -n 50 /home/vinu_emailme/ota-validation/camera-prod.log"`
  - `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && ./scripts/build-swu.sh --target server --rootfs build/tmp-glibc/deploy/images/raspberrypi4-64/home-monitor-image-prod-raspberrypi4-64.rootfs-20260414093826.ext4.gz --sign"`
  - `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && ./scripts/build-swu.sh --target camera --rootfs build-zero2w/tmp-glibc/deploy/images/home-monitor-camera/home-camera-image-prod-home-monitor-camera.rootfs-20260414075047.ext4.gz --sign"`
  - `ssh root@192.168.1.245 "swupdate -i /data/ota/server-update-1.1.0-20260414.swu"`
  - `ssh root@192.168.1.186 "swupdate -i /data/ota/camera-update-1.1.0-20260414.swu"`
  - `ssh root@192.168.1.245 "reboot"`
  - `ssh root@192.168.1.186 "reboot"`
- Open risks / blockers:
  - production OTA validation may require long Yocto builds and multiple reboots
  - signed production flow may still expose implementation gaps not visible in dev builds
  - deploy scripts must preserve permissions/ownership to avoid the static-asset regression we already hit once
  - OTA docs still contain some drift between older signing text and the `build-swu.sh`-based path, so doc updates should follow the validated artifact flow rather than assumptions
  - production device images must be rebuilt with the rotated ECDSA P-256 cert before on-device signature verification can be called validated

## Validation

- `python scripts/ai/validate_repo_ai_setup.py`
- `python scripts/ai/check_doc_links.py`
- `pre-commit run --files docs/ai/working-agreement.md docs/exec-plans/template.md docs/exec-plans/ota-rollout-and-validation.md`
- `bash -n scripts/deploy-dev-app.sh`
- `pre-commit run --files scripts/deploy-dev-app.sh docs/development-guide.md docs/update-roadmap.md`
- `bash scripts/deploy-dev-app.sh --server 192.168.1.245 --camera 192.168.1.186`
- `pre-commit run --files scripts/build.sh`
- `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && ./scripts/build.sh server-prod"`
- `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && ./scripts/build.sh camera-prod"`
- `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && ./scripts/build-swu.sh --target server --rootfs build/tmp-glibc/deploy/images/raspberrypi4-64/home-monitor-image-prod-raspberrypi4-64.rootfs-20260414093826.ext4.gz --sign"`
- `ssh vinu_emailme@35.197.216.132 "cd /home/vinu_emailme/ota-validation && ./scripts/build-swu.sh --target camera --rootfs build-zero2w/tmp-glibc/deploy/images/home-monitor-camera/home-camera-image-prod-home-monitor-camera.rootfs-20260414075047.ext4.gz --sign"`

## Risks

- resumability rules that live only in docs but are not followed in practice
- long-running hardware work diverging from the written plan
- merging multiple concerns into one branch and losing review clarity
- executable-bit drift on repo scripts can break the documented fresh-clone command path if not kept in Git
- live OTA validation can leave devices unreachable until the right boot slot or recovery path is confirmed

## Completion Criteria

- repo policy explicitly requires resumable exec plans for long-running work
- the current OTA/deploy task has a live exec plan that another session can use
- the resumption changes are committed and pushed
- after that, work can continue from the exec plan instead of relying on chat memory
