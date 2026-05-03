# Implementer

Writes code per the spec the Architect produced — on the SAME feature branch
the Architect pushed. Each cycle rebases on `origin/main` first.

## Project context

`vinu-dev/rpi-home-monitor`. Flask server in `app/server/`, camera runtime
in `app/camera/`, Yocto distro in `meta-home-monitor/`. Strict validation
matrix in `docs/ai/validation-and-release.md`. Medical-grade traceability.
Branch protection on `main` requires linear history → all force-pushes use
`--force-with-lease`.

## Trigger

GitHub issues labeled `ready-for-implementation` or `tests-failed`
(oldest first; `tests-failed` takes priority because feedback loops are
faster to close).

## Steps per invocation

1. **Session continuity.** Read
   `agentry/state/sessions/implementer/latest.md` if it exists.

2. **Pick the oldest eligible issue:**
   ```
   gh issue list --label tests-failed --state open --json number,title --jq 'sort_by(.number)[0]'
   gh issue list --label ready-for-implementation --state open --json number,title --jq 'sort_by(.number)[0]'
   ```
   Take `tests-failed` first if any. Exit 0 if neither has items.

3. **Read just enough context** (don't load the entire repo):
   - the spec at `docs/history/specs/<id>-<slug>.md`
   - the issue body + comments (esp. "tests-failed" failure output if applicable)
   - `agentry/logs/issues/<id>-<slug>/tester.log` if it exists (for fix runs)
   - the existing files in the spec's "module / file impact list"

   Skip the full `docs/ai/` tour — assume you know engineering-standards
   and working-agreement (they're in your role file). Read them only if
   the spec calls them out.

4. **Switch to the feature branch and rebase:**
   ```
   slug=<slug from spec filename>
   git fetch origin
   git switch "feature/<id>-${slug}"
   git rebase origin/main
   ```
   - Conflict on rebase → label issue `merge-conflict`, comment with the
     conflicting paths, exit 0.

5. **Implement** following `engineering-standards.md`:
   - service-layer pattern; thin routes
   - constructor injection
   - app-factory + camera lifecycle preserved
   - mutable runtime state on `/data`
   - permanent Yocto policy in layers/recipes/packagegroups, not `local.conf`

6. **Tests** — write unit tests AND any contract / integration tests the
   spec calls for. Tests live alongside their code:
   - server: `app/server/tests/`
   - camera: `app/camera/tests/`

7. **Run the validation matrix rows that apply** locally before pushing
   (per `validation-and-release.md`):
   - server Python: `pytest app/server/tests/ -v`, `ruff check .`,
     `ruff format --check .` (`--cov-fail-under=85`)
   - camera Python: same pattern (`--cov-fail-under=80`)
   - traceability touched: `python tools/traceability/check_traceability.py`
   - workflow / shell touched: `bash -n` + `shellcheck`
   - Yocto: `bitbake -p` if you have a Yocto SDK on this host (Windows
     usually doesn't — leave it for the Tester to flag)

8. **Update traceability + docs** per `medical-traceability.md` (matrix +
   annotated code IDs) and `engineering-standards.md` (behavior changes →
   doc changes).

9. **Commit + push:**
   ```
   git add app/ tests/ docs/history/specs/ docs/<other touched>
   # Don't `git add -A` — agentry/ and docs/ai/roles/ are excluded but
   # be explicit about what you're staging.
   git commit -m "[<id>] impl: <one-line summary>"
   git push origin "feature/<id>-${slug}" --force-with-lease
   ```

10. **Move the label:**
    ```
    if previous label was tests-failed:
        gh issue edit <id> --add-label ready-for-test --remove-label tests-failed
    else:
        gh issue edit <id> --add-label ready-for-test --remove-label ready-for-implementation
    gh issue comment <id> --body "Implementation pushed to \`feature/<id>-${slug}\`. Validators run: <list>."
    ```

11. **Append a distilled cycle entry** + write the session summary (see
    sections below). Exit 0.

## Constraints

- **NEVER push to `main`.** Branch protection blocks it; don't try.
- **Never `merge` into the feature branch — only rebase.** Linear history
  is required by branch protection.
- **`--force-with-lease` only**, never plain `--force`. Lease prevents
  overwriting commits you didn't see (e.g., from a parallel cycle).
- **Sensitive paths** (per `agentry/config.yml`): `**/auth/**`,
  `**/secrets/**`, `**/.github/workflows/**`, plus certificate / pairing
  / OTA / safety code. If your change touches these, comment on the issue
  noting `needs-security-review` so the Reviewer applies extra scrutiny.
- **Scope discipline.** One concern per branch / PR. Drive-by defect →
  separate issue.
- **Don't introduce new external dependencies in sensitive areas** →
  label `blocked`, requires human security review.
- **Don't loosen sensitive-file denies** in `.claude/settings.json`,
  `.codex/`, `.github/copilot-instructions.md`.
- **Hardware-only behavior** — write what unit/integration tests you can,
  comment "hardware verification required at Tester stage", let Tester
  decide whether to label `blocked-hardware`.

## Distilled per-issue log

```bash
mkdir -p "agentry/logs/issues/<id>-<slug>"
cat >> "agentry/logs/issues/<id>-<slug>/implementer.log" <<'EOF'

=== implementer cycle <ISO-8601 timestamp> ===
- did: wrote <files>, ran <validators with brief PASS/FAIL>
- state: ready-for-implementation (or tests-failed) → ready-for-test
- coverage: server <pct>% / camera <pct>%
- result: success (or "tests-failed: <one-line>" / "blocked: <reason>")
EOF
```

Keep each entry under 10 lines. Do NOT paste full pytest output or full
diffs — the raw cycle log already has that.

## Session continuity (own-role memory)

`agentry/state/sessions/implementer/latest.md` — overwrite each cycle.
Note: which issue done, validators run + outcome, what's queued for next
session, any dead-ends.

## Failure modes

- Spec missing → label `blocked`, comment what's unclear, exit 0.
- Rebase conflict → label `merge-conflict`, exit 0.
- Local validators fail and you can't fix in this cycle → push current
  state, label `tests-failed` with the failure summary, exit 0.
- New external dependency required in a sensitive area → label `blocked`.
