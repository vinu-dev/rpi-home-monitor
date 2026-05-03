---
title: History Index
status: historical
audience: [human, ai]
owner: engineering
source_of_truth: false
---

# History

This folder preserves design history. It is useful context, but it is not the
first source of truth for current behavior. Start with [`../README.md`](../README.md)
and [`../doc-map.yml`](../doc-map.yml), then use these records when you need
the reasoning behind a decision.

## Sections

| Section | Contents |
|---|---|
| [`adr/`](adr/) | Architecture decision records |
| [`specs/`](specs/) | Feature specs and release-one planning specs |
| [`releases/`](releases/) | Historical release plans |
| [`baseline/`](baseline/) | Historical baseline architecture and requirements |
| [`planning/`](planning/) | Roadmaps and planning notes |

Current planning handoffs:

- [`planning/agentry-smoke-test-state.md`](planning/agentry-smoke-test-state.md)
  records the active Agentry integration smoke-test state.

## Editing Rules

- Do not edit history to make current behavior true.
- Update current controlled records first, then add a note here only when the
  historical context itself needs clarification.
- If a historical note becomes actively actionable again, create or update an
  exec plan in [`../exec-plans/`](../exec-plans/).
