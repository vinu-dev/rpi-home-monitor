# Validation And Release

## Validation Matrix

| Area touched | Required validation |
|--------------|---------------------|
| Repository governance, docs, adapters | `python scripts/ai/validate_repo_ai_setup.py`, `python scripts/ai/check_doc_links.py`, `python scripts/ai/check_shell_scripts.py`, `pre-commit run --all-files` |
| Requirements, risk, security, traceability, annotated code | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py`, relevant tests |
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| Camera Python | `pytest app/camera/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | relevant contract tests |
| Security-sensitive path | full relevant suite + smoke |
| Yocto config or recipe | `bitbake -p` and VM build for affected image |
| Hardware behavior | deploy and `scripts/smoke-test.sh` |
| Workflow or shell changes | `bash -n scripts/*.sh`, `shellcheck scripts/*.sh`, workflow lint |

## Release And Deploy Expectations

- Use branches and PRs.
- Record deployment impact in PRs.
- Run live smoke verification after hardware deploys.
- Treat the smoke script and deploy runbook as code.

## CI Enforcement Baseline

- CI should run `pre-commit`, repository governance checks, Ruff, workflow lint,
  shell checks, and the relevant test suites.
- Coverage is enforced, not just reported:
  - server: `--cov-fail-under=85`
  - camera: `--cov-fail-under=80`
- Path filters must include app code, Yocto layers, configs, workflows, docs,
  scripts, and generated adapters.

## AI Rule Review And Eval Practice

For meaningful changes to AI rules, tool adapters, skills, or agent settings:

- compare the change against current official Codex, Claude Code, and Copilot
  guidance when tool behavior matters
- define what improved behavior should be observable
- run deterministic checks such as adapter freshness, doc links, doc map,
  traceability checks, and pre-commit
- for repeatable AI workflows, prefer small eval-style checks that verify the
  expected outcome, process, style, and efficiency signals
- document remaining assumptions and tool-specific limitations in the PR

## Hardware Reality Rule

If code, docs, and device disagree, the device wins until the repo is updated.

## Required Artifacts For Strong PRs

- concise goal
- change summary
- test plan
- deployment impact
- doc impact
- traceability impact and unresolved `OPEN QUESTION:` /
  `REGULATORY REVIEW REQUIRED:` items

## Branch Protection Recommendation

Use GitHub protection for `main`:

- require PRs
- require status checks
- require linear or merge-only policy, whichever the team prefers
- prevent force pushes

These should be enforced in GitHub settings, not just documented.
