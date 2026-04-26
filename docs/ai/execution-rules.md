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

## Security Posture Rule — never propose weakening security

**Rule:** Never propose weakening security as a workaround or convenience. If
a workflow requires bypassing or weakening signing, auth, hardening, or any
other security control, **refuse and propose a secure alternative**. Don't
soft-pedal it as "if you want to..." — call it what it is and don't offer it
as an option.

Examples of things to never propose:

- Injecting authorized_keys / passwords into prod images for "convenience"
- Disabling signing enforcement to install unsigned bundles
- Adding `debug-tweaks` to prod (or any equivalent broadens-attack-surface
  feature)
- Suggesting `--privileged` containers as a shortcut
- Proposing "just SSH in" instead of fixing the proper user-facing path
- Bypassing CSRF, mTLS, or signature checks "for testing"
- Adding open ports, removing firewall rules, disabling SELinux/AppArmor
- Anything that makes prod look like dev for ergonomic reasons

Instead, always:

- Make the secure path THE path.
- If the secure path is too painful, fix the path, not the security.
- Surface that the user wants to do something insecure, refuse, propose the
  secure approach.
- Treat security regressions in proposals the same as other regressions —
  block them.

This rule applies whether or not the user explicitly asked for the insecure
shortcut. "User asked me to" is not a defence. Propose the secure path; if
the user still wants the insecure one, escalate visibly (commit message,
ADR, or a refusal back to the user) rather than land it quietly.
