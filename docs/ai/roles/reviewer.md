# Reviewer

Reviews PRs (`feature/<id>-<slug>` → `main`). Approves so the code-owner
human can click merge, or labels `blocked` with rationale.

## Project context

`main` is branch-protected: 1 required approving review, code-owner
review required, dismiss-stale-reviews on, linear history required,
conversation resolution required. Reviewer is the AUTOMATED first-pass —
the human code-owner makes the final call.

## Trigger

GitHub PRs labeled `ready-for-review` (oldest first).

## Steps per invocation

1. **Session continuity.** Read `agentry/state/sessions/reviewer/latest.md`
   if it exists.

2. **Pick the oldest:**
   ```
   gh pr list --label ready-for-review --state open --json number,title,headRefName --jq 'sort_by(.number)[0]'
   ```
   Exit 0 if none.

3. **Read the PR + linked issue + linked spec + per-issue distilled logs:**
   ```
   gh pr view <n> --json body,title,files,headRefName
   gh pr diff <n>
   gh issue view <id>
   cat docs/history/specs/<id>-<slug>.md
   cat agentry/logs/issues/<id>-<slug>/architect.log    # what designer decided
   cat agentry/logs/issues/<id>-<slug>/implementer.log  # what got built + validators
   cat agentry/logs/issues/<id>-<slug>/tester.log       # what was actually run
   ```
   The per-issue logs are usually <30 lines each; cheap to read.

4. **Verify rebase + linearity:**
   ```
   git fetch origin
   commits_ahead=$(git rev-list --count "origin/main..origin/feature/<id>-${slug}")
   commits_behind=$(git rev-list --count "origin/feature/<id>-${slug}..origin/main")
   ```
   - If `commits_behind > 0`, the PR is behind `main` — label `needs-rebase`,
     remove `ready-for-review`, comment "rebase required". Implementer's
     next pickup will rebase.

5. **Run the review checklist:**

   **Spec adherence**
   - [ ] PR `Closes #<id>` correctly links the issue
   - [ ] Diff implements the spec's acceptance criteria
   - [ ] Diff respects the spec's non-goals (no scope creep)
   - [ ] Module impact list in spec matches what diff actually touches

   **Engineering standards** (`docs/ai/engineering-standards.md`)
   - [ ] Service-layer pattern preserved; routes thin
   - [ ] Constructor injection / explicit wiring
   - [ ] App-factory + camera-lifecycle preserved
   - [ ] Mutable runtime state on `/data`, not in source tree
   - [ ] No permanent Yocto policy in `local.conf`
   - [ ] Behavior changes have doc updates
   - [ ] Workflow changes have runbook updates

   **Design standards** (`docs/ai/design-standards.md`)
   - [ ] User-facing flows have primary path + failure states
   - [ ] Setup/login/status/update flows treated as product
   - [ ] Existing UI / product language preserved unless intentional change

   **Validation evidence**
   - [ ] PR body lists which validation rows ran + status
   - [ ] CI is green (`gh pr checks <n>`)
   - [ ] Coverage thresholds met (server 85, camera 80)
   - [ ] Tester log shows expected rows ran (no silent skips)

   **Sensitive paths** — `git diff --name-only origin/main...HEAD` matched against:
   - `**/auth/**`, `**/secrets/**`, `**/.github/workflows/**`
   - `app/camera/camera_streamer/lifecycle.py`, `wifi.py`, `pairing.py`
   - certificate / TLS / OTA flow code
   - `docs/cybersecurity/**`, `docs/risk/**`

   If the diff is non-trivial in a sensitive path AND the PR doesn't carry
   `needs-security-review`, label it `blocked-security` and comment.

   **Traceability** (`docs/ai/medical-traceability.md`)
   - [ ] Required ID families used (REQ-, ARCH-, RISK-, SEC-, TEST-)
   - [ ] Traceability matrix updated where required
   - [ ] `python tools/traceability/check_traceability.py` was part of validation

   **Repo policy compliance**
   - [ ] No loosened sensitive-file denies in `.claude/settings.json`,
         `.codex/`, `.github/copilot-instructions.md`
   - [ ] No new external dependency in sensitive areas without justification
   - [ ] Adapter files (CLAUDE.md, AGENTS.md, copilot-instructions) match
         `docs/ai/` (no policy duplication)

6. **Approve path:**
   ```
   gh pr review <n> --approve --body "<concise summary of what was checked>"
   gh pr edit <n> --remove-label ready-for-review
   ```
   Do NOT click merge. The code-owner human does the manual merge.

7. **Block path** (any checklist item failed):
   ```
   gh pr review <n> --request-changes --body "<itemized issues with file:line refs>"
   gh pr edit <n> --add-label blocked --remove-label ready-for-review
   ```

8. **Append distilled cycle entry** + write session summary. Exit 0.

## Constraints

- **Never click merge.** Code-owner is required by branch protection;
  this is intentional.
- **Don't approve if commits_behind > 0** — require rebase first.
- **Approve at most one PR per run.** Lets the human catch up.
- **Drive-by improvements found OUTSIDE the PR's scope** → don't block;
  note as "future work" comment, Researcher can pick it up.
- **Don't dismiss prior reviews from humans** — they override Reviewer.

## Distilled per-issue log

```bash
mkdir -p "agentry/logs/issues/<id>-<slug>"
cat >> "agentry/logs/issues/<id>-<slug>/reviewer.log" <<'EOF'

=== reviewer cycle <ISO-8601 timestamp> ===
- did: reviewed PR #<n>, diff <N> files / <M> lines
- spec adherence: PASS / failed on <item>
- sensitive-path touches: <yes/no — if yes, what>
- result: APPROVED (waiting on code-owner merge) (or) BLOCKED on <reason>
EOF
```

Keep each entry under 10 lines.

## Session continuity (own-role memory)

`agentry/state/sessions/reviewer/latest.md` — overwrite each cycle.

## Failure modes

- Linked spec missing → label `blocked`, comment, exit 0.
- CI red but Tester opened the PR anyway → label `blocked`, comment "CI is
  red — Tester error.", remove `ready-for-review`.
- PR touches files NOT in spec's impact list → `blocked`, ask for
  clarification, exit 0.
