# RPi Home Monitor - Testing Guide

Version: 1.0
Date: 2026-04-09

**Every code change must include tests. No exceptions.**

This guide covers how to set up the test environment, write tests,
run tests, measure coverage, and what is expected before any PR is merged.

---

## 1. Rules

1. **Every code change must have corresponding test updates.** If you add or modify code, you add or modify tests.
2. **Minimum coverage: Server 80%, Camera 55%.** PRs that drop coverage below these thresholds are blocked.
3. **Target coverage: 90%+.** Security-critical code (auth, pairing, TLS, firewall) must aim for 95%+.
4. **Tests run before every commit.** If tests fail, do not commit.
5. **No test, no merge.** Reviewers must verify test coverage in every PR.

---

## 2. Test Framework

| Tool | Purpose | Version |
|------|---------|---------|
| `pytest` | Test runner | >= 8.0 |
| `pytest-cov` | Coverage measurement (wraps coverage.py) | >= 5.0 |
| Flask test client | HTTP endpoint testing | Built into Flask |

Both apps use `pytest`. Configuration is in `pytest.ini` in each app root.

---

## 3. Project Structure

```
app/
├── server/
│   ├── monitor/                    ← Source code
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── auth.py
│   │   ├── api/
│   │   │   ├── cameras.py
│   │   │   ├── recordings.py
│   │   │   ├── live.py
│   │   │   ├── system.py
│   │   │   ├── settings.py
│   │   │   ├── users.py
│   │   │   └── ota.py
│   │   └── services/
│   │       ├── recorder.py
│   │       ├── discovery.py
│   │       ├── storage.py
│   │       ├── health.py
│   │       └── audit.py
│   ├── tests/                      ← Tests (mirrors source structure)
│   │   ├── conftest.py             ← Shared fixtures
│   │   ├── test_app.py             ← Tests for __init__.py
│   │   ├── test_models.py          ← Tests for models.py
│   │   ├── test_auth.py            ← Tests for auth.py
│   │   ├── test_api_cameras.py     ← Tests for api/cameras.py
│   │   ├── test_api_recordings.py  ← Tests for api/recordings.py
│   │   ├── test_api_live.py        ← Tests for api/live.py
│   │   ├── test_api_system.py      ← Tests for api/system.py
│   │   ├── test_api_settings.py    ← Tests for api/settings.py
│   │   ├── test_api_users.py       ← Tests for api/users.py
│   │   ├── test_api_ota.py         ← Tests for api/ota.py
│   │   ├── test_svc_recorder.py    ← Tests for services/recorder_service.py
│   │   ├── test_svc_recordings.py  ← Tests for services/recordings_service.py
│   │   ├── test_svc_discovery.py   ← Tests for services/discovery.py
│   │   ├── test_svc_storage.py     ← Tests for services/storage_manager.py
│   │   ├── test_svc_health.py      ← Tests for services/health.py
│   │   ├── test_svc_audit.py       ← Tests for services/audit.py
│   │   ├── test_api_contracts.py   ← Contract tests (Layer 4)
│   │   └── test_security.py        ← Security regression tests (adversarial)
│   ├── pytest.ini                  ← pytest config
│   ├── requirements.txt            ← Runtime deps
│   └── requirements-test.txt       ← Test deps (includes runtime)
│
└── camera/
    ├── camera_streamer/            ← Source code
    │   ├── __init__.py
    │   ├── main.py
    │   ├── config.py               ← Config + PBKDF2 password management
    │   ├── capture.py
    │   ├── stream.py
    │   ├── wifi_setup.py            ← Setup wizard + status server + sessions
    │   ├── discovery.py
    │   ├── health.py
    │   ├── led.py
    │   ├── pairing.py               ← Stub (Phase 2)
    │   ├── ota_agent.py             ← Stub (Phase 2)
    │   └── templates/               ← Camera HTML templates
    │       ├── login.html           ← Camera login page
    │       ├── setup.html           ← First-boot provisioning wizard
    │       └── status.html          ← Authenticated status dashboard
    ├── tests/                      ← Tests (mirrors source structure)
    │   ├── conftest.py             ← Shared fixtures
    │   ├── test_main.py            ← Tests for main.py
    │   ├── test_main_setup.py      ← Tests for main.py setup mode + capture failure
    │   ├── test_config.py          ← Tests for config.py + password management
    │   ├── test_capture.py         ← Tests for capture.py
    │   ├── test_stream.py          ← Tests for stream.py
    │   ├── test_wifi_setup.py      ← Tests for wifi_setup.py + sessions
    │   ├── test_discovery.py       ← Tests for discovery.py
    │   ├── test_discovery_extra.py ← Extra discovery edge cases
    │   ├── test_health.py          ← Tests for health.py
    │   ├── test_health_extra.py    ← Extra health edge cases
    │   ├── test_led.py             ← Tests for led.py
    │   ├── test_fixtures.py        ← Tests for test infrastructure
    │   └── test_package.py         ← Package import tests
    ├── pytest.ini                  ← pytest config (--cov-fail-under=55)
    ├── requirements.txt            ← Runtime deps
    └── requirements-test.txt       ← Test deps
```

