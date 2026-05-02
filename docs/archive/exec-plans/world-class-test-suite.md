# Exec Plan: World-Class Test Suite

## Goal

Elevate the automated test suite to best-in-class across every layer:
unit, integration, contract, security, E2E, and soak. Every gap identified
in the 2026-04-18 audit is addressed with real, running automation.

## Non-Goals

- No production code changes (test-only branch)
- No hardware layer changes (HIL/labgrid is a separate initiative)
- No Yocto changes

## Constraints

- All tests must pass `ruff check` and `ruff format --check`
- Server coverage must remain ≥ 80%, camera ≥ 70%
- Every new test file must use the correct layer marker
- No merge to main — user reviews branch before promoting

## Context

- Branch: `test/world-class-suite`
- Audit report: conversation session 2026-04-18
- Key files touched:
  - `app/server/tests/conftest.py` — add `logged_in_client` fixture
  - `app/server/tests/security/test_security.py` — fill CSRF stub, parametrize
  - `app/server/tests/integration/test_api_blueprints.py` — convert scaffolding
  - `app/server/tests/integration/test_api_webrtc.py` — new
  - `app/server/tests/unit/test_storage_manager.py` — new
  - `app/camera/tests/unit/test_wifi.py` — new
  - `app/server/requirements-test.txt` — add hypothesis
  - `app/camera/requirements-test.txt` — add hypothesis
  - `app/server/tests/unit/test_hypothesis_auth.py` — new
  - `app/camera/tests/unit/test_hypothesis_crypto.py` — new
  - `tests/e2e/playwright/smoke/` — expand specs
  - `tests/e2e/playwright/regression/` — expand specs
  - `tests/soak/test_reliability_hooks.py` — real scenarios
  - `.github/workflows/test.yml` — schemathesis fuzzing phase

## Plan

1. [x] Create branch `test/world-class-suite`
2. [x] Write exec plan
3. [ ] `conftest.py` — `logged_in_client` fixture; remove 13 duplicate `_login` helpers
4. [ ] `test_security.py` — fill CSRF stub; parametrize traversal + injection
5. [ ] `test.yml` — schemathesis `--phases=examples,fuzzing`
6. [ ] `test_api_blueprints.py` — convert to real route tests
7. [ ] `test_storage_manager.py` — full unit coverage
8. [ ] `test_wifi.py` (camera) — subprocess-mocked unit coverage
9. [ ] `test_api_webrtc.py` — auth gate + proxy behaviour
10. [ ] `hypothesis` — add to requirements, write property-based auth + crypto tests
11. [ ] Playwright — Settings, OTA, user management, on-demand specs
12. [ ] Soak — streaming churn, reconnect, storage pressure stubs with real assertions
13. [ ] Camera conftest — `logged_in_client` for camera security/integration tests

## Resumption

- Current status: exec plan written, branch created
- Last completed step: 2
- Next step: 3 — conftest.py logged_in_client fixture
- Branch: `test/world-class-suite`
- Commands to resume:
  ```bash
  cd <workspace>
  git checkout test/world-class-suite
  ```
- Open risks: none

## Validation

```bash
# Server
cd app/server
pytest tests/unit tests/integration tests/security tests/contracts -v
pytest tests/unit tests/integration tests/security --cov=monitor --cov-report=term-missing --cov-fail-under=80

# Camera
cd app/camera
pytest tests/unit tests/integration tests/security tests/contracts -v
pytest tests/unit tests/integration tests/security --cov=camera_streamer --cov-report=term-missing --cov-fail-under=70

# Lint
cd ../..
ruff check .
ruff format --check .

# Browser
npx playwright test --project=smoke
```

## Completion Criteria

- All existing tests still pass
- All new tests pass
- Coverage thresholds hold
- `ruff check .` clean
- No `pass`-body test methods anywhere in the security layer
- Schemathesis runs `--phases=examples,fuzzing` in CI
