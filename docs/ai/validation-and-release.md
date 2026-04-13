# Validation And Release

## Validation Matrix

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check app/`, `ruff format --check app/` |
| Camera Python | `pytest app/camera/tests/ -v`, `ruff check app/`, `ruff format --check app/` |
| API contract | relevant contract tests |
| Security-sensitive path | full relevant suite + smoke |
| Yocto config or recipe | `bitbake -p` and VM build for affected image |
| Hardware behavior | deploy and `scripts/smoke-test.sh` |
| Docs or adapters | repo AI validator |

## Release And Deploy Expectations

- Use branches and PRs.
- Record deployment impact in PRs.
- Run live smoke verification after hardware deploys.
- Treat the smoke script and deploy runbook as code.

## Hardware Reality Rule

If code, docs, and device disagree, the device wins until the repo is updated.

## Required Artifacts For Strong PRs

- concise goal
- change summary
- test plan
- deployment impact
- doc impact

## Branch Protection Recommendation

Use GitHub protection for `main`:

- require PRs
- require status checks
- require linear or merge-only policy, whichever the team prefers
- prevent force pushes

These should be enforced in GitHub settings, not just documented.