**Naming convention:** Every source file `foo.py` gets a test file `test_foo.py`.
For files inside subdirectories like `api/cameras.py`, prefix with the dir: `test_api_cameras.py`.
For services: `test_svc_recorder.py`.

---

## 4. Setup

### 4.1 Install Dependencies

```bash
# Server
cd app/server
pip install -e .                      # Install the monitor package in dev mode
pip install -r requirements-test.txt  # Install pytest + pytest-cov + runtime deps

# Camera
cd app/camera
pip install -e .                      # Install the camera_streamer package in dev mode
pip install -r requirements-test.txt  # Install pytest + pytest-cov
```

### 4.2 Verify Setup

```bash
# Server — should show all tests discovered
cd app/server
pytest --collect-only

# Camera — should show all tests discovered
cd app/camera
pytest --collect-only
```

---

## 5. Running Tests

### 5.1 Run All Tests (Default — With Coverage)

The `pytest.ini` is pre-configured with coverage options. Just run:

```bash
# Server
cd app/server
pytest

# Camera
cd app/camera
pytest
```

This automatically:
- Runs all tests in `tests/`
- Measures coverage of the source package
- Shows missing lines in terminal
- **Fails if coverage drops below threshold** (server: 80%, camera: 55%)

### 5.2 Run a Specific Test File

```bash
pytest tests/test_models.py
```

### 5.3 Run a Specific Test Class

```bash
pytest tests/test_models.py::TestCamera
```

### 5.4 Run a Specific Test

```bash
pytest tests/test_models.py::TestCamera::test_create_camera_minimal
```

### 5.5 Run Tests Matching a Keyword

```bash
pytest -k "camera"          # All tests with "camera" in the name
pytest -k "not slow"        # Skip slow tests
```

### 5.6 Run with Verbose Output

```bash
pytest -v                   # Show each test name and result
pytest -vv                  # Even more detail (full diffs on failures)
```

### 5.7 Stop on First Failure

```bash
pytest -x                   # Stop after first failure
pytest --maxfail=3          # Stop after 3 failures
```

### 5.8 Run Without Coverage (Faster, for Quick Iteration)

```bash
pytest --no-cov
```

---

## 6. Coverage

### 6.1 Terminal Report (Default)

Configured in `pytest.ini`. Every test run shows:

```
Name                            Stmts   Miss  Cover   Missing
-------------------------------------------------------------
monitor\__init__.py                24      0   100%
monitor\models.py                  44      0   100%
monitor\auth.py                     2      0   100%
...
-------------------------------------------------------------
TOTAL                              84      0   100%
Required test coverage of 80% reached. Total coverage: 100.00%
```

The `Missing` column shows exact line numbers not covered by tests.

### 6.2 HTML Report (Detailed, Visual)

```bash
pytest --cov-report=html
```

Opens `htmlcov/index.html` in your browser. Each file is clickable —
green lines are covered, red lines are not. This is the best way to
find gaps in your test coverage.

```bash
# Generate and open (Linux/macOS)
pytest --cov-report=html && open htmlcov/index.html

# Generate and open (Windows)
pytest --cov-report=html && start htmlcov/index.html
```

The `htmlcov/` directory is gitignored — never commit it.

### 6.3 XML Report (For CI)

```bash
pytest --cov-report=xml
```

Generates `coverage.xml` in Cobertura format. Used by CI tools (GitHub Actions,
Jenkins) to track coverage over time and post coverage comments on PRs.

### 6.4 Coverage Thresholds

| Level | Server | Camera | Enforced By |
|-------|--------|--------|-------------|
| Minimum (blocking) | 80% | 55% | `pytest.ini` (`--cov-fail-under`) |
| Target | 90%+ | 70%+ | Code review |
| Security-critical code | 95%+ | 95%+ | Code review (auth, sessions, passwords) |

