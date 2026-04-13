<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
# Agent Operating System

This repository is designed to be a gold-standard workspace for
agentic product development. This file is the short, tool-neutral
entrypoint for any coding agent.

Canonical source of truth:
- [`docs/ai/index.md`](docs/ai/index.md)

Read next:
- [`docs/ai/mission-and-goals.md`](docs/ai/mission-and-goals.md)
- [`docs/ai/repo-map.md`](docs/ai/repo-map.md)
- [`docs/ai/working-agreement.md`](docs/ai/working-agreement.md)
- [`docs/ai/engineering-standards.md`](docs/ai/engineering-standards.md)
- [`docs/ai/design-standards.md`](docs/ai/design-standards.md)
- [`docs/ai/validation-and-release.md`](docs/ai/validation-and-release.md)
- [`docs/exec-plans/template.md`](docs/exec-plans/template.md)

Core rules:
- work from an explicit product or operator goal
- prefer design-level fixes over local patches
- keep tool adapters short and keep canonical policy in `docs/ai/`
- run the right validation for the area you touched
- do not commit directly to `main`

Key validation:
- server: `pytest app/server/tests/ -v`, `ruff check app/`, `ruff format --check app/`
- camera: `pytest app/camera/tests/ -v`, `ruff check app/`, `ruff format --check app/`
- Yocto: `bitbake -p` and VM build for affected images
- hardware deploys: `bash scripts/smoke-test.sh <server-ip> <password> [camera-ip] [camera-password]`

Tool adapters:
- `CLAUDE.md`
- `.github/copilot-instructions.md`
- `.github/instructions/*.instructions.md`
- `.cursor/rules/*.mdc`
- `.qodo/workflows/*.toml`
