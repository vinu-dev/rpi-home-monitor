# `agentry/` - Local Agentry Installation

This folder is the repo-local Agentry dependency for this target repository.
Each target repo gets its own copy.

## What Is In Here

| Path | Purpose | Commit? |
|------|---------|---------|
| `config.yml` | Role roster, model/CLI assignment, timeouts, run mode | yes |
| `start.ps1` / `start.sh` | Entry points for start, GUI, configure, stop | yes |
| `.env.example` | Secrets template | yes |
| `.gitignore` | Ignores local runtime files | yes |
| `.env` | Real secrets | no |
| `.venv/` | Repo-local Agentry Python venv | no |
| `logs/` | Per-role stdout logs | no |
| `state/` | Runtime sessions and role continuity notes | no |
| `worktrees/` | Per-role git worktrees when enabled | no |

## Role Rules

Project-specific role rules live here:

```text
docs/ai/roles/
  researcher.md
  architect.md
  implementer.md
  tester.md
  reviewer.md
  release.md
```

Edit those files for project behavior. The prompts in `agentry/config.yml`
point at them.

The standard Implementer retry path and Tester workflow reset clean local
feature branches from `origin/feature/<id>-<slug>` before rebasing. This keeps
stale role worktrees from reporting false merge conflicts after a supervisor or
agent force-with-lease push.

The standard Tester prompt opens pull requests with `gh pr create --body-file`
so multi-line validation evidence does not depend on shell quoting behavior.

## Machine Setup

Run once per machine:

```powershell
iwr -useb https://raw.githubusercontent.com/vinu-dev/agentry/main/scripts/install-deps.ps1 | iex
```

```bash
curl -fsSL https://raw.githubusercontent.com/vinu-dev/agentry/main/scripts/install-deps.sh | bash
```

Then authenticate the LLM CLIs you plan to use:

```bash
npx --yes @anthropic-ai/claude-code login
codex login
```

## Configure Without Starting Agents

```powershell
.\agentry\start.ps1 configure --target . --defaults
.\agentry\start.ps1 gui --target .
```

```bash
./agentry/start.sh configure --target . --defaults
./agentry/start.sh gui --target .
```

Default mode is `pipeline`: existing GitHub labels move through the pipeline,
but Researcher does not create new issues. Use `manual` when you want no roles
to start. Use `autonomous` only when Researcher should be allowed to create new
work.

## Model Routing For This Repo

This repo is configured to use alternating model perspectives:

- Architect: Claude Code via `npx @anthropic-ai/claude-code --model opus`
- Implementer: Codex via `npx @openai/codex -m gpt-5.4`
- Tester: Codex via `npx @openai/codex -m gpt-5.4`
- Reviewer: Claude Code via `npx @anthropic-ai/claude-code --model opus`

Reviewer approval is recorded with a PR comment beginning
`Agentry review outcome:` plus the `agent-approved` label. The target config
does not use formal `gh pr review` by default because GitHub rejects
same-author self-review.

Reviewer also serializes shared conflict-zone PRs. Paths listed in
`merge_sensitive_paths` in `agentry/config.yml` are reviewed as a merge train:
the oldest matching PR can proceed, while newer matching PRs receive
`merge-train-waiting` until they can rebase after the older PR merges.

Researcher and Release are disabled by default. Enable them only when you want
new autonomous issue discovery or release automation.

`opus` is the Claude Code alias for the latest Opus model. Keep this alias
instead of pinning a dated Claude model unless a rollback is intentional.

The start scripts currently pin Agentry to
`fd627abae6e1f763ed7c40c373a7740f475d3771`, which includes the reviewer
comment workflow, the stream-json watchdog fix for active Claude Code tool
activity during check-ins, branch-reset hardening, Tester PR body-file
creation, safe wrapper subcommands, and merge-train waiting for shared
conflict-zone PRs. Wrapper subcommands such as `status`, `doctor`, `configure`,
and `gui` reuse an existing venv instead of force-reinstalling only because the
local install-ref marker is missing or stale, so health checks are safe while
Agentry is live.

## Start

```powershell
.\agentry\start.ps1
```

```bash
./agentry/start.sh
```

Foreground only. Ctrl-C, closing the terminal, or rebooting stops it. There is
no background service by default.

## Stop

```powershell
.\agentry\start.ps1 stop --target . --all
```

```bash
./agentry/start.sh stop --target . --all
```

Stop is conservative: Agentry kills only currently running session PIDs, not
completed or stale records.

Completed sessions clear their recorded PID before status/dashboard rendering,
so a visible PID means there is still an active role process to inspect or stop.

## Upgrade

The start scripts install Agentry from the Git ref pinned in the script. To
upgrade intentionally, update that ref or set `AGENTRY_INSTALL_REF`, stop any
running Agentry process that uses this venv, and rerun the wrapper with
`AGENTRY_FORCE_INSTALL=1`. If the venv is already corrupted, delete `.venv/`
and rerun the start script.

On Windows, the venv cannot replace `agentry\.venv\Scripts\agentry.exe` while
an old supervisor is still using it.

## Remove

Delete this `agentry/` folder. Optionally keep or delete `docs/ai/roles/`
depending on whether you want to preserve the project role documentation.