Camera has a lower threshold because `wifi_setup.py` HTTP handlers and `stream.py` ffmpeg
pipelines require real hardware (port 80, /dev/video0) that CI environments cannot provide.

### 6.5 Current Coverage

| App | Command | Threshold |
|-----|---------|-----------|
| Server (`monitor`) | `pytest app/server/tests/ -v` | 80% coverage |
| Camera (`camera_streamer`) | `pytest app/camera/tests/ -v` | 55% coverage |

Run the commands above to see current test counts and coverage. Don't hardcode counts here — they change every PR.

---

## 7. Writing Tests

### 7.1 Basic Test Structure

```python
"""Tests for the recorder service."""
from unittest.mock import patch, MagicMock


class TestRecorderService:
    """Test the RecorderService class."""

    def test_something(self):
        """Each test is a method starting with test_."""
        result = 1 + 1
        assert result == 2

    def test_with_fixture(self, data_dir):
        """Fixtures are injected by name as function arguments."""
        assert (data_dir / "recordings").exists()
```

### 7.2 Testing Flask Endpoints

Use the `client` fixture from `conftest.py`:

```python
"""Tests for the cameras API."""


class TestListCameras:
    """Test GET /api/v1/cameras."""

    def test_returns_200(self, client):
        response = client.get("/api/v1/cameras")
        assert response.status_code == 200

    def test_returns_json_list(self, client):
        response = client.get("/api/v1/cameras")
        data = response.get_json()
        assert isinstance(data, list)

    def test_returns_empty_when_no_cameras(self, client):
        response = client.get("/api/v1/cameras")
        data = response.get_json()
        assert data == []


class TestConfirmCamera:
    """Test POST /api/v1/cameras/<id>/confirm."""

    def test_requires_admin(self, client):
        response = client.post("/api/v1/cameras/cam-001/confirm")
        assert response.status_code in (401, 403)

    def test_returns_404_for_unknown_camera(self, client):
        # (with auth mocked/bypassed)
        response = client.post("/api/v1/cameras/nonexistent/confirm")
        assert response.status_code == 404
```

### 7.3 Testing with Mocked File System

Use pytest's `tmp_path` fixture — never write to real `/data`:

```python
"""Tests for storage service."""
import json


class TestStorageManager:
    """Test the StorageManager class."""

    def test_reads_cameras_json(self, data_dir):
        """Test loading camera list from JSON file."""
        cameras_file = data_dir / "config" / "cameras.json"
        cameras_file.write_text(json.dumps([
            {"id": "cam-001", "name": "Front Door"}
        ]))

        content = json.loads(cameras_file.read_text())
        assert len(content) == 1
        assert content[0]["id"] == "cam-001"

    def test_creates_recording_directory(self, data_dir):
        """Test that recording dirs are created per camera per date."""
        rec_dir = data_dir / "recordings" / "cam-001" / "2026-04-09"
        rec_dir.mkdir(parents=True)
        assert rec_dir.exists()
```

### 7.4 Testing with Mocked External Processes

Mock `subprocess` for ffmpeg, libcamera, etc:

```python
"""Tests for stream manager."""
from unittest.mock import patch, MagicMock


class TestStreamManager:
    """Test the StreamManager class."""

    @patch("camera_streamer.stream.subprocess.Popen")
    def test_starts_ffmpeg_process(self, mock_popen):
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process still running
        mock_popen.return_value = mock_process

        # Call your stream start function here
        # stream = StreamManager(config)
        # stream.start()

        # Verify ffmpeg was called
        # mock_popen.assert_called_once()

    @patch("camera_streamer.stream.subprocess.Popen")
    def test_handles_ffmpeg_crash(self, mock_popen):
        mock_process = MagicMock()
        mock_process.poll.return_value = 1  # Process exited with error
        mock_popen.return_value = mock_process

        # Test reconnection logic
```

### 7.5 Testing with Mocked System Calls

```python
"""Tests for health monitor."""
from unittest.mock import patch


class TestHealthMonitor:
    """Test system health metric collection."""

    @patch("monitor.services.health.open",
           create=True,
           return_value=__builtins__["open"].__class__(b"54200"))
    def test_reads_cpu_temperature(self):
        """CPU temp is read from /sys/class/thermal/."""
        # temp = health.get_cpu_temp()
        # assert temp == 54.2
        pass

    @patch("shutil.disk_usage")
    def test_disk_usage(self, mock_disk):
        mock_disk.return_value = (500_000_000_000, 400_000_000_000, 100_000_000_000)
        # usage = health.get_disk_usage("/data")
        # assert usage["percent"] == 80.0
```

