# Agentry smoke-test — handoff state (2026-05-03)

This doc captures where the autonomous-agent smoke test is paused. Use it
when picking up in a fresh workspace (e.g., Claude Code Dispatcher on
phone). It tells you what was built, what's running where, what's
committed, and how to continue.

---

## TL;DR

We built a working autonomous multi-agent pipeline (`vinu-dev/agentry`)
and ran it against `vinu-dev/rpi-home-monitor`. The pipeline produced
real designs, code, and test runs. Smoke test was paused with one issue
(#238 TOTP-2FA) ready-for-test on its second pass.

Two repos, both up to date on GitHub:

| Repo | Latest main | Notes |
|---|---|---|
| `vinu-dev/agentry` | latest with stream-json check-in protocol | All universal workflow, supervisor, ping protocol |
| `vinu-dev/rpi-home-monitor` | `cb54abe` | Has `agentry/config.yml` + `docs/ai/roles/*.md` |

No uncommitted work in either repo. Agentry orchestrator is stopped.

---

## What was built (architectural recap)

### Two-repo split

- **agentry** (`vinu-dev/agentry`) — universal orchestrator. Owns the
  workflow (branch model, rebase-on-pickup, label transitions, ping
  protocol, distilled log writing, session continuity). Bundles a
  `config.yml` template whose role prompts encode all of this.
- **rpi-home-monitor** (`vinu-dev/rpi-home-monitor`) — target repo.
  Provides project-specific guidance only:
  - `agentry/config.yml`: target_repo, model selection, role-specific
    overrides
  - `docs/ai/roles/<role>.md`: mission alignment, validation matrix
    rows, sensitive paths, spec template, review checklist

### One branch per issue, all roles share it

Architect creates `feature/<id>-<slug>` from `origin/main`, writes spec.
Implementer rebases on origin/main, adds code, force-push-with-lease.
Tester rebases, runs validators, opens PR (or labels `tests-failed`).
Reviewer rebases, reviews, approves or `blocked`. Code-owner human merges.

### Stream-JSON ping protocol (the headline feature)

Supervisor uses `claude --input-format=stream-json --output-format=stream-json
--verbose`. When `stall_min` or `total_min` thresholds hit, supervisor
sends `AGENTRY-CHECKIN:` over stdin. Agent replies with one of:
- `STATUS:WORKING` → reset stall timer, extend total budget
- `STATUS:DONE` → close stdin, await graceful exit
- `STATUS:BLOCKED <reason>` → close stdin, await exit (with detail)
- `STATUS:NEEDMORETIME N` → extend by N minutes (cap 240)
- no reply → kill (genuinely hung)

Backward-compatible with codex / non-stream-json CLIs (they take the
legacy text-mode path with kill-on-threshold).

### Six roles

researcher · architect · implementer · tester · reviewer · release

All running `claude` (haiku for the smoke test, configurable per-role).

---

## Pipeline state at handoff

### Issues

| Label | Count | Notes |
|---|---|---|
| ready-for-design | 15 | Researcher's queue; architect picks oldest each cycle |
| ready-for-implementation | 1 | #239 (outbound webhooks) |
| ready-for-test | 1 | #238 (TOTP-2FA) — fix-attempt commit awaiting tester validation |
| ready-for-review | 0 | (no PR opened yet) |
| tests-failed | 0 | (cleared after #238 fix attempt) |
| blocked / merge-conflict | 0 | |

### Branches on remote

- `main` @ `cb54abe` — has agentry/ + docs/ai/roles/ + haiku/stream-json args pinned
- `feature/238-totp-2fa` @ `ab5bf9fa` — design + impl + test-fix commits
- `feature/239-outbound-webhooks` @ `514f9187` — design only
- (10 stale `design/*` branches were deleted earlier in the smoke test)

### Open PRs

Zero. Tester needs to pass #238 v2 to open the first PR.

### What the pipeline produced (real artifacts)

- `docs/history/specs/238-totp-2fa.md` — spec by architect (15 acceptance
  criteria, ISO 14971-lite hazards, sensitive-path flags)
- `docs/history/specs/239-outbound-webhooks.md` — spec by architect
- `app/server/monitor/services/totp_service.py` — implementer
- `app/server/monitor/services/request_origin.py` — implementer
- `app/server/monitor/api/auth_totp.py` — implementer
- 5 new test files in `app/server/tests/` — implementer
- Modifications to `auth.py`, `models.py`, `settings_service.py`,
  `audit.py`, `__init__.py`, `login.html`, `settings.html`,
  `requirements.txt`

### Issues filed by Researcher during the test

#238–#253 (16 total). All real product ideas with sources cited.

---

## Where things are on disk

### Original (Win PC, where the smoke test ran)

| Path | What |
|---|---|
| `D:\Claude\WS-2\skynet-agentry\` | agentry repo clone |
| `D:\Claude\WS-2\smoke-test\rpi-home-monitor\` | target repo clone, has `agentry/.venv/` and stale logs |

### Dispatcher (where work continues)

| Path | What |
|---|---|
| `D:\Dispatch\agentry\` | agentry repo clone (already cloned) |
| `D:\Dispatch\rpi-home-monitor\` | target repo clone (already cloned, has `agentry/` and `docs/ai/roles/` from main) |

Everything important is on GitHub. The dispatcher should `git pull` in
both directories before starting to make sure it has the latest.

---

## How to continue on Dispatcher (`D:\Dispatch\`)

The two repos are already cloned at `D:\Dispatch\agentry\` and
`D:\Dispatch\rpi-home-monitor\`. Steps:

### 1. Pull latest on both repos

PowerShell:
```powershell
cd D:\Dispatch\agentry
git pull --rebase origin main

cd D:\Dispatch\rpi-home-monitor
git checkout main
git pull --rebase origin main
```

### 2. Authenticate the CLIs (only if not already done on this dispatcher)

```powershell
claude auth login    # opens browser; subscription
gh auth status       # confirm authed; if not: gh auth login
```

> **Important**: do NOT run `claude auth login` on a machine where the
> Claude Code desktop app is logged in with the same account — the OAuth
> rotation invalidates the desktop session. (Hit this on the original
> Win PC; works fine on a clean dispatcher.)

### 3. Set up the venv inside the target

```powershell
cd D:\Dispatch\rpi-home-monitor
python -m venv agentry\.venv
.\agentry\.venv\Scripts\python -m pip install --upgrade pip
.\agentry\.venv\Scripts\python -m pip install -e D:\Dispatch\agentry
```

### 4. Confirm config has the right args

```powershell
Get-Content agentry\config.yml | Select-String -Pattern "args:|model" | Select-Object -First 8
```

You should see for each role:
```
args: ["-p", "--model", "claude-haiku-4-5-20251001",
       "--input-format=stream-json", "--output-format=stream-json",
       "--verbose", "--dangerously-skip-permissions"]
```

If any role lacks those flags, that's a regression — re-add them.

### 5. .env (no PAT needed; `gh` keyring auth covers it)

```powershell
@'
# GITHUB_TOKEN intentionally NOT set — fall back to gh CLI keyring auth.
'@ | Set-Content -Encoding utf8 agentry\.env
```

### 6. Run doctor

```powershell
.\agentry\.venv\Scripts\agentry doctor --target .
```

Should end with `=> RESULT: PASS`.

### 7. Start

```powershell
.\agentry\.venv\Scripts\agentry start --target .
```

Foreground process; Ctrl-C to stop. Logs will land in
`agentry\logs\<role>\<timestamp>.log` and per-issue distilled logs in
`agentry\logs\issues\<id>-<slug>\<role>.log`.

---

## Picking up where we left off

The orchestrator will, on first cycles:

1. **Tester** picks `ready-for-test` → #238, runs validators on
   `feature/238-totp-2fa` (commits already pushed, including the
   test-assertion fix `ab5bf9fa`). If green → opens PR + labels
   `ready-for-review`. If red → labels `tests-failed`, implementer
   re-tries on next cycle.
2. **Implementer** picks `ready-for-implementation` → #239 (webhooks).
   Reads spec from `docs/history/specs/239-outbound-webhooks.md`,
   implements on `feature/239-outbound-webhooks`, runs validators,
   pushes, label → `ready-for-test`.
3. **Architect** picks oldest `ready-for-design` (#240 next:
   "Config backup and restore"), writes spec, pushes
   `feature/240-config-backup-restore`, label →
   `ready-for-implementation`.
4. **Reviewer** waits for the first PR.
5. **Researcher** scans competing projects again at next 60-min mark.

You'll see the **first PR open** when tester passes #238. That's the
true "end-to-end" milestone for the smoke test.

---

## Open architectural concerns (not blocking, worth flagging)

- **Shared working tree across roles.** Architect and Implementer both
  do `git switch feature/<id>` in the same `.git`. If their cycles
  overlap, branch state can flip mid-work. Hit this once during the
  test; recovered. Long-term fix: per-role git worktrees in
  `agentry/worktrees/<role>/`. Issue not yet filed — file when picking
  up if you want to address it.
- **Log size**. Stream-json mode + `--verbose` produces large per-cycle
  logs (200–900 KB per cycle). Per-issue distilled logs (in
  `agentry/logs/issues/<id>/`) are small and AI-readable; raw cycle logs
  in `agentry/logs/<role>/` need rotation. Not implemented yet.
- **Cross-issue dependencies.** Researcher noted things like "#246 active
  sessions complements #238 TOTP". Pipeline doesn't enforce ordering;
  rebasing handles file conflicts but not logical dependencies. Worth a
  `dependency_tracker` role if scope grows.
- **Hardware-only validation.** The Pi-specific tests in the validation
  matrix (`bitbake -p`, hardware smoke) are skipped on a non-Pi host.
  Tester correctly notes the skip in PR bodies. Real hardware
  verification still requires a human on a Pi.

---

## What the dispatcher should do FIRST

1. Read this doc end-to-end.
2. Read `docs/ai/index.md` to understand the rpi-home-monitor's project
   model (medical-grade traceability, etc).
3. Read `docs/ai/roles/<role>.md` files to understand each role's
   project-specific guidance.
4. Run `agentry doctor --target .` — confirm green.
5. `agentry start --target .` — run.
6. Watch the GitHub repo at `vinu-dev/rpi-home-monitor` for label
   movement and PRs.

---

## What's already pushed and final (no need to redo)

- agentry repo: stream-json supervisor + check-in protocol + UTF-8 fix
  + Windows shutil.which fix + bundled config + role templates
- rpi-home-monitor `main`: setup files + slim role rule files
  + haiku/stream-json args pinned
- 2 design specs (`#238`, `#239`) on their feature branches
- TOTP-2FA implementation + test-fix on `feature/238-totp-2fa`
- 16 issues filed and labeled by Researcher
- 6 session-continuity files in `agentry/state/sessions/<role>/latest.md`

---

## Contact / debug paths

- Per-role distilled logs (small, AI-readable):
  - `agentry/logs/issues/<id>-<slug>/<role>.log`
  - `agentry/logs/roles/{researcher,release}/YYYY-MM-DD.log`
- Per-cycle raw logs (large, useful for debugging):
  - `agentry/logs/<role>/<unix-timestamp>.log`
- Session continuity (own-role memory):
  - `agentry/state/sessions/<role>/latest.md`
- Orchestrator stdout: wherever you redirected `agentry start`.

If a role gets stuck: check its latest raw log for the AGENTRY-CHECKIN
message and the agent's STATUS: reply. If no STATUS: reply, that's a
genuine hang — the supervisor will have killed and the next cycle will
respawn.
