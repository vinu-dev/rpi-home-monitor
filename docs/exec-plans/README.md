---
title: Active Execution Plans
status: active
audience: [human, ai]
owner: engineering
source_of_truth: false
---

# Active Execution Plans

This folder is for active, resumable implementation plans. Completed plans and
field records belong in [`../archive/exec-plans/`](../archive/exec-plans/).

Use [`template.md`](template.md) for new plans.

## Active Plans

| Plan | Purpose |
|---|---|
| [`hardware-lab-rollout.md`](hardware-lab-rollout.md) | Hardware lab rollout and validation |
| [`luks-post-pair-migration.md`](luks-post-pair-migration.md) | LUKS migration after pairing |
| [`motion-mode-pre-roll.md`](motion-mode-pre-roll.md) | Motion-mode pre-roll delivery |
| [`on-demand-streaming.md`](on-demand-streaming.md) | Viewer-driven streaming |
| [`ota-rollout-and-validation.md`](ota-rollout-and-validation.md) | OTA rollout validation |

## Editing Rules

- Keep only active plans here.
- Move finished plans to [`../archive/exec-plans/`](../archive/exec-plans/).
- Keep plan references aligned with current requirements, architecture, risk,
  security, and tests before implementation PRs merge.