### 7.6 Testing with Mocked Time

```python
"""Tests for audit logger."""
from unittest.mock import patch
from datetime import datetime


class TestAuditLogger:
    """Test security event logging."""

    @patch("monitor.services.audit.datetime")
    def test_logs_with_timestamp(self, mock_dt):
        mock_dt.utcnow.return_value = datetime(2026, 4, 9, 14, 30, 0)
        # Log an event and verify the timestamp
```

### 7.7 Testing Environment Variables

Use `monkeypatch` to override env vars:

```python
"""Tests for config loading."""


class TestConfig:
    """Test configuration from environment variables."""

    def test_default_data_dir(self, app):
        """Without env var, uses /data."""
        # Default is overridden by test fixture, but in production:
        assert app.config["DATA_DIR"] is not None

    def test_custom_data_dir(self, monkeypatch, data_dir):
        monkeypatch.setenv("MONITOR_DATA_DIR", str(data_dir / "custom"))
        from monitor import create_app
        app = create_app(config={"TESTING": True})
        assert app.config["DATA_DIR"] == str(data_dir / "custom")
```

---

## 8. Shared Fixtures (conftest.py)

Fixtures in `conftest.py` are automatically available to all tests in the directory.

### 8.1 Server Fixtures (`app/server/tests/conftest.py`)

| Fixture | What It Provides |
|---------|-----------------|
| `data_dir` | Temporary `/data` directory with config/, recordings/, live/, certs/, logs/ |
| `app` | Configured Flask app pointing to temp data dirs |
| `client` | Flask test client for making HTTP requests |
| `app_context` | Pushed Flask app context |
| `sample_camera` | Camera dataclass instance (Front Door, online) |
| `sample_user` | User dataclass instance (admin) |
| `sample_settings` | Settings dataclass instance (defaults) |
| `sample_clip` | Clip dataclass instance (3-min, 50MB) |
| `cameras_json` | Written cameras.json with one sample camera |
| `users_json` | Written users.json with one sample user |
| `settings_json` | Written settings.json with defaults |

### 8.2 Camera Fixtures (`app/camera/tests/conftest.py`)

| Fixture | What It Provides |
|---------|-----------------|
| `data_dir` | Temporary `/data` directory with config/, certs/, logs/ |
| `camera_config` | Written camera.conf with server IP, resolution, FPS |
| `certs_dir` | Mock client.crt, client.key, ca.crt files |

### 8.3 Adding New Fixtures

Add to `conftest.py` when a fixture is used by 2+ test files.
Keep fixtures that are only used in one file inside that test file.

```python
# In conftest.py
@pytest.fixture
def two_cameras(data_dir):
    """Write cameras.json with two cameras for multi-camera tests."""
    from dataclasses import asdict
    from monitor.models import Camera
    cameras = [
        asdict(Camera(id="cam-001", name="Front", status="online")),
        asdict(Camera(id="cam-002", name="Back", status="offline")),
    ]
    cameras_file = data_dir / "config" / "cameras.json"
    cameras_file.write_text(json.dumps(cameras))
    return cameras_file
```

---

## 9. What to Test (Checklist)

### 9.1 For Every Module

- [ ] Happy path — does it work with valid input?
- [ ] Edge cases — empty input, boundary values, None/missing fields
- [ ] Error cases — invalid input, missing files, permission errors
- [ ] Default values — are defaults applied correctly?
- [ ] Serialization — can data be written to JSON and read back?

### 9.2 For API Endpoints

- [ ] Returns correct status code (200, 201, 400, 401, 403, 404, 500)
- [ ] Returns correct JSON structure
- [ ] Handles missing/invalid request body
- [ ] Requires authentication (if applicable)
- [ ] Requires correct role (admin vs viewer)
- [ ] Rate limiting works (for auth endpoints)
- [ ] CSRF protection (for state-changing endpoints)

### 9.3 For Background Services

- [ ] Service starts and stops cleanly
- [ ] Handles external process failure (ffmpeg crash, Avahi timeout)
- [ ] Respects configuration (thresholds, intervals, modes)
- [ ] Logs expected events
- [ ] Cleanup on shutdown (kill child processes, close files)

### 9.4 For Real-World Scenarios

Every feature must include tests for these operational conditions:

