# RPi Home Monitor - Testing Guide

Version: 2.0
Date: 2026-04-14

**Release confidence is defined by automated test layers, not by manual smoke testing.**

This guide documents the layered test structure, the required markers, the CI promotion lanes, and the automated-only testing policy for this repository.

---

## 1. Rules

1. Every code change must include test updates in the correct layer.
2. Manual testing is optional exploratory work, not a required merge or release gate.
3. PRs must pass automated software-only gates before merge.
4. Nightly runs must validate image/runtime behavior and hardware workflows.
5. Release promotion is allowed only when all required automated gates pass.
6. Coverage thresholds remain enforced, but they are secondary to critical-path automation.

---

## 2. Test Layers

The repository now uses explicit test layers instead of a single mixed `pytest` bucket.

```text
app/server/tests/
  unit/
  integration/
  contracts/
  security/

app/camera/tests/
  unit/
  integration/
  contracts/
  security/

tests/
  e2e/playwright/
  hardware/
  soak/
  yocto/
```

### Layer definitions

| Layer | Scope | What belongs here |
|------|-------|-------------------|
| `unit` | Pure logic | No real network, processes, or hardware |
| `integration` | Service/component behavior | Flask test client, lifecycle orchestration, temp filesystem, mocked external binaries |
| `contract` | Interface correctness | OpenAPI validation, response-shape checks, Schemathesis |
| `security` | Adversarial and security regression | Auth, TLS, sessions, CSRF, pairing, abuse paths |
| `e2e` | Browser automation | Playwright user-visible flows only |
| `hardware` | Real device automation | Pytest-driven HIL with lab inventory and artifacts |
| `soak` | Reliability and endurance | Long-running and churn scenarios |
| `ota` | Cross-layer capability marker | OTA and rollback scenarios on contract, hardware, or soak suites |

### Marker rules

The following markers are required and registered:

- `unit`
- `integration`
- `contract`
- `security`
- `e2e`
- `hardware`
- `ota`
- `slow`
- `soak`

Collection hooks automatically apply the expected layer marker based on directory and fail when conflicting layer markers are present.

---

## 3. Running Tests Locally

### 3.1 Install dependencies

```bash
# Server
cd app/server
pip install -e . -r requirements-test.txt

# Camera
cd app/camera
pip install -e . -r requirements-test.txt

# Browser E2E
cd ../..
npm install
npx playwright install --with-deps chromium
```

The camera test requirements include `cryptography` so integration tests can
exercise the HTTPS certificate fallback path when `openssl` is absent or mocked
unavailable.

### 3.2 Run by layer

```bash
# Server
cd app/server
pytest tests/unit
pytest tests/integration
pytest tests/contracts
pytest tests/security

# Camera
cd app/camera
pytest tests/unit
pytest tests/integration
pytest tests/contracts
pytest tests/security

# Top-level automation
cd ../..
pytest tests/hardware
pytest tests/yocto
pytest tests/soak
npx playwright test --project=smoke
```

### 3.3 Coverage runs

Coverage is still enforced, but in dedicated aggregate jobs and local commands instead of every split lane.

```bash
# Server aggregate coverage
cd app/server
pytest tests/unit tests/integration tests/security \
  --cov=monitor --cov-report=term-missing --cov-fail-under=80

# Camera aggregate coverage
cd app/camera
pytest tests/unit tests/integration tests/security \
  --cov=camera_streamer --cov-report=term-missing --cov-fail-under=70
```

### 3.4 Contract and schema automation

```bash
# Server contract tests
pytest app/server/tests/contracts
schemathesis run openapi/server.yaml --url https://127.0.0.1:5443 --tls-verify=false

# Camera contract tests
pytest app/camera/tests/contracts
schemathesis run openapi/camera.yaml --url https://127.0.0.1:5444 --tls-verify=false
```

---

## 4. Browser Automation

Playwright lives under `tests/e2e/playwright/` and is split into:

- `auth/` for bootstrap/auth setup
- `smoke/` for critical PR gating
- `regression/` for broader nightly coverage

Current project model:

- `setup`: prepares seeded auth/bootstrap state
- `smoke`: critical-path browser checks for PRs
- `full`: broader regression coverage for nightly runs

Artifacts:

