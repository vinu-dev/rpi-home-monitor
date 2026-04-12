# RPi Home Monitor — AI Operating Manual

## 1. What This Is

Self-hosted home security camera system on Raspberry Pi. Two separate apps:

- **`app/server/`** — Flask web app (monitor-server). Runs on RPi 4 Model B. Receives camera streams, records 3-min MP4 clips, serves web dashboard, manages cameras.
- **`app/camera/`** — Python streaming service (camera-streamer). Runs on RPi Zero 2W. Captures 1080p video, pushes RTSPS (mTLS) to server.
- **`meta-home-monitor/`** — Custom Yocto Linux distro (Home Monitor OS). Custom distro config, not poky.

```
Camera (V4L2) → FFmpeg (H.264 RTSPS push, mTLS)
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

## 2. Core Principles

This project is not a prototype. Every change must be production-ready, scalable, maintainable, and suitable for open-source contribution. The goal is a reference-quality system that others can learn from and contribute to.

### Design Philosophy

- **Build it right, not just working** — do not take shortcuts to make something functional. Every implementation must be the best possible design for this product.
- **Design-level fixes only** — never patch symptoms. If something breaks, find and fix the root cause. No fallback-based designs as a primary solution.
- **No hacks, no workarounds** — no internal-only shortcuts, no undocumented logic, no manual-only workflows. Everything must be reproducible and automated.

### Code Quality

- **Readable and review-friendly** — code will be read by contributors and the open-source community. Write for humans first. Clear module boundaries, obvious data flow, self-documenting names.
- **Easy to debug, update, and extend** — structure code so that adding a feature or fixing a bug requires touching minimal files with obvious impact.
- **Well-structured modules** — each module has a clear purpose. No god files, no tangled dependencies, no circular imports.

### Testing & Reliability

- **Automated tests for everything** — every service, API endpoint, and behavior must have tests. No untested code paths in production.
- **Real-world simulation** — tests must cover realistic scenarios, not just happy paths with injected state. Test coverage must include:
  - **Fresh setup (zero state)** — first boot, no config, no users, no cameras
  - **Network reconnect** — WiFi drops, camera disconnects, server restarts
  - **Failure recovery** — corrupt config, full disk, service crash
- **Smoke tests on hardware** — deploy and verify on real boards. Smoke tests are mandatory after every deploy, not optional.

### UI/UX Standards

- **Clean, intuitive, responsive** — mobile-first dark theme. Follow modern UI best practices (design tokens, component architecture, accessible markup).
- **Architecture for growth** — UI code must support scaling (more cameras, more settings, more pages) without restructuring.
- **Review-friendly and reusable** — UI code should be exemplary. Well-structured CSS with design tokens, co-located Alpine.js components, semantic HTML.

### Open Source Readiness

- **Easy to understand** — a new contributor should be able to read any file and understand what it does and why.
- **Easy to contribute to** — clear patterns, consistent conventions, good test coverage make contributions safe.
- **No internal assumptions** — every decision must be documented or self-evident from the code. No tribal knowledge.

## 3. Architecture & Patterns

### Patterns We Follow

- **Service Layer** — business logic in `services/*_service.py` classes, routes are thin HTTP adapters that unpack `(result, error, status_code)` tuples.
- **Constructor Injection** — pass deps in `__init__()`. No DI frameworks, no global registries.
- **Single Responsibility** — one class per file, one concern per class. No god files (>300 lines).
- **App Factory** — Flask `create_app()` decomposed into `_init_infrastructure`, `_init_services`, `_startup`, `_register_blueprints`.
- **State Machine** — camera lifecycle as explicit states (`INIT → SETUP → PAIRING → CONNECTING → VALIDATING → RUNNING → SHUTDOWN`).
- **Platform Provider** — `camera/platform.py` provides all hardware paths. Never hardcode device paths.
- **Repository** — `Store` class for JSON persistence with atomic writes.
- **Fail-Silent Adapter** — all hardware access wrapped in try/except.

### Patterns We NEVER Use

No DI containers, no event sourcing, no CQRS, no microservices, no plugin systems, no ORM, no database.

Full pattern docs: [development-guide.md Section 3.6](docs/development-guide.md). Decision rationale: [docs/adr/](docs/adr/).

### Yocto Build Rules

- **Extend, don't reshape** — do not disturb the existing distro/layer structure unless there is a strong reason. Add cleanly.
- **No `local.conf` hacks** — project logic and permanent settings go in distro config, image recipes, packagegroups, or machine config. `local.conf` is for per-developer overrides only.
- **Layer branch matching** — all added layers must match our Yocto release branch (`scarthgap`). Never mix branches.
- **Prefer maintained recipes** — use an existing recipe/layer first. Only create a custom recipe when no maintained option exists or project-specific behavior is required.
- **Packagegroups for policy** — use packagegroups to group related packages. Don't grow `IMAGE_INSTALL` lists everywhere.
- **Dev/prod separation** — debug tools, dev configs, and diagnostic packages go in `-dev` images only (via `home-*-image-dev.bb`), never in prod.
- **Runtime prerequisites** — enable all of them: service units, kernel config, state paths, startup ordering, persistent storage dirs.
- **Mutable state on `/data`** — keep runtime state out of rootfs. Logs, certs, config, recordings, VPN state all go on `/data` (persistent partition).
- **Systemd ordering** — be explicit about `After=`, `Wants=`, `Requires=` for services that depend on network, time sync, mounts, or provisioning.
- **No rootfs hacks** — avoid manual rootfs edits or post-install fixes outside the Yocto build. Everything goes through recipes.
- **Document non-obvious choices** — add a comment explaining why each layer, recipe, or config choice was added.
- **Build on VM** — all Yocto builds run on the GCP build VM in tmux/screen. Only copy final `.wic.bz2` images to the local PC for flashing.

## 4. Known Gaps

Only what's NOT done. When you implement a gap, delete it from this list in the same PR.

- **Motion detection** — recording mode exists but motion trigger not implemented (Phase 2)
- **Multi-camera** — framework exists, untested with multiple real cameras (Phase 2)
- **Cloud relay, mobile app, AI/ML** — Phase 2-3, not started

## 5. Task Routing

| Change Type | Directory | Tests | Deploy Target |
|-------------|-----------|-------|---------------|
| Server app | `app/server/` | `pytest app/server/tests/` | RPi 4B: scp to `/opt/monitor/` |
| Camera app | `app/camera/` | `pytest app/camera/tests/` | Zero 2W: scp to `/opt/camera/` |
| API endpoint | `app/server/monitor/api/` | + contract tests in `test_api_contracts.py` | RPi 4B + smoke test |
| New service | `app/server/monitor/services/` | + new `test_svc_*.py` | RPi 4B |
| Yocto recipe | `meta-home-monitor/` | `bitbake -p` (parse check) | Full image rebuild via `./scripts/build.sh` |
| Templates/UI | `app/server/monitor/templates/` | Browser check + smoke test | RPi 4B |
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

## 6. Execution Process

Mandatory for every change. Same sequence, no improvisation.

### Step 1: ORIENT

```bash
git log --oneline -10          # What changed recently
```

Read the files you will change. Verify current state. Check Section 4 (Known Gaps) — is this already done or still a stub? Don't assume.

### Step 2: BRANCH

```bash
git checkout -b <prefix>/<description>
```

Prefixes: `feature/`, `fix/`, `docs/`, `recipe/`, `release/`. Never commit directly to main.

### Step 3: IMPLEMENT

One concern per PR. Follow Section 2 principles and Section 3 patterns. No drive-by refactors.

### Step 4: VALIDATE

| Changed | Must Run | Must Update |
|---------|----------|-------------|
| Server Python | `pytest app/server/tests/ -v` + lint | — |
| Camera Python | `pytest app/camera/tests/ -v` + lint | — |
| API endpoint | + contract tests | `development-guide.md` Section 3.4 |
| New service | + dedicated `test_svc_*.py` | `services/__init__.py` docstring |
| Security-related | full suite + smoke test | `architecture.md` |
| Yocto recipe | `bitbake -p` | — |
| Release / version bump | `./scripts/generate-sbom.sh` | `sbom/*.cdx.json` |
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

Deploy using Section 5 pattern. Run smoke test. Verify services are active.

## 7. Doc Update Rules

| Trigger | Update |
|---------|--------|
| Implement a Known Gap | Delete from CLAUDE.md Section 4, same PR |
| New API endpoint or convention | `docs/development-guide.md` |
| Security model or data model change | `docs/architecture.md` |
| Choice between alternatives with trade-offs | New `docs/adr/NNNN-<title>.md` |
| User-facing feature change | `README.md` |
| Release / Yocto rebuild | `./scripts/generate-sbom.sh` — regenerate `sbom/*.cdx.json` |
| Dependency change (requirements.txt) | Regenerate SBOM, commit updated `sbom/*.cdx.json` |
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

## 8. Reference

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