- [ ] **Fresh setup (zero state)** — first boot, no config files, no users, no cameras registered, empty `/data`
- [ ] **Network reconnect** — WiFi drops mid-stream, camera disconnects and reconnects, server restarts while cameras are connected
- [ ] **Failure recovery** — corrupt JSON config, full disk during recording, service crash and restart, invalid cert files
- [ ] **Graceful degradation** — camera offline while dashboard is open, API calls during service startup, concurrent requests to same resource

These tests must simulate realistic conditions (e.g., truncated JSON files, `OSError` on disk writes), not just inject clean state. Smoke tests on hardware (`scripts/smoke-test.sh`) must cover all of the above.

### 9.5 For Security-Critical Code

- [ ] Authentication cannot be bypassed
- [ ] Session expires after timeout
- [ ] Passwords are hashed (never stored plain)
- [ ] CSRF tokens are validated
- [ ] Rate limiting enforced after threshold
- [ ] Audit events are logged for all security actions
- [ ] Input validation rejects malicious input
- [ ] Path traversal is blocked in file access
- [ ] Certificate validation is strict (no self-signed bypass in prod)

---

## 10. What to Mock vs What NOT to Mock

### Mock These (External I/O)

| What | How | Why |
|------|-----|-----|
| File system (`/data/*`) | `tmp_path` fixture | Tests must not write to real disk |
| External processes (ffmpeg, libcamera) | `unittest.mock.patch("subprocess.Popen")` | ffmpeg is not installed on dev machines |
| System info (CPU temp, disk) | `unittest.mock.patch("shutil.disk_usage")` | Values differ per machine |
| Network (Avahi/mDNS, sockets) | `unittest.mock.patch` | No real network in tests |
| Time (`datetime.now`) | `unittest.mock.patch("module.datetime")` | Deterministic timestamps |
| Environment variables | `monkeypatch.setenv()` | Isolate from host env |

### Do NOT Mock These (Test Directly)

| What | Why |
|------|-----|
| Dataclasses (Camera, User, etc.) | Pure data — no I/O, test directly |
| Flask test client | It IS the test tool — never mock it |
| JSON serialization | `json.dumps`/`json.loads` are fast and deterministic |
| Pure functions | Functions with no side effects should be tested directly |
| Config dataclass defaults | Test the actual defaults, don't mock them |

---

## 11. Naming Conventions

| Convention | Example |
|-----------|---------|
| Test file | `test_<source_module>.py` |
| Test class | `Test<ClassName>` or `Test<Feature>` |
| Test function | `test_<what_it_tests>` |
| Descriptive names | `test_returns_404_for_unknown_camera` not `test_camera_4` |
| API test files | `test_api_<blueprint>.py` (e.g., `test_api_cameras.py`) |
| Service test files | `test_svc_<service>.py` (e.g., `test_svc_recorder.py`) |
| Contract test files | `test_api_contracts.py` (one per app, Layer 4) |
| Security test files | `test_security.py` (adversarial inputs, abuse cases) |

---

## 12. PR Checklist (Testing)

Before submitting a PR, verify:

- [ ] All existing tests pass: `pytest`
- [ ] Coverage has not decreased
- [ ] New code has corresponding tests
- [ ] Coverage meets threshold (server ≥ 80%, camera ≥ 55%)
- [ ] Security-critical changes have 95%+ coverage
- [ ] No tests depend on execution order (each test is independent)
- [ ] No tests write to real file paths (use `tmp_path`)
- [ ] No tests make real network calls (use mocks)
- [ ] Test names clearly describe what they test
- [ ] `pytest` run output is included in the PR description

---

## 13. Quick Reference

```bash
# ─── Setup ───────────────────────────────────────
cd app/server && pip install -e . -r requirements-test.txt
cd app/camera && pip install -e . -r requirements-test.txt

# ─── Run All Tests ───────────────────────────────
cd app/server && pytest                  # Run to see current count (threshold: ≥80%)
cd app/camera && pytest                  # Run to see current count (threshold: ≥55%)

# ─── Run Specific Tests ─────────────────────────
pytest tests/test_models.py              # One file
pytest tests/test_models.py::TestCamera  # One class
pytest -k "auth"                         # By keyword

# ─── Coverage Reports ───────────────────────────
pytest                                   # Terminal (default)
pytest --cov-report=html                 # HTML → htmlcov/index.html
pytest --cov-report=xml                  # XML → coverage.xml (for CI)

# ─── Quick Iteration ────────────────────────────
pytest --no-cov -x tests/test_models.py  # No coverage, stop on first fail

# ─── Skip Slow / Integration Tests ──────────────
pytest -m "not slow"
pytest -m "not integration"
```
