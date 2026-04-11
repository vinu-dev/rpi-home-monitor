# RPi Home Monitor — AI Operating Manual

## 1. What This Is

Self-hosted home security camera system on Raspberry Pi. Two separate apps:

- **`app/server/`** — Flask web app (monitor-server). Runs on RPi 4 Model B. Receives camera streams, records 3-min MP4 clips, serves web dashboard, manages cameras.
- **`app/camera/`** — Python streaming service (camera-streamer). Runs on RPi Zero 2W. Captures 1080p video, pushes RTSP to server.
- **`meta-home-monitor/`** — Custom Yocto Linux distro (Home Monitor OS). Custom distro config, not poky.

```
Camera (V4L2) → FFmpeg (H.264 RTSP push)
    → MediaMTX (:8554) on server
        ├→ WebRTC (WHEP :8889) → browser <video> (sub-1s, live view)
        ├→ FFmpeg Record → /data/recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.mp4
        └→ FFmpeg Snap   → /data/live/<cam-id>/snapshot.jpg (every 30s)

Browser → NGINX (:443 HTTPS)
    ├→ /webrtc/<cam-id>/ → proxy to MediaMTX :8889 (WHEP)
    ├→ /clips/<cam-id>/* → served from /data/recordings/
    └→ /api/*            → Flask (:5000)
```

See [README.md](README.md) for user-facing overview and setup instructions.

## 2. Architecture Constraints

### Patterns We Follow

- **Service Layer** — business logic in `services/*_service.py` classes, routes are thin HTTP adapters that unpack `(result, error, status_code)` tuples.
- **Constructor Injection** — pass deps in `__init__()`. No DI frameworks, no global registries.
- **Single Responsibility** — one class per file, one concern per class. No god files (>300 lines).
- **App Factory** — Flask `create_app()` decomposed into `_init_infrastructure`, `_init_services`, `_startup`, `_register_blueprints`.
- **State Machine** — camera lifecycle as explicit states (`INIT → SETUP → CONNECTING → VALIDATING → RUNNING → SHUTDOWN`).
- **Platform Provider** — `camera/platform.py` provides all hardware paths. Never hardcode device paths.
- **Repository** — `Store` class for JSON persistence with atomic writes.
- **Fail-Silent Adapter** — all hardware access wrapped in try/except.

### Patterns We NEVER Use

No DI containers, no event sourcing, no CQRS, no microservices, no plugin systems, no ORM, no database.

Full pattern docs: [development-guide.md Section 3.6](docs/development-guide.md). Decision rationale: [docs/adr/](docs/adr/).

## 3. Known Gaps

Only what's NOT done. When you implement a gap, delete it from this list in the same PR.

- **pairing.py** — mTLS cert exchange between camera and server (stub, Phase 2)
- **ota_agent.py** — camera OTA update listener (stub, Phase 2)
- **OTA upload/push** — server endpoints are stubs; status endpoint works
- **RTSPS** — camera-to-server streams use plaintext RTSP today (Phase 2: mTLS)
- **Motion detection** — recording mode exists but motion trigger not implemented (Phase 2)
- **Multi-camera** — framework exists, untested with multiple real cameras (Phase 2)
- **Cloud relay, mobile app, AI/ML** — Phase 2-3, not started

## 4. Task Routing

| Change Type | Directory | Tests | Deploy Target |
|-------------|-----------|-------|---------------|
| Server app | `app/server/` | `pytest app/server/tests/` | RPi 4B: scp to `/opt/monitor/` |
| Camera app | `app/camera/` | `pytest app/camera/tests/` | Zero 2W: scp to `/opt/camera/` |
| API endpoint | `app/server/monitor/api/` | + contract tests in `test_api_contracts.py` | RPi 4B + smoke test |
| New service | `app/server/monitor/services/` | + new `test_svc_*.py` | RPi 4B |
| Yocto recipe | `meta-home-monitor/` | `bitbake -p` (parse check) | Full image rebuild via `./scripts/build.sh` |
| Templates/UI | `app/server/monitor/templates/` | Manual browser check | RPi 4B |
| Docs only | `docs/` | None | None |

### Deploy Pattern (from Windows, not from build VM)

