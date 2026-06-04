# First Setup And Factory Reset Recovery

## Goal

Make server and camera first-time setup and authenticated factory reset reliable
on real devices without touching the operator PC's WiFi.

## Non-Goals

- Do not weaken authentication, signing, or pairing controls.
- Do not add SSH or CLI backdoors for admin recovery.
- Do not change the PC/laptop network configuration during validation.

## Constraints

- Authenticated GUI factory reset should return the device to setup mode.
- Server reset uses the image default setup hotspot credential and preserves
  the build-time provisioning CA under `/etc/home-monitor/trust`.
- Camera reset may continue using the camera setup hotspot password the
  operator chose during camera setup until that flow is redesigned.
- Hardware GPIO reset remains a full wipe path for runtime state.
- Device validation must use serial ports and device-side network paths.

## Superseded Server Credential Note

This plan predates the certificate-gated server setup design. Current server
setup no longer asks for a rotated setup hotspot password, and server factory
reset now wipes any legacy `/data/config/setup-hotspot.psk` so the server
returns to the image default hotspot credential. The build-time provisioning CA
under `/etc/home-monitor/trust` remains installed because a freshly flashed
server image already contains that trust anchor.

## Context

- Branch: `codex/fix-first-setup-factory-reset`
- Server: COM4 and LAN `192.168.1.245`
- Camera: COM3, setup AP `HomeCam-Setup`, setup IP `10.42.0.1`
- Key files: camera/server hotspot scripts, camera/server factory reset
  services, provisioning setup UI, systemd helper services.

## Plan

1. Preserve the camera setup hotspot PSK during camera authenticated reset.
2. Wipe the server setup hotspot PSK during server factory reset.
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
- Server live deploy check: factory reset wipes legacy
  `/data/config/setup-hotspot.psk`, preserves `/etc/home-monitor/trust`, and
  leaves `monitor`, `monitor-privileged-helper`, and `monitor-hotspot.service`
  active after recovery.
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

- Server and camera setup hotspot credential behavior now differs; docs and
  tests must clearly separate the server certificate-gated setup path from the
  older camera setup path.
- Legacy server devices with a rotated setup hotspot PSK return to the
  documented factory setup password after factory reset.

## Completion Criteria

- GUI reset on server returns to cert-gated setup with the factory setup
  hotspot credential and the build-time provisioning CA preserved.
- GUI reset on camera returns to setup mode and accepts the operator-chosen
  camera setup hotspot password.
- Tests and repo rule checks pass or have documented environment blockers.
- PR to `main` is merged after CI passes.
