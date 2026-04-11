# ADR-0007: Dev Build Default Credentials

## Status
Accepted

## Context
Development and testing workflows require authenticating with both the server
and camera after every fresh flash. The server already auto-creates an
`admin`/`admin` user, but the camera has no default password — the WiFi
setup wizard must be completed first. This blocks automated smoke testing
and slows manual development iteration.

We need a pattern that:
1. Lets dev builds boot straight to a testable state (no setup wizard).
2. Keeps prod builds secure (no default credentials, setup wizard required).
3. Never puts dev-only logic in application source code.

## Decision
Provision default credentials and skip the setup wizard **in dev images
only**, using a Yocto recipe (not application code).

### Dev defaults (dev images only)
- **Server**: Already handled — `_ensure_default_admin()` creates
  `admin`/`admin` on first boot. Dev recipe pre-stamps
  `/data/.setup-done` so the setup wizard is bypassed.
- **Camera**: Dev recipe pre-creates `/data/config/camera.conf` with a
  known admin password hash (`admin`/`admin`) and stamps
  `/data/.setup-done`.
- **Smoke test**: Uses `admin` as default password for both server and
  camera.

### Prod defaults (prod images)
- **Server**: `_ensure_default_admin()` still creates `admin`/`admin`, but
  the setup wizard runs and forces the user to set a real password.
- **Camera**: No password until setup wizard completes.

### Implementation
The existing `monitor-dev-config` recipe (already dev-image-only) is
extended with a first-boot systemd oneshot service that:
1. Creates `/data/.setup-done`
2. Writes camera config with pre-hashed `admin`/`admin` password

This keeps application code clean — no `if DEV_MODE` branches.

## Alternatives Considered

### 1. Environment variable `DEV_MODE=true`
Rejected. Adds conditional logic to app code that could accidentally
ship in prod. Violates the principle of keeping dev-only config in
Yocto recipes.

### 2. Disable auth entirely in dev builds
Rejected. Auth bugs would be invisible during development. Smoke tests
need to exercise auth paths.

### 3. Fixture/seed script run manually
Rejected. Adds a manual step that everyone forgets. The whole point is
zero-friction boot-to-testable.

## Consequences
- Dev images boot directly to a testable state with known credentials.
- Smoke tests run without manual setup on fresh dev images.
- Prod images are unaffected — setup wizard still required.
- The `admin`/`admin` password is well-known and documented; this is
  acceptable for dev builds that run on local networks only.
