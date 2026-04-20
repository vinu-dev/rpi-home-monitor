# Engineering Standards

## Architecture Standards

- Follow the service-layer pattern already used in the repo.
- Keep routes thin and business logic in services.
- Prefer constructor injection and explicit wiring.
- Preserve the app-factory and camera lifecycle patterns.
- Keep mutable runtime state on `/data`.
- Keep permanent Yocto policy out of `local.conf`.

## Quality Standards

- readable code over clever code
- obvious module boundaries
- minimal surprise for future contributors
- comments only when they add real value
- no hidden runtime assumptions

## Documentation Standards

- behavior changes require doc changes
- workflow changes require runbook changes
- architecture changes require ADR or architecture doc updates
- avoid copying the same rule into many files

## Automation Standards

- if a rule is important, try to enforce it in CI or templates
- prefer scripts over manual checklists when the process is repeatable
- keep operational scripts aligned with real deployed behavior
- keep generated adapter files machine-rebuilt from the canonical source
- treat workflow files and shell scripts as production code

## Design-Level Fix Rule

Good fixes solve the real constraint:

- not "make the test green"
- not "make the deploy pass once"
- not "silence the symptom"

Instead:

- identify the system boundary
- identify the product expectation
- change the smallest correct layer
- validate in the environment that matters

## Security: No Backdoors

This is a **home-security product**. Non-negotiable rules for any
authentication, recovery, or privileged-access feature:

- **No documented command, script, or endpoint that bypasses the primary
  auth mechanism.** If a single command resets the admin password, it
  doesn't matter that it needs `sudo` — it is a backdoor. A backdoor
  anyone with physical access can run is still a backdoor; the threat
  model "physical access = compromise" is a *limit* of our protection,
  not a license to install one on purpose.
- **Pre-auth surfaces never disclose internals.** The login page, HTTP
  error responses, and any other unauthenticated surface must not
  mention recovery commands, script paths, SSH procedures, internal
  URLs, or the names of privileged tools. "Contact your administrator"
  is the most the login screen ever says.
- **Lost-access recovery is a hardware concern.** A sole admin locked
  out of the web UI does a **hardware factory reset** (physical
  button, GPIO pin-short, or SD-card reflash). Nothing shorter. The
  pain is the feature — this is what prevents an attacker with brief
  physical access from walking away with an operational account.
- **Admin-assisted recovery is fine when audited.** Any admin can reset
  any other user's password from Settings → Users; the target is
  force-rotated on next login and both actions hit `/logs`. This is a
  known, session-gated, observable transaction — not a backdoor.
- **When in doubt, refuse.** If a recovery story has a step that reads
  "and then the operator runs X," stop. Find a design that doesn't
  need X, or escalate the requirement to hardware.

A rejected backdoor, even a narrowly-scoped one, is documented in
`docs/exec-plans/*.md` under a "rejected on review" section so the
decision and reasoning survive to the next engineer who has the same
idea. See [`docs/admin-recovery.md`](../admin-recovery.md) and
[`docs/exec-plans/auth-recovery.md`](../exec-plans/auth-recovery.md)
for the working example.
