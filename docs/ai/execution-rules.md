# AI Execution Rules

This file translates roadmap and spec planning into day-to-day rules for AI
coding agents working in this repository.

## Read Order For Feature Work

Before implementing a feature, read in this order:

1. the relevant release plan in `docs/releases/`
2. the feature spec in `docs/specs/`
3. any linked ADRs
4. `docs/ai/working-agreement.md`
5. `docs/ai/engineering-standards.md`

If these disagree, treat ADRs and approved security/product constraints as the
highest-priority source of truth and update the stale planning document.

## Feature Readiness Rule

Do not start implementation from a roadmap bullet alone.

A feature is ready for execution only when it has:

- a release assignment
- a spec under `docs/specs/`
- acceptance criteria
- explicit non-goals
- a likely module/file impact list
- a validation plan

If one of these is missing, stop and fill the planning gap first.

## Implementation Rules

- Keep work feature-scoped and branch-scoped.
- Do not invent user-visible behavior outside the feature spec.
- Prefer existing services, routes, templates, and lifecycle patterns over new
  subsystems.
- Keep routes thin and business rules in services.
- Preserve the current server/camera responsibility split.
- Keep the product local-first; do not introduce cloud coupling by default.
- Assume no public internet by default; if remote access is part of the feature,
  design it around Tailscale to the local product rather than vendor-managed
  cloud delivery.
- Do not create a second source of truth for event state, auth state, or device
  state if an existing one can be extended.

## Sensitive-Area Rules

Escalate or require explicit review before changing:

- authentication or recovery flows
- camera/server trust boundaries
- OTA/update workflow
- pairing or key material handling
- retention / deletion semantics
- anything that alters the meaning of an event in a user-visible way

## Done Means More Than Code

A feature is not done until:

- code is complete
- tests prove the intended behavior
- user-facing docs are updated
- planning docs stay consistent with shipped behavior
- verification notes exist for any browser/device flow that matters in practice

## Issue Structure Rule

Prefer linked issues with clear ownership:

- parent feature issue
- backend/API issue
- frontend/UI issue
- verification/docs issue

If a feature needs a major architecture decision, create or update an ADR
instead of hiding the decision inside code review comments.

## Recovery And Security Rule

Never reintroduce any CLI, SSH, or pre-auth recovery mechanism that resets the
sole admin account. That direction is closed by product decision and documented
in `docs/admin-recovery.md`, `docs/adr/0022-no-backdoors.md`, and
`docs/exec-plans/auth-recovery.md`.
