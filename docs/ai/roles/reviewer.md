# Reviewer — project-specific guidance for `vinu-dev/rpi-home-monitor`

This file describes WHAT to look for when reviewing a PR in this repo. The
HOW (picking PRs, checking rebase status, approve vs block, label
transitions) lives in agentry's bundled prompt — see `agentry/config.yml`.

## Branch protection on `main` (already enforced)

- 1 required approving review
- code-owner review required
- dismiss-stale-reviews on
- linear history required
- conversation resolution required
- `enforce_admins: false` (admins can bypass for setup, but agents NEVER
  bypass — agents only approve, they never merge)

## Review checklist

### Spec adherence
- [ ] PR body has `Closes #<id>` linking the issue
- [ ] Diff implements the spec's acceptance criteria
- [ ] Diff respects the spec's non-goals (no scope creep)
- [ ] Files touched match the spec's "module / file impact list" — note
      deviations if any

### Engineering standards (`docs/ai/engineering-standards.md`)
- [ ] Service-layer pattern preserved; routes thin
- [ ] Constructor injection / explicit wiring
- [ ] App-factory + camera-lifecycle preserved
- [ ] Mutable runtime state on `/data`, NOT in source tree
- [ ] No permanent Yocto policy in `local.conf`
- [ ] Behavior changes have doc updates
- [ ] Workflow changes have runbook updates

### Design standards (`docs/ai/design-standards.md`)
- [ ] User-facing flows have primary path + failure states
- [ ] Setup / login / status / update / recovery flows treated as product
- [ ] Existing UI / product language preserved unless intentional change

### Validation evidence
- [ ] PR body lists which validation rows ran + status
- [ ] CI is green (`gh pr checks <n>`)
- [ ] Coverage thresholds met (server 85, camera 80)
- [ ] Tester log (`agentry/logs/issues/<id>-<slug>/tester.log`) shows the
      expected rows ran (no silent skips)

### Sensitive-path scrutiny

`git diff --name-only origin/main...HEAD` matched against:

- `**/auth/**`, `**/secrets/**`
- `**/.github/workflows/**`
- `app/camera/camera_streamer/lifecycle.py`, `wifi.py`, `pairing.py`
- certificate / TLS / OTA flow code
- `docs/cybersecurity/**`, `docs/risk/**`

If diff is non-trivial in a sensitive path AND the PR doesn't carry
`needs-security-review`, label `blocked-security` and request review.

### Traceability (`docs/ai/medical-traceability.md`)
- [ ] Required ID families used (REQ-, ARCH-, RISK-, SEC-, TEST-)
- [ ] Traceability matrix updated where required
- [ ] `python tools/traceability/check_traceability.py` was part of
      validation evidence

### Repo policy compliance
- [ ] No loosened sensitive-file denies in `.claude/settings.json`,
      `.codex/`, `.github/copilot-instructions.md`
- [ ] No new external dependency in sensitive areas without justification
- [ ] Adapter files (CLAUDE.md, AGENTS.md, copilot-instructions) match
      `docs/ai/` (no policy duplication)

## When to block

Any failed checklist item → block. Comment with itemized issues, file/line
references where possible. Use `gh pr review <n> --request-changes`.

## When to flag for human security review

Sensitive-path touch + non-trivial diff + no `needs-security-review` label
already → label `blocked-security`. Don't try to evaluate security
implications yourself; flag for the human.

## Drive-by improvements outside scope

Don't block — note as "future work" comment. Researcher can pick it up
on its next cycle.
