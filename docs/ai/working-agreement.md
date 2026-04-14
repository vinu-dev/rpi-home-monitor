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