- traces on failure
- screenshots on failure
- HTML report
- optional video for longer nightly runs

---

## 5. Contract Automation

The contract layer combines:

- hand-written response-shape checks for critical routes
- checked-in OpenAPI specs in `openapi/`
- Schemathesis schema-driven API execution against local test-mode app instances

Any new user-visible API endpoint must update the OpenAPI spec and include either:

- a contract test, or
- an integration/browser test that proves the new behavior

---

## 6. Hardware Automation

Hardware automation is moving to `pytest tests/hardware ...` as the authoritative path.

Shell scripts such as `scripts/smoke-test.sh` and `scripts/e2e-smoke-test.sh` remain fallback operator tools only.

Current bridge-phase implementation:

- SSH-based service health checks
- SSH-based runtime layout checks
- smoke and end-to-end script execution through pytest
- automatic journald capture into `artifacts/hardware/` when a hardware test fails

Bridge-phase environment variables:

- `HIL_SERVER`
- `HIL_CAMERA`
- `HIL_SERVER_PASSWORD`
- `HIL_CAMERA_PASSWORD`
- `HIL_SERVER_SSH_USER`
- `HIL_CAMERA_SSH_USER`
- `HIL_SERVER_SSH_PORT`
- `HIL_CAMERA_SSH_PORT`
- `HIL_ARTIFACT_DIR`
- `WIFI_SSID`
- `WIFI_PASSWORD`

The target hardware model is:

- self-hosted GitHub Actions runner on the lab host
- `labgrid` inventory and pytest integration
- power control per DUT
- serial console capture
- isolated Wi-Fi/AP setup for provisioning tests
- structured failure artifacts for every run

Required hardware scenarios include:

- fresh server provisioning
- fresh camera provisioning
- pairing and discovery
- streaming and recording validation
- reconnect and reboot recovery
- storage pressure behavior
- certificate revocation after unpair
- OTA install, health check, and rollback

---

## 7. Yocto and Runtime Validation

Yocto and image validation live under `tests/yocto/` and in dedicated workflows.

Required automation model:

- parse/build checks on PRs that touch Yocto or update paths
- nightly image builds
- QEMU `testimage` runtime validation
- release-time signed artifact validation before hardware promotion gates

---

## 8. CI Promotion Lanes

### PR CI

- `repo-governance`
- `pre-commit`
- `workflow-lint`
- `shell-lint`
- `lint`
- `server-unit`
- `server-integration`
- `server-contract`
- `server-security`
- `server-coverage`
- `camera-unit`
- `camera-integration`
- `camera-contract`
- `camera-security`
- `camera-coverage`
- `browser-e2e-smoke`
- `yocto-parse` when relevant paths change

### Nightly CI

- full server and camera layered suites
- Playwright full regression
- Yocto image build and runtime hooks
- hardware smoke lane
- OTA smoke lane
- soak subset

### Release Validation

- clean rebuild from source
- signed bundle validation
- server OTA install gate
- camera OTA install gate
- rollback gate
- post-update health confirmation
- publish only if every required automated gate passes

---

## 9. Developer Expectations

Before opening a PR:

- run the relevant unit/integration/security layer locally
- update OpenAPI when API behavior changes
- add or update Playwright coverage for user-visible flow changes
- add hardware/OTA coverage when device behavior changes
- keep manual testing notes out of the required gate path

If you touch auth, pairing, TLS, OTA, or provisioning, your change should include:

- unit coverage
- integration coverage
- contract validation
- security regression coverage
- nightly hardware coverage if runtime behavior changed on device

---

## 10. Quick Reference

```bash
# Server layers
cd app/server
pytest tests/unit
pytest tests/integration
pytest tests/contracts
pytest tests/security
pytest tests/unit tests/integration tests/security --cov=monitor --cov-report=term-missing --cov-fail-under=80

# Camera layers
cd app/camera
pytest tests/unit
pytest tests/integration
pytest tests/contracts
pytest tests/security
pytest tests/unit tests/integration tests/security --cov=camera_streamer --cov-report=term-missing --cov-fail-under=70

# Browser
cd ../..
npx playwright test --project=smoke
npx playwright test --project=full

# Top-level automation
pytest tests/yocto
pytest tests/hardware
pytest tests/soak
```
