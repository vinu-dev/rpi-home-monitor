# First Setup And Factory Reset Recovery

## Goal

Make server and camera first-time setup and authenticated factory reset reliable
on real devices without touching the operator PC's WiFi.

## Non-Goals

- Do not weaken authentication, signing, or pairing controls.
- Do not add SSH or CLI backdoors for admin recovery.
- Do not change the PC/laptop network configuration during validation.

## Constraints

- Authenticated GUI factory reset should return the device to setup mode using
  the setup hotspot password the operator chose during setup.
- Hardware GPIO reset remains the full wipe path and may remove setup hotspot
  password files.
- Device validation must use serial ports and device-side network paths.

## Context

- Branch: `codex/fix-first-setup-factory-reset`
- Server: COM4 and LAN `192.168.1.245`
- Camera: COM3, setup AP `HomeCam-Setup`, setup IP `10.42.0.1`
- Key files: camera/server hotspot scripts, camera/server factory reset
  services, provisioning setup UI, systemd helper services.

## Plan

1. Preserve setup hotspot PSK during authenticated factory reset.
2. Keep hardware GPIO reset wiping setup hotspot PSKs.
3. Update tests and docs for that contract.
4. Deploy to server and camera.
5. Validate reset/setup from real devices through serial/device paths.
6. Commit, push, open PR to `main`, wait for CI, merge.

## Resumption

- Current status: implementation and hardware validation complete; ready for
  final repo validation, PR, CI, and merge.
- Last completed step: camera authenticated reset was triggered through the
  live API, returned to `HomeCam-Setup`, accepted the operator-chosen setup
  hotspot password, and was restored to the home WiFi.
- Next step: run final validation gates, commit, push, open the PR, wait for
  CI, and merge.
- Branch / PR: `codex/fix-first-setup-factory-reset`, PR not opened yet.
- Devices / environments: COM3 camera serial, COM4 server serial, server LAN
  SSH for device-side validation only.
- Commands to resume: `git status --short --branch`, then run the validation
  listed below.
- Open risks / blockers: full Yocto VM build may need the configured build VM.

## Validation

- `pytest -q app/camera/tests/unit/test_privileged_helper.py app/camera/tests/integration/test_wifi_setup.py app/camera/tests/unit/test_factory_reset.py`
  passed on Windows.
- Server live deploy check: `/opt/monitor/monitor/services/backup_paths.py`
  excludes `setup-hotspot.psk` from resettable config files; server
  `setup-hotspot.psk` remains `monitor:monitor 600`; `monitor`,
  `monitor-privileged-helper`, and `monitor-hotspot.service` are active.
- Camera live deploy check over `HomeCam-Setup`: setup submission returned
  `{"status":"connecting"}` instead of the previous 500 when
  `/data/config/camera-hotspot.psk` started root-owned.
- Camera serial validation on COM3: after setup, `camera-privileged-helper`,
  `camera-streamer`, and `camera-hotspot.service` were active; setup was
  complete; `/data/config/camera-hotspot.psk` was `camera:camera 600`.
- Camera authenticated factory reset validation: `/api/factory-reset` returned
  success, the setup AP reappeared, `HomeCam-Setup` accepted the
  operator-chosen password, `/api/status` showed `setup_complete: false`, and
  `/data/config/camera-hotspot.psk` remained `camera:camera 600`.
- Camera restore validation: setup was submitted again with `MysticNet2.4`, the
  camera returned on LAN at `192.168.1.186`, `/api/status` showed
  `server_connected: true`, and serial confirmed setup complete with services
  active.
- `pytest app/camera/tests/ -v`
- `pytest app/server/tests/ -v`
- `ruff check .`
- `ruff format --check .`
- `python tools/docs/check_doc_map.py`
- `python scripts/ai/validate_repo_ai_setup.py`
- `python scripts/ai/check_doc_links.py`
- `python scripts/ai/check_shell_scripts.py`
- `python scripts/check_version_consistency.py`
- `python scripts/check_versioning_design.py`
- `python -m pre_commit run --all-files`
- hardware deploy and serial/device smoke validation for server and camera

## Risks

- Preserving the setup hotspot password during GUI reset is intentional, but
  docs and tests must clearly separate it from hardware reset semantics.
- Legacy devices without a rotated setup hotspot PSK still fall back to the
  documented factory setup password until the first setup wizard stores one.

## Completion Criteria

- GUI reset on server and camera returns to setup mode and accepts the
  operator-chosen setup hotspot password.
- Tests and repo rule checks pass or have documented environment blockers.
- PR to `main` is merged after CI passes.
