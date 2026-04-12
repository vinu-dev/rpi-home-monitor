# RPi Home Monitor - Development Guide

Version: 1.0
Date: 2026-04-09

**This document defines the rules for all development on this project.**
Every contributor (human or AI) must follow these rules. They are not
suggestions — they are mandatory for maintaining a production-quality codebase.

---

## 1. Git Workflow

### 1.1 Branch Strategy

```
main                         ← Always deployable. Protected.
  ├── feature/camera-pairing ← New features
  ├── fix/storage-cleanup    ← Bug fixes
  ├── recipe/add-swupdate    ← Yocto recipe changes
  ├── docs/update-api-spec   ← Documentation only
  └── release/v1.1.0         ← Release preparation
```

**Rules:**
- **NEVER commit directly to `main`.** All changes go through feature branches + pull requests.
- Branch naming: `<type>/<short-description>` where type is one of:
  - `feature/` — new functionality
  - `fix/` — bug fix
  - `recipe/` — Yocto recipe additions or changes
  - `config/` — build configuration changes
  - `docs/` — documentation only
  - `refactor/` — code restructuring with no behavior change
  - `security/` — security fixes (prioritize these)
  - `release/` — release preparation
- Branch from `main`, merge back to `main` via PR.
- Delete branch after merge.

### 1.2 Commit Rules

- **One logical change per commit.** Don't mix recipe changes with app changes.
- **Commit message format:**
  ```
  <type>: <short summary in imperative mood>

  <optional body explaining WHY, not what>

  Refs: #<issue-number> (if applicable)
  ```
  Types: `feat`, `fix`, `recipe`, `config`, `docs`, `refactor`, `security`, `test`, `chore`
- **Examples:**
  ```
  feat: add camera discovery via mDNS
  fix: prevent storage cleanup from deleting clips less than 24h old
  recipe: add swupdate to server image for OTA support
  config: pin OpenSSL to 3.5.x in distro config
  security: enforce mTLS on RTSP camera connections
  ```
- **Never use** `--no-verify`, `--force`, or `--amend` on shared branches.
- **Never rewrite history** on `main` or any branch others may have pulled.

### 1.3 Pull Request Process

1. Create branch from latest `main`
2. Make changes, commit with proper messages
3. Push branch, open PR
4. PR description must include:
   - **What** changed (summary)
   - **Why** it changed (motivation)
   - **How to test** (steps to verify)
   - **Yocto impact** — does this require a rebuild? Which image?
5. Self-review the diff before requesting review
6. After merge, delete the branch

### 1.4 Release Process

1. Create `release/vX.Y.Z` branch from `main`
2. Update version numbers:
   - `meta-home-monitor/conf/distro/home-monitor.conf` → `DISTRO_VERSION`
   - `app/server/setup.py` → `version`
   - `app/camera/setup.py` → `version`
3. Build all 4 images (server-dev, server-prod, camera-dev, camera-prod)
4. Test dev images on hardware
5. Merge to `main`, tag `vX.Y.Z`
6. Create GitHub Release with prod images attached
7. **Semantic versioning:**
   - `MAJOR` — breaking changes (partition layout, API incompatible)
   - `MINOR` — new features (new API endpoints, new packages)
   - `PATCH` — bug fixes, security patches

---

## 2. Yocto Rules

### 2.1 Distro Policy

- **All system policy lives in `home-monitor.conf`.** Never put DISTRO_FEATURES, init manager, or package classes in `local.conf`.
- **`local.conf` is machine-specific only:** MACHINE, GPU_MEM, WiFi firmware, CPU threads. Nothing else.
- **Never use `DISTRO = "poky"`.** Always `DISTRO = "home-monitor"`.
- **Pin critical package versions** in the distro config. If you upgrade a version, test the full image.

### 2.2 Layer Rules

- **All custom recipes go in `meta-home-monitor`.** Never modify upstream layers (poky, meta-raspberrypi, meta-openembedded).
- **To customize an upstream recipe, use a `.bbappend`** in meta-home-monitor. Never fork the recipe.
- **Layer priority is 10** (higher than defaults). This is intentional — our customizations win.
- **LAYERSERIES_COMPAT must match the release.** Currently `scarthgap`. Update when upgrading Yocto.
- **Never add a layer dependency** without verifying it's available and compatible with scarthgap.

### 2.3 Recipe Rules

