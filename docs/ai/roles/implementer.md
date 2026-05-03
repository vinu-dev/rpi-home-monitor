# Implementer — project-specific guidance for `vinu-dev/rpi-home-monitor`

This file describes WHAT to do when writing code in this repo. The HOW
(picking issues, rebasing, force-push-with-lease, label transitions,
distilled log writing) lives in agentry's bundled prompt — see
`agentry/config.yml`.

## Repo layout (per `docs/ai/repo-map.md`)

| Area | Purpose |
|---|---|
| `app/server/` | Flask server, API, dashboard, auth, OTA |
| `app/camera/` | camera runtime, pairing, WiFi setup, HTTPS status UI |
| `meta-home-monitor/` | Yocto distro, recipes, image policy |
| `config/` | committed Yocto build configs |
| `scripts/` | build, smoke, deploy, ops helpers |
| `docs/` | system of record |

Tests:
- server: `app/server/tests/`
- camera: `app/camera/tests/`

## Engineering standards (`docs/ai/engineering-standards.md`)

- Service-layer pattern; routes thin, business logic in services
- Constructor injection / explicit wiring
- App-factory + camera lifecycle preserved
- Mutable runtime state on `/data`, NOT in source tree
- Permanent Yocto policy in layers / recipes / packagegroups, NOT `local.conf`
- Readable code over clever code
- Behavior changes require doc changes
- Workflow changes require runbook changes

## Validation matrix to run locally before pushing

Per `docs/ai/validation-and-release.md`. Run only the rows that apply:

| Path you touched | Required validation |
|---|---|
| `app/server/**` | `pytest app/server/tests/ -v --cov=app/server --cov-fail-under=85`, `ruff check .`, `ruff format --check .` |
| `app/camera/**` | `pytest app/camera/tests/ -v --cov=app/camera --cov-fail-under=80`, `ruff check .`, `ruff format --check .` |
| `meta-home-monitor/**`, `config/**` | `bitbake -p` (skip if no Yocto SDK on host — note in PR) |
| `docs/ai/**`, `AGENTS.md`, `CLAUDE.md`, `.github/copilot-*` | `python scripts/ai/validate_repo_ai_setup.py`, `python scripts/ai/check_doc_links.py`, `pre-commit run --all-files` |
| traceability files (REQ, RISK, SEC, TEST IDs touched) | `python tools/traceability/check_traceability.py` |
| `scripts/**`, `.github/workflows/**` | `bash -n` + `shellcheck` |

## Sensitive paths — extra care

Touch any of these → comment on the issue with `needs-security-review` so
Reviewer applies extra scrutiny:

- `**/auth/**`, `**/secrets/**`
- `**/.github/workflows/**`
- `app/camera/camera_streamer/lifecycle.py`, `wifi.py`, `pairing.py`
- certificate / TLS / pairing / OTA flow code

## Traceability (per `docs/ai/medical-traceability.md`)

Meaningful changes update or explicitly confirm:
- requirement IDs (REQ-…)
- architecture links (ARCH-…)
- risk links (RISK-…)
- security links (SEC-…)
- test links (TEST-…)
- traceability matrix entries

Run `python tools/traceability/check_traceability.py` after changing any of:
requirements, risk, security, architecture, tests, or annotated code.

## Branch naming reminder

`feature/<id>-<slug>` — all roles add commits to this same branch (Architect
created it with the spec). Don't create `impl/*` or `design/*` separately.

## Commit message convention

Match existing repo style: `[<id>] <type>: <one-line>` where type is one of
`design`, `impl`, `test`, `fix`, `docs`. The agent's commits should match
the architect's design commit format.

## What to do if you can't fix in this cycle

If validators fail and you can't fix immediately, push current state and
let Tester surface the failure to the next Implementer cycle. Don't sit
on failures — push, label `tests-failed`, exit. Next cycle picks up.

## What to do if a new external dependency is required

Don't add it. Label issue `blocked` and comment explaining the need. New
deps in sensitive areas require human security review.
