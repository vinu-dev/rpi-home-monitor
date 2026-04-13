# AI Operating System

This directory is the canonical source of truth for AI-agent behavior in this
repository. Tool-specific entrypoints such as `AGENTS.md`, `CLAUDE.md`,
`.github/copilot-instructions.md`, and `.cursor/rules/` should stay short and
point back here instead of duplicating the whole handbook.

## Objectives

- Make this repository a strong default environment for AI-led product work.
- Keep agent behavior goal-driven, test-driven, and design-aware.
- Support multiple coding tools without forking the repo's standards.
- Move important rules from tribal knowledge into versioned, reviewable files.
- Enforce critical rules in automation wherever practical.

## Governance Surface

- `docs/ai/` is the canonical policy layer.
- `scripts/ai/build_instruction_files.py` generates tool adapters from that
  layer.
- `scripts/ai/validate_repo_ai_setup.py` checks adapter freshness and repo
  shape.
- CI and pre-commit must enforce the important rules, not just document them.

## Read In Order

1. [`mission-and-goals.md`](mission-and-goals.md)
2. [`repo-map.md`](repo-map.md)
3. [`working-agreement.md`](working-agreement.md)
4. [`engineering-standards.md`](engineering-standards.md)
5. [`design-standards.md`](design-standards.md)
6. [`validation-and-release.md`](validation-and-release.md)

## Existing System References

- [`../architecture.md`](../architecture.md)
- [`../development-guide.md`](../development-guide.md)
- [`../testing-guide.md`](../testing-guide.md)
- [`../build-setup.md`](../build-setup.md)
- [`../hardware-setup.md`](../hardware-setup.md)
- [`../requirements.md`](../requirements.md)
- [`../adr/`](../adr/)

## Adapter Rule

- `docs/ai/` is canonical.
- Root and tool-specific instruction files are adapters.
- Adapters should fit the tool, not redefine the repo.

## Design Rule

Every implementation should improve the product toward a clear user-facing or
operator-facing goal. Agents should not optimize for local code motion alone.

## Validation

Validate this instruction surface with:

```bash
python scripts/ai/validate_repo_ai_setup.py
python scripts/ai/check_doc_links.py
python scripts/ai/check_shell_scripts.py
pre-commit run --all-files
```
