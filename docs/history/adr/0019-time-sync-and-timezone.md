# ADR-0019: Time sync, timezone, and LAN NTP topology

**Status:** Accepted
**Date:** 2026-04-18

## Context

Timestamps are load-bearing in a home-monitor system: clip filenames
(`YYYY-MM-DD/HH-MM-SS.mp4`), audit log entries, recording-schedule
evaluation, certificate expiry checks, OTA version ordering, and
dashboard "time ago" displays all depend on a correct, consistent clock.

Observations that drove this ADR:

- The dashboard was rendering every clip as "1 hour ago" regardless of
  age, because the server returned ISO timestamps without a `Z` suffix
  and browsers interpreted the naked string as local time. (Fixed in
  commit `5f7d390`.)
- The OS image shipped with `systemd-timesyncd` already configured, but
  there was no user-facing control over timezone (the `timezone` field
  was stored but never applied via `timedatectl`), NTP mode
  (always-on), or manual clock setting (impossible).
- Cameras and the server both synced independently to upstream NTP
  pools. On a LAN with intermittent or firewalled internet, cameras and
  server could drift apart from each other even though both were
  "in sync" with *some* source.
- After an A/B OTA rootfs swap, `/etc/timezone` and
  `/etc/systemd/timesyncd.conf` are restored to factory defaults on
  the new rootfs. The user's choice would silently revert — same
  regression shape as the WiFi profile loss fixed in ADR-0008.

## Decision

### 1. Authoritative clock topology: server, cameras follow

The home-monitor **server** is the authoritative LAN clock. It runs
`systemd-timesyncd` with a short preference list of upstream NTP
sources (Google, Cloudflare, `pool.ntp.org` fallback) and exposes its
own time to cameras via mDNS hostname.

Each **camera** ships a `systemd-timesyncd` drop-in at
`/etc/systemd/timesyncd.conf.d/10-home-camera.conf` with:

```ini
[Time]
NTP=rpi-divinu.local
FallbackNTP=time.google.com time.cloudflare.com pool.ntp.org
```

The drop-in is installed by the `camera-streamer` recipe so it lives
inside the camera rootfs (baked into every A/B slot), requires no
runtime configuration, and survives OTA.

Rationale vs. running `chrony` as a proper NTP server on the home
monitor: `timesyncd` is already present and enabled on both devices,
adds zero new packages to the image, and is adequate for one-hop LAN
sync (sub-second accuracy, which is far better than the clip-filename
resolution of one second). We can revisit `chrony` if we later need
stratum-1 accuracy or intermittent-internet resilience.

### 2. User-facing time settings

`Settings` gains one new field:

```python
ntp_mode: str = "auto"   # auto | manual
```

`timezone` (already present) is **applied** on every change and on
every server startup via `timedatectl set-timezone`.

- `ntp_mode=auto` → `timedatectl set-ntp true` → timesyncd runs.
- `ntp_mode=manual` → `timedatectl set-ntp false` → clock stays where
  the user sets it.

New API surface:

- `GET  /api/v1/settings/time` — current timezone + NTP state + system
  time (from `timedatectl show`).
- `POST /api/v1/settings/time` — `{"time": "YYYY-MM-DDTHH:MM:SS"}`;
  requires `ntp_mode=manual` (returns 409 otherwise). Invokes
  `timedatectl set-time` and writes an audit event `TIME_SET_MANUAL`.

The existing `PUT /api/v1/settings` handles `timezone` and `ntp_mode`
changes; both trigger `_apply_runtime_changes` which calls
`timedatectl` directly — no restart required.

### 3. OTA resilience: re-apply on every startup

`SettingsService.reapply_persisted_time_settings()` is called from
`create_app()` immediately after the service is wired. It reads the
persisted `Settings` (on `/data/config/settings.json`) and re-applies
`timezone` + `ntp_mode` via `timedatectl`.

This closes the OTA regression: after an A/B rootfs swap, the new
rootfs boots with factory `/etc/timezone` and timesyncd defaults, but
the server process re-applies the user's choices within seconds of
coming up. No extra systemd unit, no carry-over gymnastics in
`post-update.sh` — the source of truth lives on `/data`, which is
shared across both slots.

### 4. UI

A new "Date & Time" card on the Settings page shows system time,
timezone, NTP state (active / synchronized), and — when `ntp_mode`
is `manual` — a `datetime-local` input plus **Apply** button.
The existing System Settings form grows a radio pair
(**Automatic (NTP)** / **Manual**) next to the timezone field so
the two related controls live together.

## Consequences

### Positive

- User can set timezone + NTP mode from the web UI; changes apply
  immediately without reboot.
- Timezone + NTP choice survives OTA rootfs swaps (no new carry-over
  logic).
- Cameras and server share a clock within sub-second accuracy on
  the LAN, even when the internet link is flaky.
- Clip filenames stay UTC (already the case; orthogonal to display
  timezone) so the dashboard's recent-clips query is timezone-safe.

### Negative / accepted trade-offs

- `timedatectl` requires root. The monitor service already runs with
  enough capability to invoke it (same path used for `nmcli`,
  `swupdate`). Test suite mocks `subprocess.run` — no production
  `timedatectl` call in CI.
- Manual time setting is not validated against plausibility (could
  set clock to year 3000). Acceptable: this is an admin-only
  operation, audit-logged, and the next `ntp_mode=auto` flip will
  correct it.
- Cameras depend on mDNS resolution of `rpi-divinu.local` for their
  primary NTP source. If Avahi is down on the server, cameras fall
  back to public pools (explicitly listed in the drop-in) — degraded
  but not broken.

## Implementation notes

- `app/server/monitor/models.py` — add `ntp_mode` to `Settings`.
- `app/server/monitor/services/settings_service.py` — add
  `_apply_timezone`, `_apply_ntp_mode`, `get_time_status`,
  `set_manual_time`, `reapply_persisted_time_settings`; wire
  timezone + ntp_mode into `_apply_runtime_changes`.
- `app/server/monitor/api/settings.py` — `GET`/`POST /settings/time`.
- `app/server/monitor/__init__.py` — call
  `reapply_persisted_time_settings()` at startup.
- `app/server/monitor/templates/settings.html` — Date & Time card +
  NTP mode radios.
- `app/camera/config/timesyncd-camera.conf` — camera NTP drop-in.
- `meta-home-monitor/recipes-camera/camera-streamer/camera-streamer_1.0.bb`
  — install the drop-in to
  `/etc/systemd/timesyncd.conf.d/10-home-camera.conf`.
- `openapi/server.yaml` — document `/settings/time` + `TimeStatus`.

## References

- ADR-0008 — persistence contract; establishes the "re-apply from
  `/data` on boot" pattern used here.
- ADR-0018 — dashboard IA; the "1 hour ago" drift was visible on the
  Recent Events feed and motivated this work.
- `systemd-timesyncd.service(8)` for drop-in semantics.
