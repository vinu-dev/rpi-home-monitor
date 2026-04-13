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
