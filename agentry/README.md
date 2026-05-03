# `agentry/` — local Agentry installation for this repo

This folder is the gtest-style "fetched dependency" for [Agentry](https://github.com/vinu-dev/agentry). It's how the orchestrator runs against THIS repo. Each target repo gets its own copy.

## What's in here

| File | Purpose | Committed? |
|------|---------|-----------|
| `config.yml` | Picks which LLM CLI handles each role (Claude / Codex / etc.) and the timeouts | yes |
| `start.ps1` / `start.sh` | The entry point. Run this every time you want Agentry running. Creates `.venv/` and installs Agentry on first run. | yes |
| `.env.example` | Template for your secrets (GITHUB_TOKEN etc.) | yes |
| `.gitignore` | Ignores the next four entries | yes |
| `.env` | Your actual secrets — copied from `.env.example` and filled in by you | **no, gitignored** |
| `.venv/` | Python venv with Agentry pip-installed; auto-created on first run | **no, gitignored** |
| `logs/` | Per-role agent stdout, one timestamped file per run | **no, gitignored** |
| `state/` | Runtime state | **no, gitignored** |

## Where role rule files live

NOT here. They live at the **standard target-repo location**:

```
<this-repo>/docs/ai/roles/
├── researcher.md
├── architect.md
├── implementer.md
├── tester.md
├── reviewer.md
└── release.md
```

Edit those for project-specific instructions per role. The prompts in `agentry/config.yml` already point at them.

## How to use

### One time per machine
Install Python, Node.js, Claude Code, Codex CLI:

```powershell
# Windows
iwr -useb https://raw.githubusercontent.com/vinu-dev/agentry/main/scripts/install-deps.ps1 | iex
```

```bash
# Linux
curl -fsSL https://raw.githubusercontent.com/vinu-dev/agentry/main/scripts/install-deps.sh | bash
```

Then authenticate the LLM CLIs (browser flow):

```
claude login
codex login
```

### One time per repo (this folder)
1. Copy `.env.example` to `.env` and fill in `GITHUB_TOKEN`
2. Edit `config.yml` — pick which CLI handles each role
3. Edit `../docs/ai/roles/*.md` if you want project-specific role instructions

### Every time you want Agentry running

```powershell
# Windows
.\agentry\start.ps1
```

```bash
# Linux
./agentry/start.sh
```

Foreground. Ctrl-C to stop. Close the terminal to stop. Reboot kills it. **No service.** Run the script again when you want it running again.

## To upgrade Agentry

Delete `.venv/` and run `start.ps1` / `start.sh` again. The venv is recreated and pulls the latest from GitHub.

## To remove Agentry from this repo

Just delete this `agentry/` folder. Optionally also delete `docs/ai/roles/` (or keep them — they're useful project documentation regardless of whether Agentry is running).
