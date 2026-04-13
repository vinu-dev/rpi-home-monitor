# Mission And Goals

## Product Mission

Build a trustworthy, self-hosted home monitoring system that feels like a real
product, not a prototype. The system should be understandable, maintainable,
and safe for open-source contributors and production-minded operators.

## Goal-First Execution

Every substantial task should be framed in terms of:

- target user or operator outcome
- success criteria
- constraints
- validation plan
- deployment impact

If the user gives an implementation detail without the outcome, infer the goal
from the product context and state the assumption in the final handoff.

## Success Criteria Template

Use this mental checklist:

- What user or operator problem is solved?
- What behavior must be true when the change is done?
- How will we verify it locally?
- How will we verify it on hardware or in production-like conditions?
- What docs, runbooks, or automation must change with it?

## Large Task Rule

For large, risky, or multi-step work, create an execution plan from
[`../exec-plans/template.md`](../exec-plans/template.md) before coding.

## Anti-Goals

- code churn without product movement
- prompt-shaped code that ignores repo architecture
- passing local tests while drifting from hardware reality
- design regressions justified as "good enough for now"
- undocumented workflow exceptions