```bash
# Server
ssh root@<server-ip> "mkdir -p /opt/monitor_new"
scp -r app/server/monitor/* root@<server-ip>:/opt/monitor_new/
ssh root@<server-ip> "mv /opt/monitor/monitor /opt/monitor/monitor_old && mv /opt/monitor_new /opt/monitor/monitor && rm -rf /opt/monitor/monitor_old && systemctl restart monitor"

# Camera
ssh root@<camera-ip> "mkdir -p /opt/camera_new"
scp -r app/camera/camera_streamer/* root@<camera-ip>:/opt/camera_new/
ssh root@<camera-ip> "mv /opt/camera/camera_streamer /opt/camera/camera_streamer_old && mv /opt/camera_new /opt/camera/camera_streamer && rm -rf /opt/camera/camera_streamer_old && systemctl restart camera-streamer"

# Smoke test
bash scripts/smoke-test.sh <server-ip> <password> [camera-ip]
```

## 5. Execution Process

Mandatory for every change. Same sequence, no improvisation.

### Step 1: ORIENT

```bash
git log --oneline -10          # What changed recently
```

Read the files you will change. Verify current state. Check Section 3 (Known Gaps) — is this already done or still a stub? Don't assume.

### Step 2: BRANCH

```bash
git checkout -b <prefix>/<description>
```

Prefixes: `feature/`, `fix/`, `docs/`, `recipe/`, `release/`. Never commit directly to main.

### Step 3: IMPLEMENT

One concern per PR. Follow Section 2 patterns. No drive-by refactors.

### Step 4: VALIDATE

| Changed | Must Run | Must Update |
|---------|----------|-------------|
| Server Python | `pytest app/server/tests/ -v` + lint | — |
| Camera Python | `pytest app/camera/tests/ -v` + lint | — |
| API endpoint | + contract tests | `development-guide.md` Section 3.4 |
| New service | + dedicated `test_svc_*.py` | `services/__init__.py` docstring |
| Security-related | full suite + smoke test | `architecture.md` |
| Yocto recipe | `bitbake -p` | — |
| Implement a gap | relevant tests | CLAUDE.md Section 3 (delete the gap) |

### Step 5: LINT

```bash
ruff check app/ && ruff format --check app/
```

Fix all issues before committing.

### Step 6: COMMIT

Conventional message. Always include trailer:

```bash
git commit -m "$(cat <<'EOF'
<type>: <description>

<body if needed>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

### Step 7: PUSH + CI

```bash
git push -u origin <branch>
gh pr checks <number> --watch    # Wait for all checks green
```

### Step 8: PR

```bash
gh pr create --title "<type>: <short description>" --body "..."
```

Title under 70 chars. Body has `## Summary` (bullets) and `## Test plan` (checklist).

### Step 9: MERGE

Only after CI green: `gh pr merge <number> --merge --delete-branch`

### Step 10: DEPLOY (if hardware available)

Deploy using Section 4 pattern. Run smoke test. Verify services are active.

## 6. Doc Update Rules

| Trigger | Update |
|---------|--------|
| Implement a Known Gap | Delete from CLAUDE.md Section 3, same PR |
| New API endpoint or convention | `docs/development-guide.md` |
| Security model or data model change | `docs/architecture.md` |
| Choice between alternatives with trade-offs | New `docs/adr/NNNN-<title>.md` |
| User-facing feature change | `README.md` |
| Never hardcode test counts in docs | Say "run pytest" instead |
| Never duplicate info that lives in `docs/` | Link to it |

### File Location Rules

**Root (3 files only):**

| File | Why it must be at root |
|------|------------------------|
| `CLAUDE.md` | Claude Code auto-reads from project root |
| `README.md` | GitHub renders from repo root |
| `CHANGELOG.md` | Conventional root placement |

**`docs/` (all other documentation):**

- `docs/*.md` — guides and specifications
- `docs/adr/NNNN-*.md` — Architecture Decision Records

Never create `.md` files in `app/`, `scripts/`, or `meta-home-monitor/`. New guide or spec goes in `docs/`. New architectural decision goes in `docs/adr/`.

## 7. Reference

| Document | What's Inside |
|----------|---------------|
| [README.md](README.md) | User-facing overview, setup, build targets |
| [CHANGELOG.md](CHANGELOG.md) | Release notes, setup walkthrough |
| [requirements.md](docs/requirements.md) | User stories, API spec, security requirements |
| [architecture.md](docs/architecture.md) | System design, security model, threat analysis |
| [development-guide.md](docs/development-guide.md) | Git workflow, Yocto rules, Python conventions, patterns |
| [testing-guide.md](docs/testing-guide.md) | Test framework, writing tests, coverage targets |
| [build-setup.md](docs/build-setup.md) | Build VM setup, prerequisites |
| [hardware-setup.md](docs/hardware-setup.md) | Shopping list, assembly, flashing, first boot |
| [docs/adr/](docs/adr/) | 6 Architecture Decision Records |
