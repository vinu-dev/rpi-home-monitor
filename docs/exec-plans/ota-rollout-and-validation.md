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
- Last completed step:
  - completed live dev deploy validation on:
    - server `192.168.1.245`
    - camera `192.168.1.186`
- Next step:
  - commit the resumability + dev deploy workflow changes
  - then begin production OTA validation:
    - signing prerequisites
    - build artifacts
    - what is already validated vs still blocked
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
  - after commit: inspect signing/build scripts and begin production OTA validation
- Open risks / blockers:
  - production OTA validation may require long Yocto builds and multiple reboots
  - signed production flow may still expose implementation gaps not visible in dev builds
  - deploy scripts must preserve permissions/ownership to avoid the static-asset regression we already hit once

## Validation

- `python scripts/ai/validate_repo_ai_setup.py`
- `python scripts/ai/check_doc_links.py`
- `pre-commit run --files docs/ai/working-agreement.md docs/exec-plans/template.md docs/exec-plans/ota-rollout-and-validation.md`
- `bash -n scripts/deploy-dev-app.sh`
- `pre-commit run --files scripts/deploy-dev-app.sh docs/development-guide.md docs/update-roadmap.md`
- `bash scripts/deploy-dev-app.sh --server 192.168.1.245 --camera 192.168.1.186`

## Risks

- resumability rules that live only in docs but are not followed in practice
- long-running hardware work diverging from the written plan
- merging multiple concerns into one branch and losing review clarity

## Completion Criteria

- repo policy explicitly requires resumable exec plans for long-running work
- the current OTA/deploy task has a live exec plan that another session can use
- the resumption changes are committed and pushed
- after that, work can continue from the exec plan instead of relying on chat memory
