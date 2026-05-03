# Architect — project-specific guidance for `vinu-dev/rpi-home-monitor`

This file describes WHAT a good spec looks like in this repo. The HOW
(branch model, rebase, force-push-with-lease, label transitions, distilled
log writing, session continuity) lives in agentry's bundled prompt — see
`agentry/config.yml`.

## Read order before designing

Per `docs/ai/execution-rules.md` "Read Order For Feature Work":

1. `docs/ai/mission-and-goals.md`
2. `docs/ai/repo-map.md`
3. `docs/ai/design-standards.md`
4. `docs/ai/engineering-standards.md`
5. `docs/ai/working-agreement.md`
6. `docs/ai/medical-traceability.md`
7. `docs/ai/validation-and-release.md`
8. Sample 1–2 existing specs from `docs/history/specs/` as formatting
   templates (e.g. `r1-camera-offline-alerts.md`)
9. `docs/history/releases/` — does this issue fit an open release plan?

## Spec MUST contain

Write to `docs/history/specs/<id>-<slug>.md`. Required sections:

- **Goal** — user/operator outcome (restate the issue's goal).
- **Context** — relevant existing code (cite specific files in `app/server/`
  / `app/camera/` / `meta-home-monitor/`).
- **User-facing behavior** — primary path AND failure states (per
  `design-standards.md`'s "every user-facing flow should have a clear
  primary path and clear failure states").
- **Acceptance criteria** — testable bullets, each with the validation
  mechanism (unit test, contract test, smoke test, hardware verification).
- **Non-goals** — what we are explicitly NOT doing.
- **Module / file impact list** — concrete files and likely changes.
- **Validation plan** — pull from `validation-and-release.md`'s validation
  matrix the rows that apply.
- **Risk** — ISO 14971-lite framing: hazards, severity, probability,
  proposed risk controls. Reference `docs/risk/` if relevant.
- **Security** — threat-model deltas. Note if change touches sensitive
  paths (`**/auth/**`, `**/secrets/**`, `**/.github/workflows/**`,
  certificate / pairing / OTA flows).
- **Traceability** — placeholder REQ / ARCH / RISK / SEC / TEST IDs the
  Implementer fills in (per `medical-traceability.md`).
- **Deployment impact** — Yocto rebuild needed? OTA path? Hardware
  verification?
- **Open questions** — blocking ones → label issue `blocked` and stop.

## Existing patterns to preserve

Don't invent new architecture when these cover the case:

- **Service-layer**: routes thin, business logic in services
  (`app/server/monitor/services/`)
- **App-factory** for the Flask server
- **Camera lifecycle / state-machine** (`app/camera/camera_streamer/lifecycle.py`)
- **Mutable runtime state on `/data`** (NOT in source tree)
- **Yocto policy in layers/recipes/packagegroups**, not `local.conf`

## Sensitive paths needing extra design scrutiny

If the spec touches any of these, call it out explicitly:

- `**/auth/**`, `**/secrets/**`
- `**/.github/workflows/**`
- `app/camera/camera_streamer/lifecycle.py`, `wifi.py`, `pairing.py`
- certificate / TLS / pairing / OTA flow code
- `docs/cybersecurity/**`, `docs/risk/**`

## When to label `blocked` instead of designing

- regulatory clearance required
- hardware redesign required
- major architecture rework (changing the service-layer pattern, etc.)
- new external dependency in a sensitive area
- the issue body lacks goal or sources (refer back to Researcher)
