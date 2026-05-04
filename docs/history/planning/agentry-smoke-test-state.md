# Agentry Smoke-Test State (2026-05-03)

This document records the live Agentry integration state for
`vinu-dev/rpi-home-monitor`. It is an operator handoff, not product
requirements.

## Current Summary

The pipeline has produced real issue movement, design specs, implementation
attempts, validation output, and platform fixes. It is no longer a dry run.

Two repositories stay separate:

| Repository | Responsibility | Current baseline |
|---|---|---|
| `vinu-dev/agentry` | Universal platform, supervisor, prompts, session handling | `ad2c7a8535c1ebdcfcd9bcb922e1500d61a8d6b4` |
| `vinu-dev/rpi-home-monitor` | Target config, product code, product docs, role guidance | pinned to the Agentry baseline above |

Do not mix platform fixes into this repo. Do not put target project fixes in
the Agentry repo.

## Platform Fixes Already Merged

- `vinu-dev/agentry#16`: Claude Code stream-json `result` events now complete
  role runs correctly, and role prompts are less likely to fall into generic
  "what should I do?" mode.
- `vinu-dev/agentry#17`: universal role prompts now instruct each role to
  process exactly one work item per run, then exit. This prevents Architect or
  another role from burning tokens by drifting into the next issue.
- `vinu-dev/agentry#19`: completed sessions clear stale PIDs from
  status/dashboard, the runtime contract forbids role-level wakeup/scheduling
  tools, and the standard Reviewer leaves pending-CI PRs in
  `ready-for-review` for the next orchestrator interval.

## Target Configuration

The target Agentry scripts pin the platform to:

```text
ad2c7a8535c1ebdcfcd9bcb922e1500d61a8d6b4
```

Model routing:

- Architect: Claude Code with `--model opus`
- Implementer: Codex with `gpt-5.4`
- Tester: Codex with `gpt-5.4`, 60 minute total/stall budget, 80000 token budget
- Reviewer: Claude Code with `--model opus`
- Researcher: disabled by default
- Release: disabled by default

`opus` is used as the Claude Code latest-Opus alias so the operator gets the
large Claude model without hardcoding a dated model name.

## Current Pipeline State

As of this update:

- #238 is `tests-failed`. Tester found real failures: formatting drift, missing
  traceability IDs, and settings-service test failures.
- #239 is in implementation for outbound webhook delivery.
- #240, #241, and #242 were moved by Architect to `ready-for-implementation`.
- #243 and newer issues remain in `ready-for-design`.
- No Researcher or Release automation should run unless explicitly enabled by
  an operator.

## Operator Workflow

From `D:\Codex\rpi-home-monitor`:

```powershell
git status -sb
git pull --ff-only origin main
powershell -NoProfile -ExecutionPolicy Bypass -File .\agentry\start.ps1 status --target .
powershell -NoProfile -ExecutionPolicy Bypass -File .\agentry\start.ps1 doctor --target . --init-labels
powershell -NoProfile -ExecutionPolicy Bypass -File .\agentry\start.ps1
```

Open the GUI when useful:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\agentry\start.ps1 gui --target .
```

GUI URL: `http://127.0.0.1:4783`

No committed `agentry/.env` is required on this machine. GitHub access uses the
authenticated `gh` keyring.

## Windows Pin Upgrade Note

The start scripts reinstall Agentry when the pinned Git ref changes. On
Windows, stop running Agentry processes before testing a new pin because
`agentry\.venv\Scripts\agentry.exe` is locked while the old supervisor is
running.

Use the Agentry stop command first:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\agentry\start.ps1 stop --target . --all
```

Only stop repo-local Agentry/session processes. Do not stop Codex Desktop or
Claude Desktop.

## Supervisor Policy

- Watch issues, labels, PRs, logs, and status periodically.
- Let Agentry run a full pipeline path.
- Merge reviewed PRs when ready, using admin bypass if branch protection blocks
  the authorized merge.
- Treat token budgets as warnings first, not automatic kill triggers.
- Use the check-in/status path before terminating sessions. Killing is the last
  resort for sessions that fail to respond or clearly cannot progress.
- Commit and push platform or target fixes frequently on `codex/*` branches.

## Documentation Policy

Every functional change should update the matching docs in the same PR:

- operator behavior: `agentry/README.md` and this handoff document
- role behavior: `agentry/config.yml` plus the relevant `docs/ai/roles/*.md`
- product behavior: `README.md`, `docs/guides/`, requirements, risk,
  traceability, specs, and test docs as applicable
- architectural changes: ADRs or architecture docs under `docs/history/adr/`
  and `docs/architecture/`

If a change does not need a doc update, say why in the PR body.
