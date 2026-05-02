# Working Agreement

## Scope Discipline

- One concern per branch and PR.
- Avoid drive-by refactors unless they are required to complete the goal.
- If you discover unrelated defects, note them separately instead of folding
  them into the current task.

## Source Of Truth Discipline

- Put canonical policy in `docs/ai/` and the deeper docs it references.
- Keep tool adapters short.
- Update docs, scripts, and templates together when workflow behavior changes.
- Avoid conflicting instructions across `AGENTS.md`, `CLAUDE.md`, Copilot,
  Cursor, Qodo, local memories, and generated adapters. If two files disagree,
  update the canonical source and regenerate adapters.

## Task Context Contract

AI agents should start implementation work with four things clear:

- goal: the product, operator, safety, security, or engineering outcome
- context: the relevant files, docs, issues, logs, examples, or screenshots
- constraints: architecture, traceability, security, hardware, release, and
  validation rules
- done when: tests, checks, behavior, docs, PR, CI, or deployment evidence that
  must be true before handoff

If any of these are missing but the safe path is obvious, make reasonable
assumptions, record them, and keep going. Ask for alignment only when the
missing detail creates a real safety, security, product, or release tradeoff.

## Context Hygiene

- Load the smallest useful set of files first, then expand deliberately.
- Use `docs/doc-map.yml` and folder `README.md` files to avoid treating archived
  or historical material as current truth.
- Keep permanent agent rules concise because many tools load them into every
  session.
- Put detailed examples, long investigation notes, and one-off findings in
  exec plans, quality records, specs, or archive records instead of bloating
  tool entrypoints.
- When a tool has a way to inspect loaded memory or rules, use it to debug
  instruction conflicts before editing more policy.

## Default Expectations

- branch from `main`
- validate before commit
- explain deployment impact in the PR
- preserve design intent, not just functional behavior
- treat hardware verification as first-class when the product touches devices
- keep multi-step work resumable inside the repo, not just in chat history

## Resumption Discipline

- Any task likely to span multiple sessions, hardware steps, or long validations
  must have a living exec plan under `docs/exec-plans/`.
- The exec plan must include:
  - current goal
  - exact current status
  - completed steps
  - next step
  - validation already run
  - commands, devices, branches, PRs, and artifacts needed to resume
  - known blockers or risks
- Update the exec plan before stopping when:
  - a session limit is approaching
  - hardware work is paused
  - CI is still running
  - a task is handed off to another agent or another day
- Do not leave critical progress only in terminal state or chat context.
- If a task has no durable resumption artifact, it is not in a healthy stopping state.

## When To Escalate

Ask for alignment only when there is a meaningful tradeoff:

- breaking UX or API behavior
- security posture changes
- destructive operations or data migration
- release-risky workflow changes
- major architecture changes

## What Good Output Looks Like

- clear goal statement
- small coherent change set
- tests that prove the behavior
- docs that match the new reality
- no hidden manual steps

## What Bad Output Looks Like

- adding rules without enforcement
- giant monolithic instruction files
- fixing symptoms instead of root causes
- UI or architecture drift justified by convenience
- deploy docs that do not work on real hardware
