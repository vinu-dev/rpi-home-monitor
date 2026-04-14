# Hardware Lab Rollout Plan

Version: 1.0
Date: 2026-04-14
Status: Active

This exec plan captures the staged rollout from the current SSH-based hardware checks to the target `labgrid`-driven hardware-in-the-loop lab.

Use this file when resuming hardware automation work after an interruption.

---

## 1. Current State

Today the repo supports:

- software-only PR gating in GitHub Actions
- top-level `pytest tests/hardware ...` entrypoints
- shell-backed hardware smoke and E2E flows
- self-hosted workflow placeholders for hardware, OTA, and soak lanes
- SSH-based access to live devices

Today the repo does **not** yet support:

- serial-captured boot evidence
- automated hard power cycling
- bootloader slot inspection through a structured fixture layer
- managed AP / Wi-Fi fault injection
- true `labgrid` target inventory in active CI use

That means current hardware automation is useful, but not yet release-grade enough to prove rollback and recovery on its own.

---

## 2. Bridge Strategy Before Full Lab

Until the full lab is installed, use a bridge model:

### Available now

- HTTPS/API checks against the server
- camera status endpoint checks
- SSH reachability checks
- systemd service health checks over SSH
- filesystem/layout checks over SSH
- shell-backed smoke and end-to-end scenarios

### Deferred until lab hardware is available

- serial console capture during boot/update/rollback
- automated power cut / cold boot recovery
- AP teardown / network churn orchestration
- deterministic rollback proof when network never returns

This bridge model is good enough for:

- nightly smoke
- operator-triggered validation
- early OTA workflow bring-up

It is not enough for:

- final “gold standard” release proof
- unattended rollback confidence
- root-cause diagnosis of failed boots

---

## 3. Required Final Lab Inventory

Each DUT group should eventually expose:

- management name
- IP address or hostname
- SSH username
- serial device path
- controllable power endpoint
- board role (`server` or `camera`)

Minimum target inventory:

- `server-dut`
- `camera-dut-1`

Recommended expansion:

- `camera-dut-2` for multi-camera concurrency
- dedicated AP / Wi-Fi controller resource
- optional USB mass storage / OTA media injection resource

---

## 4. Environment Model During Bridge Phase

The bridge-phase SSH suite expects or can use:

- `HIL_SERVER`
- `HIL_CAMERA`
- `HIL_SERVER_PASSWORD`
- `HIL_CAMERA_PASSWORD`
- `HIL_SERVER_SSH_USER` default `root`
- `HIL_CAMERA_SSH_USER` default `root`
- `HIL_SERVER_SSH_PORT` default `22`
- `HIL_CAMERA_SSH_PORT` default `22`
- `WIFI_SSID`
- `WIFI_PASSWORD`

Assumption:

- the self-hosted runner already has SSH key-based access to the devices
- the password environment variables are for UI/API login and setup flows, not SSH authentication

---

## 5. Rollout Phases

### Phase A: SSH-only hardware validation

Target outcome:

- `pytest tests/hardware` verifies live API status plus basic systemd/filesystem health over SSH
- smoke and E2E shell flows remain callable through pytest

Required tests:

- server SSH reachable
- camera SSH reachable
- `monitor` active
- `mediamtx` active
- `camera-streamer` active
- required `/data` directories exist
- optional journald scrape on failure

### Phase B: Structured hardware fixtures

Target outcome:

- shared pytest helpers wrap SSH, HTTP polling, and log capture
- shell scripts stop being the only implementation path

Required work:

- `tests/hardware/conftest.py`
- helper functions for SSH, HTTP readiness, journald capture
- artifact capture directory for failed runs

### Phase C: Power-aware lab

Target outcome:

- each DUT can be cold-booted and reset from CI

Required hardware:

- smart plug / relay / PDU per DUT
- API credentials stored on the lab runner

Required tests:

- power cycle recovery
- boot timeout detection
- service readiness after cold boot

### Phase D: Serial-aware lab

Target outcome:

- failed update and rollback paths are diagnosable and machine-verifiable

Required hardware:

- serial adapter for each DUT
- stable naming on the lab host

Required tests:

- boot log capture
- slot selection confirmation
- rollback confirmation when SSH never returns

### Phase E: Full `labgrid` adoption

Target outcome:

- hardware inventory is modeled as resources and driven by pytest fixtures

Required work:

- replace ad hoc SSH commands with `labgrid` fixtures
- make release workflows consume structured DUT groups
- store serial and power artifacts on every failure

---

## 6. Resume Checklist

When resuming hardware automation work:

1. Confirm the self-hosted runner still has SSH access to each DUT.
2. Run `pytest tests/hardware -v` with the bridge-phase environment variables.
3. Confirm which power-control hardware has been purchased and installed.
4. Confirm whether serial console hardware is present and mapped stably.
5. Update `tests/hardware/labgrid/` with the latest inventory.
6. Replace one shell-backed scenario at a time with native pytest fixture logic.

---

## 7. Success Criteria

Bridge phase success:

- nightly hardware smoke runs without manual SSH steps
- failures capture enough information to distinguish API, service, and connectivity issues

Gold-standard success:

- release pipeline can prove OTA install, reboot, post-boot health, and rollback without operator intervention
- failure artifacts include serial logs, service logs, and power-cycle history