- **Every recipe must have:**
  - `SUMMARY` — one line, what it does
  - `DESCRIPTION` — detailed purpose
  - `LICENSE` and `LIC_FILES_CHKSUM` — mandatory, even for our own code
  - `RDEPENDS` — explicit runtime dependencies (don't rely on implicit)
- **Recipe naming:** `<package-name>_<version>.bb` (e.g., `monitor-server_1.0.bb`)
- **Use `inherit` properly:** `systemd` for services, `packagegroup` for groups, `setuptools3` for Python packages.
- **SRC_URI:** Always use `file://` for local files, full URLs for remote. Never use `git://` (use `https://`).
- **Never use `DEPENDS` when you mean `RDEPENDS`.** DEPENDS = build-time, RDEPENDS = runtime.
- **Test recipe changes with:** `bitbake <recipe> -c cleansstate && bitbake <recipe>` before committing.

### 2.4 Image Rules

- **Shared content goes in `.inc` files.** Image variants (dev/prod) are thin `.bb` files that `require` the `.inc`.
- **Never add packages directly to image `.bb` files** unless they're variant-specific (e.g., gdb in dev only). Add them to the appropriate packagegroup instead.
- **Packagegroup naming:** `packagegroup-<product>-<function>.bb` (e.g., `packagegroup-monitor-security.bb`)
- **Dev image rules:**
  - Includes `debug-tweaks` (root login, no password)
  - Includes dev tools (gdb, strace, tcpdump)
  - Used for development and testing only
  - **Never flash a dev image to a production device**
- **Prod image rules:**
  - No `debug-tweaks`
  - No root password, key-only SSH
  - No dev tools
  - This is what ships to real devices

### 2.5 Build Verification

Before any Yocto change is committed:

1. **Parse check:** `bitbake -p <image>` — must show 0 errors
2. **Dry-run dependency check:** `bitbake -g <image>` — verify dependency graph
3. **Full build** for affected images (at least once before merging to main)
4. **Boot test** on hardware or QEMU (for significant changes)

### 2.6 Configuration Management

- **`bblayers.conf`** is shared between all machines. One file, committed to git.
- **Adding a new layer:**
  1. Add clone command to `scripts/build.sh`
  2. Add to `config/bblayers.conf`
  3. Add to `LAYERDEPENDS` in `layer.conf` if required
  4. Document in README.md
  5. Verify: `bitbake-layers show-layers`
- **Adding a new machine:**
  1. Create `config/<machine>/local.conf` (minimal, machine-specific only)
  2. Add build target to `scripts/build.sh`
  3. Create image `.inc` and dev/prod `.bb` files if needed
  4. Document in README.md

---

## 3. Application Development Rules

### 3.1 Code Organization

- **Application code lives in `app/`, never in Yocto recipe `files/` directories.**
- **Yocto recipes reference `app/` via `FILESEXTRAPATHS`.** The recipe is just packaging.
- **Server app structure:**
  ```
  app/server/monitor/
    __init__.py          ← App factory (create_app)
    auth.py              ← Auth module
    models.py            ← Data classes
    api/<blueprint>.py   ← One file per API area
    services/<service>.py ← One file per background service
    templates/<page>.html ← One file per page
    static/              ← CSS, JS (no build tools)
  ```
- **Camera app structure:**
  ```
  app/camera/camera_streamer/
    main.py              ← Entry point
    lifecycle.py         ← State machine (INIT→SETUP→PAIRING→...→RUNNING)
    config.py            ← Config + PBKDF2 password management
    capture.py           ← V4L2 camera detection
    stream.py            ← FFmpeg RTSPS streaming
    wifi_setup.py        ← First-boot WiFi hotspot + setup wizard
    wifi.py              ← WiFi connection management
    status_server.py     ← Post-setup status/admin server
    discovery.py         ← Avahi mDNS advertisement
    pairing.py           ← PIN-based cert exchange with server
    ota_agent.py         ← OTA update listener (port 8080, mTLS)
    factory_reset.py     ← WiFi wipe + config reset
    encryption.py        ← LUKS key derivation
    health.py            ← CPU/RAM/uptime monitoring
    led.py               ← ACT LED patterns
    platform.py          ← Hardware path abstraction
    logging_config.py    ← Logging setup
    templates/           ← HTML templates (login, setup, status)
  ```

### 3.2 Python Rules

- **Python 3.10+ minimum.** Use type hints on all function signatures.
- **No external dependencies beyond what Yocto provides.** Check `bitbake -s | grep python3-` before adding any import. If it's not in Yocto, don't use it.
- **Flask app factory pattern** — always use `create_app()`, never module-level app.
- **Blueprints for API organization** — one blueprint per resource area.
- **No ORM, no SQLAlchemy, no database.** JSON files on `/data` partition.
- **File I/O must use `/data/` paths** (from environment variables). Never hardcode paths.
- **All config from environment variables or `/data/config/`.** Never hardcode IPs, ports, or credentials.
- **Error handling:**
  - Catch specific exceptions, never bare `except:`.
  - Log errors with context (camera ID, file path, etc.).
  - API errors return proper JSON with HTTP status codes.
  - Never expose internal errors to the user (no tracebacks in API responses).
- **Logging:**
  - Use Python's `logging` module, never `print()`.
  - Log levels: DEBUG (dev only), INFO (normal operations), WARNING (recoverable), ERROR (failures), CRITICAL (system down).
  - All log output goes to journald via systemd.

### 3.3 Security Rules (Non-Negotiable)

These rules apply to ALL code, whether application or recipe:

- **Never store passwords in plaintext.** Server uses bcrypt (cost 12). Camera uses PBKDF2-SHA256 (100k iterations, random salt).
- **Never log passwords, tokens, or certificates.** Redact in log messages.
- **Never hardcode secrets** (passwords, keys, tokens) in source code or recipes.
- **All user input is untrusted.** Validate and sanitize everything from HTTP requests.
- **SQL injection is not possible** (no SQL), but **command injection is.** Never pass user input to `subprocess`, `os.system`, or shell commands without proper escaping. Use `subprocess.run()` with list arguments, never shell=True with user data.
- **Path traversal:** Validate all file paths from user input. Never allow `../` in clip filenames or camera IDs.
- **CSRF tokens on all state-changing endpoints** (POST, PUT, DELETE).
- **Session cookies:** Always `Secure`, `HttpOnly`, `SameSite=Strict`.
- **TLS:** All network communication must use TLS. No exceptions, no "we'll add it later."
- **Secrets on disk** (users.json, certs) must be on the LUKS-encrypted `/data` partition with restrictive file permissions (0600 for keys, 0640 for config).

### 3.4 API Design Rules

- **Prefix all API routes with `/api/v1/`.** Version from day one.
- **RESTful conventions:**
  - `GET` = read (safe, idempotent)
  - `POST` = create
  - `PUT` = update (full replace)
  - `PATCH` = partial update
  - `DELETE` = remove
- **Response format:** Always JSON. Use consistent structure:
  ```json
  {"status": "ok", "data": {...}}
  {"status": "error", "message": "human-readable error"}
  ```
- **HTTP status codes:** 200 (ok), 201 (created), 400 (bad request), 401 (unauthorized), 403 (forbidden), 404 (not found), 429 (rate limited), 500 (server error).
- **Auth required on ALL endpoints** except `POST /api/v1/auth/login` and the first-boot setup.
- **Admin-only endpoints** must check role. A viewer hitting an admin endpoint gets 403, not a different error.
- **When adding a new endpoint:**
  1. Add to the blueprint in `app/server/monitor/api/`
  2. Update `docs/requirements.md` (SR-SRV-12: REST API section)
  3. Add auth decorator and role check
  4. Add audit log entry for state-changing operations

### 3.5 Frontend Rules

- **Mobile-first.** Design for phone screens, scale up to desktop.
- **No build tools** (no webpack, no npm, no node_modules). Plain HTML, CSS, JS.
- **No frontend frameworks** (no React, no Vue). Vanilla JS + Jinja2 templates.
- **Dark theme by default** (camera monitoring UIs are used in dim rooms).
- **HLS.js is the only external JS library** (for live video playback).
- **All pages must work without JavaScript** for basic content. JS enhances (auto-refresh, live video) but pages should degrade gracefully.
- **No CDN dependencies.** All assets served locally. The system works without internet.

### 3.6 Design Patterns & Architecture Rules

This project follows a small, deliberate set of design patterns. These are chosen for an embedded system with limited RAM (512MB on camera, 4GB on server) — simplicity and low overhead are priorities.

#### Patterns We Use

| Pattern | Where | Purpose |
|---------|-------|---------|
| **App Factory** | `server/__init__.py` | `create_app()` builds Flask with all deps |
| **Blueprint/Module** | `server/api/*.py` | One blueprint per API domain |
| **Service Layer** | `server/services/*.py` | Business logic separated from HTTP routes |
| **Repository** | `server/store.py` | Data access abstracted behind `Store` class |
| **Data Transfer Objects** | `server/models.py`, `camera/config.py` | Dataclasses for structured data |
| **Platform Provider** | `camera/platform.py` | Hardware abstraction — all device paths in one place |
| **Strategy** | Streaming backends, capture backends | Swappable implementations behind a Protocol interface |
| **Fail-Silent Adapter** | `camera/led.py`, hardware access | Wraps hardware calls, fails gracefully on unsupported platforms |
| **Constructor Injection** | All classes | Dependencies passed in `__init__`, never imported globals |
| **Observer/Callback** | `StorageManager` → `StreamingService` | Notify dependent services of state changes without tight coupling |

#### Patterns We Do NOT Use (and Why)

| Pattern | Why Not |
|---------|---------|
| **DI Container** (inject, dependency-injector) | ~10 services total. Constructor injection is sufficient. |
| **Abstract Factory** | Not building families of related objects. Strategy covers our needs. |
| **Event Sourcing / CQRS** | JSON files, not a database. Adds complexity for zero benefit. |
| **Microservices** | 512MB device. One process per app is correct. |
| **Plugin System** (dynamic loading) | Firmware, not VS Code. Compile-time decisions are fine. |
| **ORM / Active Record** | No SQL database. JSON files with atomic writes. |

#### Single Responsibility Rule

- **One class per file, one concern per class.** If a file has two classes, split it.
- **Exception:** Small related dataclasses may share a file (e.g., `models.py`).
- **God files are banned.** If a file exceeds ~300 lines, it's doing too much — split it.
- **Each module should be describable in one sentence.** If you need "and" in the description, it's two modules.

#### Platform Abstraction Rule

- **All hardware paths come from `Platform` provider or environment variables.** Never hardcode `/dev/video0`, `/sys/class/leds/ACT`, `wlan0`, or `thermal_zone0` directly in business logic.
- **`platform.py`** auto-detects hardware, can be overridden by env vars.
- **Modules receive hardware paths via constructor injection.** Example: `LedController(led_path=platform.led_path)`.
- **Hardware access must fail silently** on unsupported platforms (tests, containers, different boards).

#### Strategy Pattern Rule

- **Swappable backends use `typing.Protocol`** to define the interface.
- **Current strategies:** `StreamBackend` (FFmpeg vs go2rtc), `CaptureBackend` (v4l2 vs libcamera), `PlayerBackend` (WebRTC vs HLS).
- **Selection happens at startup** in `main.py` or `create_app()`, not scattered in business logic.
- **New backends** are added by creating a new class that satisfies the Protocol — no existing code changes needed.

#### Dependency Injection Rule

- **Pass dependencies in `__init__`.** Never import a service and call it directly from another service.
- **Flask app context** (`current_app`) is acceptable for accessing app-wide singletons (Store, AuditLogger).
- **No service locators, no global registries.** If a service needs another service, it receives it as a constructor argument.

#### Observer/Callback Pattern Rule

- **Use simple callbacks for cross-service notifications.** When Service A needs to tell Service B about a state change, A holds a `_on_change` callback set via `set_*_callback()`.
- **Example:** `StorageManager.set_dir_change_callback(fn)` — when recordings dir changes (e.g., switching to USB), StorageManager calls `fn(new_dir)`, which triggers StreamingService to restart recorder pipelines.
- **Keep it simple.** One callback per concern. No event buses, no pub/sub, no observer registries. This system has ~10 services — a callback function is sufficient.
- **Wiring happens in `create_app()`.** The app factory connects callbacks between services.

#### Live Streaming Architecture Rule

- **Recordings use HLS** (FFmpeg → 3-min MP4 clips). This is the storage pipeline.
- **Live view uses WebRTC** (MediaMTX WHEP → browser). Sub-second latency.
- **Fallback cascade:** WebRTC → HLS. The player tries WebRTC first, falls back to HLS if ICE negotiation fails.
- **MediaMTX is the single stream hub.** Camera pushes RTSP to MediaMTX. All consumers (WebRTC, HLS, recording) read from MediaMTX. Never duplicate the camera stream.

### 3.7 AI Contributor Rules

This codebase is developed primarily by AI agents (Claude Code). These rules
exist because AI amplifies ambiguity — clarity and enforcement matter more
than minimalism.

#### Where Business Logic Lives

| Layer | What belongs here | What does NOT belong here |
|-------|-------------------|--------------------------|
| `api/*.py` (routes) | HTTP parsing, JSON response formatting | Validation, subprocess calls, store access |
| `services/*_service.py` | Validation, orchestration, audit logging | HTTP request/response, Flask session access |
| `store.py` | JSON read/write, atomic persistence | Business rules, validation |
| `models.py` | Data structures (dataclasses) | Methods with side effects |

**Rule:** If you find `subprocess.run()`, `store.get_*()`, or validation
logic inside a route handler — extract it to the service layer.

#### Where FFmpeg Orchestration Lives

- **`streaming.py`** — all ffmpeg pipeline management (HLS, recording, snapshots)
- **`recorder.py`** — clip metadata and filesystem queries (NOT actual recording)
- **Never** put ffmpeg commands in route handlers or other services
- **Never** call ffmpeg directly from `create_app()` or startup code —
  always go through `StreamingService`

#### What Not to Refactor Casually

These decisions are deliberate. Do not change them without an ADR:

- **JSON files for persistence** (not SQLite, not Postgres) — see ADR-0002
- **Flask** (not FastAPI, not Django) — see ADR-0006
- **HLS fallback, WebRTC primary** — see ADR-0005
- **Service layer pattern** — see ADR-0003
- **Camera lifecycle state machine** — see ADR-0004
- **No DI containers** — constructor injection is sufficient for ~10 services
- **No src/ layout** — flat package layout matches Yocto recipe expectations

#### Naming Conventions

- **Services:** `services/<name>_service.py` containing class `<Name>Service`
- **Routes:** `api/<resource>.py` containing blueprint `<resource>_bp`
- **Tests:** `tests/test_<module>.py` for routes, `tests/test_<name>_service.py` for services
- **Models:** All in `models.py` as dataclasses
- **Constants:** UPPER_SNAKE_CASE at module level, never in class bodies

#### Service Layer Checklist (for new features)

1. Create `services/<feature>_service.py` with `<Feature>Service` class
2. Constructor injection: `__init__(self, store, audit=None, ...)`
3. Methods return `(result, error, status_code)` tuples
4. Fail-silent audit: `_log_audit()` wraps audit calls in try/except
5. Wire in `__init__.py` → `_init_services()`
6. Create thin route in `api/<feature>.py`
7. Write `tests/test_<feature>_service.py` (unit, mocked deps)
8. Update `services/__init__.py` docstring

### 3.8 Testing Rules

**Full details: [`docs/testing-guide.md`](testing-guide.md)** — setup, writing tests, running tests, coverage reports, examples, checklists.

#### 5-Layer Testing Pyramid

Every PR must satisfy the appropriate layers:

| Layer | What | Where | When to run | Required? |
|-------|------|-------|-------------|-----------|
| **1. Static checks** | `ruff check` + `ruff format --check` | CI + pre-commit | Every push | Always |
| **2. Unit tests** | Service logic, models, helpers | `tests/test_*.py` | Every push | Always |
| **3. Integration tests** | Flask routes + services wired together | `tests/test_api_*.py` | Every push | Always |
| **4. Contract tests** | Exact JSON field names per endpoint | `tests/test_api_contracts.py` | Every push | Always |
| **5. Smoke tests** | Live hardware: HTTPS, auth, health, cameras | `scripts/smoke-test.sh` | After deploy | Before merge |

**Why contract tests (Layer 4)?** A renamed response field passes unit tests but breaks the frontend. Contract tests pin the exact field names returned by every API endpoint. If a field changes, the contract test fails immediately.

**Why smoke tests (Layer 5)?** Unit/integration tests use mocks. Smoke tests hit a real RPi with real HTTPS, real disk, real systemd services. Run after deploying to hardware:

```bash
./scripts/smoke-test.sh <server-ip> <password>
```

#### Key Rules (non-negotiable)

1. **Every code change must include unit tests.** No PR is merged without tests for the changed code.
2. **Minimum coverage: Server 80%, Camera 55%.** PRs that drop coverage below these thresholds are blocked. Target: server 90%+, camera 70%+.
3. **Security-critical code (auth, sessions, passwords, pairing, TLS, certs) must aim for 95%+ coverage.** This includes camera PBKDF2 password hashing and session management.
4. **Run tests before every commit.** If tests fail, do not commit.
5. **Test file naming:** `test_<module>.py` — mirrors the module it tests.
6. **Test location:** `app/server/tests/` and `app/camera/tests/`.
7. **Contract tests are mandatory for new API endpoints.** Add field assertions to `test_api_contracts.py`.
8. **Run smoke tests after every deploy to hardware.** Report results in the PR.

```bash
# Run before every commit
cd app/server && pytest     # Server: coverage + tests
cd app/camera && pytest     # Camera: coverage + tests
```

#### Test File Organization

| Pattern | Purpose | Example |
|---------|---------|---------|
| `test_<service>.py` | Unit tests for a service | `test_user_service.py` |
| `test_api_<domain>.py` | Integration tests for API routes | `test_api_cameras.py` |
| `test_api_contracts.py` | Contract tests (all endpoints) | Field name assertions |
| `test_svc_<module>.py` | Unit tests for infrastructure services | `test_svc_audit.py` |
| `test_<module>.py` | Unit tests for non-service modules | `test_auth.py`, `test_store.py` |

#### Integration / Hardware Testing

- **Test on actual hardware** before merging significant changes. QEMU doesn't have camera hardware.
- **For app changes:** Use the rsync workflow on a dev image. Don't rebuild the full Yocto image for every code change.
- **For recipe changes:** Full bitbake build + parse check required.
- **API testing:** Document curl commands for each endpoint in the PR description.
- **Security testing:** Run through the threat model checklist (docs/architecture.md Section 3.1) for any change that touches auth, networking, or data storage.

---

## 4. File and Directory Rules

### 4.1 Where Things Go

| What | Where | Never |
|------|-------|-------|
| Application code | `app/server/` or `app/camera/` | In recipe `files/` dirs |
| Yocto recipes | `meta-home-monitor/recipes-*/` | In upstream layers |
| System config (systemd, nginx, nftables) | `app/<app>/config/` | Hardcoded in recipes |
| Build configuration | `config/` | In meta-home-monitor |
| Distro policy | `meta-home-monitor/conf/distro/` | In local.conf |
| Package lists | `meta-home-monitor/recipes-core/packagegroups/` | In image .bb files |
| Documentation | `docs/` | Scattered in code comments |
| Build scripts | `scripts/` | Inline in README |
| Partition layouts | `meta-home-monitor/wic/` | Hardcoded in recipes |

### 4.2 Naming Conventions

| Item | Convention | Example |
|------|-----------|---------|
| Python files | `snake_case.py` | `camera_streamer.py` |
| Python classes | `PascalCase` | `RecorderService` |
| Python functions | `snake_case` | `get_camera_status()` |
| Python constants | `UPPER_SNAKE_CASE` | `MAX_CLIP_DURATION` |
| API endpoints | `/lowercase-kebab` | `/api/v1/cameras` |
| Yocto recipes | `<name>_<version>.bb` | `monitor-server_1.0.bb` |
| Yocto packagegroups | `packagegroup-<product>-<function>.bb` | `packagegroup-monitor-video.bb` |
| Config files | `kebab-case.conf` | `nginx-monitor.conf` |
| systemd units | `kebab-case.service` | `camera-streamer.service` |
| Git branches | `<type>/<kebab-case>` | `feature/camera-pairing` |
| Git tags | `vMAJOR.MINOR.PATCH` | `v1.2.0` |

### 4.3 Files That Must Stay In Sync

When changing one, check if the others need updating:

| Change | Also update |
|--------|-------------|
| New API endpoint | `docs/requirements.md` (API section), `app/server/monitor/__init__.py` (blueprint registration) |
| New package in image | Appropriate packagegroup `.bb`, verify parse with `bitbake -p` |
| New Python dependency | `app/<app>/requirements.txt`, check `bitbake -s` for Yocto availability |
| New systemd service | Recipe `.bb` (SYSTEMD_SERVICE), `app/<app>/config/` |
| Distro feature change | `meta-home-monitor/conf/distro/home-monitor.conf`, may affect all images |
| Partition layout change | `meta-home-monitor/wic/*.wks`, `docs/architecture.md` (Section 5) |
| New image package | Packagegroup, NOT image .bb directly |
| Version bump | Distro config, `app/*/setup.py`, git tag, release notes |
| Security-relevant change | `docs/architecture.md` (threat model), `docs/requirements.md` (security section) |

---

## 5. Data & Storage Rules

### 5.1 Data Partition (`/data`)

- **Everything on `/data` must survive OTA updates.** The rootfs is replaced; `/data` persists.
- **File structure is part of the contract.** Don't reorganize without updating all consumers.
  ```
  /data/
    recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.mp4     ← clips
    recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.thumb.jpg ← thumbnails
    live/<cam-id>/stream.m3u8                          ← HLS playlist
    live/<cam-id>/segment_NNN.ts                       ← HLS segments
    config/cameras.json                                ← camera registry
    config/users.json                                  ← user accounts
    config/settings.json                               ← system settings
    certs/ca.crt, ca.key                               ← local CA
    certs/server.crt, server.key                       ← server TLS
    certs/cameras/<cam-id>.crt                         ← camera certs
    logs/audit.log                                     ← security events
  ```

### 5.2 JSON Data Files

- **Always write atomically:** Write to temp file, then `os.rename()` to final path. Never write directly (power loss = corruption).
- **Always read with a default fallback.** If the file is missing or corrupt, use defaults — don't crash.
- **Keep JSON schemas simple.** No nested arrays-of-objects deeper than 2 levels.
- **Camera IDs are immutable** (derived from hardware serial). Never allow rename of the ID itself.
- **User IDs are `usr-NNN`**, auto-incrementing. Never reuse deleted IDs.

### 5.3 Recording Files

- **File naming is the index.** We don't maintain a database of clips — the filesystem IS the database. Camera ID, date, and time are in the path.
- **3-minute segments, aligned to clock.** `14-00-00.mp4`, `14-03-00.mp4`, etc.
- **Thumbnails always alongside clips.** Same name with `.thumb.jpg` suffix.
- **ffmpeg must use `-movflags +faststart`** so clips are playable immediately without downloading the whole file.
- **Never delete a clip while it's being written.** Check the ffmpeg process before cleanup.

---

## 6. Deployment & Operations Rules

### 6.1 Image Flashing

- **Dev images for development only.** Never deploy dev images outside your test bench.
- **Prod images for real devices.** Always use prod for anything in a real home.
- **Label SD cards** with the image type and version (e.g., "server-prod v1.0.0").

### 6.2 OTA Updates

- **All OTA images must be signed** with Ed25519 (`scripts/sign-image.sh`).
- **Never push an unsigned image.** Devices will reject it.
- **Test OTA on a dev device first** before pushing to prod.
- **The signing private key is never committed to git.** It lives in `~/.monitor-keys/` on the build machine only.
- **Rollback:** If a new rootfs fails to boot 3 times, the device automatically rolls back. Never disable this.

### 6.3 Secrets Management

| Secret | Where it lives | Who can access |
|--------|---------------|---------------|
| OTA signing private key | `~/.monitor-keys/ota-signing.key` on build machine | Build operator only |
| OTA signing public key | Embedded in rootfs (`/etc/monitor/ota-signing.pub`) | All devices (read-only) |
| CA private key | `/data/certs/ca.key` on server | Server process only (0600) |
| Server TLS key | `/data/certs/server.key` on server | nginx process (0640) |
| Camera client keys | `/data/certs/client.key` on each camera | Camera process only (0600) |
| User passwords | `/data/config/users.json` (bcrypt hashed) | Server process only (0640) |
| WiFi passwords | NetworkManager system-connections | Root only |
| GitHub SSH keys | `~/.ssh/` on build VM | Build operator only |

**Rules:**
- Never commit any of the above to git.
- Never log any of the above.
- Never transmit any private key over unencrypted channels.
- `.gitignore` must block `*.key`, `*.pem`, `users.json`.

---

## 7. Documentation Rules

### 7.1 What Must Be Documented

| Change type | Documentation required |
|---|---|
| New feature | Update `docs/requirements.md` (add user need or SW requirement) |
| New API endpoint | Update `docs/requirements.md` (SR-SRV-12 API section) |
| Architecture change | Update `docs/architecture.md` |
| Security change | Update threat model in `docs/architecture.md` Section 3 |
| New Yocto recipe/package | Update `README.md` (if it affects build) |
| Build process change | Update `README.md` and `scripts/build.sh` |
| New dev workflow | Update this file (`docs/development-guide.md`) |

### 7.2 Documentation Style

- **Markdown only.** No Word docs, no Google Docs, no wiki.
- **Keep docs in the repo** (in `docs/`). The docs ship with the code.
- **Update docs in the same PR as the code change.** Don't create "update docs" PRs after the fact.
- **Diagrams in ASCII art** (renders everywhere, no external tools).
- **Version numbers in docs:** Update when they change. Stale version numbers are misleading.

---

## 8. Open Source Rules

Since this is a public GitHub repository:

### 8.1 License

- **AGPL-3.0 License** for all custom code in this repository. Commercial licensing available on request.
- **Every recipe must declare `LICENSE` and `LIC_FILES_CHKSUM`.**
- **Yocto generates SPDX license manifests** (enabled in distro config). These ship with the image and list every package and its license.
- **Before adding a new package:** Check its license is compatible.

### 8.2 What Never Goes in the Repo

- Compiled binaries or images (use GitHub Releases)
- Private keys, certificates, or credentials
- Yocto build output (`build/`, `build-zero2w/`, `sstate-cache/`, `downloads/`)
- Upstream layers (`poky/`, `meta-raspberrypi/`, `meta-openembedded/`)
- IDE config except `.editorconfig` (no `.vscode/settings.json` with personal paths)
- Large media files (use Git LFS if absolutely necessary)

### 8.3 Issue Tracking

- **Use GitHub Issues** for bugs, features, and tasks.
- **Issue labels:** `bug`, `feature`, `recipe`, `security`, `docs`, `question`
- **Security vulnerabilities:** Open as private/confidential if GitHub supports it. Don't disclose details in public issues until fixed.

---

## 9. Checklist for Common Tasks

### Adding a New Package to an Image

```
[ ] Is the package available in Yocto? Check: bitbake -s | grep <name>
[ ] Which image needs it? Server, camera, or both?
[ ] Add to the appropriate packagegroup .bb file (NOT to the image .bb)
[ ] Parse check: bitbake -p <image>
[ ] Full build test: bitbake <image>
[ ] Update docs if it's a significant addition
[ ] Commit on a feature branch, open PR
```

### Adding a New API Endpoint

```
[ ] Create or update the blueprint in app/server/monitor/api/
[ ] Add auth decorator (@login_required, @admin_required)
[ ] Add CSRF check for state-changing methods
[ ] Add audit log entry for state-changing operations
[ ] Return proper JSON response with correct HTTP status
[ ] Validate all user input
[ ] Add contract test in tests/test_api_contracts.py (exact field names)
[ ] Add integration test in tests/test_api_<domain>.py
[ ] Update docs/requirements.md (SR-SRV-12 API section)
[ ] Test with curl
[ ] Commit on a feature branch, open PR
```

### Adding a New Yocto Recipe

```
[ ] Create recipe in meta-home-monitor/recipes-<category>/<name>/
[ ] Include: SUMMARY, DESCRIPTION, LICENSE, LIC_FILES_CHKSUM
[ ] Declare RDEPENDS (runtime) and DEPENDS (build-time) explicitly
[ ] If it has a systemd service: inherit systemd, set SYSTEMD_SERVICE
[ ] Add to appropriate packagegroup (not image .bb directly)
[ ] Parse check: bitbake -p <recipe>
[ ] Build check: bitbake <recipe>
[ ] Image build: bitbake <image>
[ ] Commit on a recipe/ branch, open PR
```

### Making a Release

```
[ ] All PRs for the release are merged to main
[ ] Create release/vX.Y.Z branch
[ ] Update DISTRO_VERSION in home-monitor.conf
[ ] Update version in app/server/setup.py and app/camera/setup.py
[ ] Build all 4 images: server-dev, server-prod, camera-dev, camera-prod
[ ] Parse check passes with 0 errors
[ ] Boot test on hardware (at least server-dev + camera-dev)
[ ] Merge to main
[ ] Tag: git tag vX.Y.Z
[ ] Create GitHub Release with prod .wic.bz2 images attached
[ ] Sign prod images: ./scripts/sign-image.sh <image.swu>
```

### Security Fix

```
[ ] Assess severity (critical/high/medium/low)
[ ] Create security/ branch (don't disclose details in branch name)
[ ] Fix the vulnerability
[ ] Update threat model in docs/architecture.md if needed
[ ] Test the fix
[ ] Open PR with security label
[ ] After merge: update all deployed devices via OTA
[ ] If critical: fast-track a patch release (vX.Y.Z+1)
```
