# Tester — project-specific guidance for `vinu-dev/rpi-home-monitor`

This file describes WHAT to validate in this repo. The HOW (picking issues,
rebasing the feature branch, opening the PR, label transitions) lives in
agentry's bundled prompt — see `agentry/config.yml`.

## Validation matrix (per `docs/ai/validation-and-release.md`)

Inspect the diff against `main` (`git diff --name-only origin/main...HEAD`)
and run the rows that apply:

| Path | Validators |
|---|---|
| `app/server/**` | `pytest app/server/tests/ -v --cov=app/server --cov-fail-under=85`<br>`ruff check .`<br>`ruff format --check .` |
| `app/camera/**` | `pytest app/camera/tests/ -v --cov=app/camera --cov-fail-under=80`<br>`ruff check .`<br>`ruff format --check .` |
| Any code change / repo governance / docs | `python tools/docs/check_doc_map.py`<br>`python scripts/ai/validate_repo_ai_setup.py`<br>`python scripts/ai/check_doc_links.py`<br>`python scripts/ai/check_shell_scripts.py`<br>`python scripts/check_version_consistency.py`<br>`python scripts/check_versioning_design.py`<br>`pre-commit run --all-files` |
| Traceability touched | `python tools/traceability/check_traceability.py` |
| `meta-home-monitor/**`, `config/**` (Yocto) | `bitbake -p` |
| `scripts/**`, `.github/workflows/**` | `bash -n <script>`, `shellcheck <script>` |

## Coverage thresholds (do NOT lower)

- server: `--cov-fail-under=85`
- camera: `--cov-fail-under=80`

## When the host can't run a row

- **Yocto** (`bitbake -p`): if the orchestrator host has no Yocto SDK
  (typically true on Windows), SKIP and note in the PR body:
  `Yocto bitbake parse skipped — host has no SDK; Yocto verification
  needed in Linux build env before merge.`
- **Hardware acceptance criteria**: SKIP locally, label the PR
  `needs-hardware-verification`. The human reviewer or a hardware-attached
  tester run handles it.

## PR body template

Open the PR with this body:

```
Closes #<id>

## Summary
<one paragraph from the spec's Goal>

## Spec
`docs/history/specs/<id>-<slug>.md`

## Validation evidence
- <command 1>: PASS
- <command 2>: PASS
- python tools/docs/check_doc_map.py: PASS
- python scripts/ai/validate_repo_ai_setup.py: PASS
- python scripts/ai/check_doc_links.py: PASS
- python scripts/ai/check_shell_scripts.py: PASS
- python scripts/check_version_consistency.py: PASS
- python scripts/check_versioning_design.py: PASS
- pre-commit run --all-files: PASS
- ruff check .: PASS
- ruff format --check .: PASS
- coverage: server <pct>% / camera <pct>%
- skipped: <bitbake / hardware> (reason)

## Deployment impact
<copy from the spec>

## Traceability
<updated IDs / matrix entries>

## Out of scope
<copy from the spec>
```

## Failure handling

- ANY validator red → label issue `tests-failed` and post the failure
  output as a comment (truncate stack traces past 200 lines). Do NOT open
  a PR.
- Flaky test → still label `tests-failed`. Implementer triages on next
  pickup.

Missing local tooling is not a pass. Install reasonable Python tools such as
`pre-commit`; otherwise mark the row as blocked and do not claim green.

## Don't suppress, don't lower thresholds, don't auto-merge

- Don't add `# noqa` or `pytest.skip` to make tests pass.
- Don't reduce `--cov-fail-under` numbers.
- Don't `gh pr merge` — apply `ready-for-review` only; the code-owner
  human merges manually.
