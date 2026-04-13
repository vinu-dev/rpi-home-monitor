# Repo Map

## Top-Level Routing

| Area | Purpose | Key docs | Required validation |
|------|---------|----------|---------------------|
| `app/server/` | Flask server, API, dashboard, auth, OTA | `docs/architecture.md`, `docs/testing-guide.md` | `pytest app/server/tests/ -v`, lint |
| `app/camera/` | camera runtime, pairing, WiFi setup, HTTPS status UI | `docs/architecture.md`, `docs/testing-guide.md` | `pytest app/camera/tests/ -v`, lint |
| `meta-home-monitor/` | Yocto distro, recipes, image policy | `docs/development-guide.md`, `docs/build-setup.md` | `bitbake -p`, VM build |
| `config/` | committed Yocto build configs | `docs/build-setup.md` | parse/build for affected image |
| `scripts/` | build, smoke, deploy, ops helpers | `docs/development-guide.md` | syntax + live verification if operational |
| `docs/` | system of record | all docs | doc review + validator |

## Sub-Routing

### Server

- `app/server/monitor/api/`: HTTP adapters only
- `app/server/monitor/services/`: business logic
- `app/server/monitor/templates/`: dashboard UI
- `app/server/tests/`: server test suites

### Camera

- `app/camera/camera_streamer/`: runtime modules
- `app/camera/camera_streamer/templates/`: setup/login/status UI
- `app/camera/tests/`: camera test suites

### Yocto

- `meta-home-monitor/conf/`: distro and machine policy
- `meta-home-monitor/recipes-*`: packaging and image behavior

## Change-Type Routing

| Change type | Likely files | Must do |
|-------------|--------------|---------|
| API behavior | `app/server/monitor/api/`, `services/` | unit + integration + contract tests |
| Auth or security | server/camera auth modules, cert flow | full relevant suite + smoke |
| Frontend or status page | templates, static assets | browser-level check + smoke |
| Camera lifecycle | `lifecycle.py`, `wifi.py`, `pairing.py`, `status_server.py` | camera suite + hardware verification |
| Yocto policy | `meta-home-monitor/`, `config/` | parse + VM build |
| Docs or workflows | `docs/`, adapters, templates | validator + doc review |
