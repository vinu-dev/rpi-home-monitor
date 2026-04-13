<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
# AGENTS.md

This repository is designed to work well with multiple coding agents.

Start here:
1. `docs/ai/index.md`
2. `docs/ai/core-principles.md`
3. `docs/ai/design-standards.md`
4. `docs/ai/workflow-and-validation.md`
5. `docs/ai/task-routing.md`

Then read the deeper technical docs that match the task:
- `docs/architecture.md`
- `docs/development-guide.md`
- `docs/testing-guide.md`
- `docs/build-setup.md`
- `docs/hardware-setup.md`

Core operating model:
- Goal first: define product goal, constraints, and exit criteria.
- Use the smallest complete change.
- Prefer enforceable process over prose.
- Update docs and validation when behavior changes.
- If a rule is impractical, change the rule and its enforcement. Do not keep fake policy.

Required commands by area:
- Server Python: `ruff check app/ && ruff format --check app/ && pytest app/server/tests/ -v`
- Camera Python: `ruff check app/ && ruff format --check app/ && pytest app/camera/tests/ -v`
- Yocto: `bitbake -p` plus build impact note
- Hardware smoke: `bash scripts/smoke-test.sh <server-ip> <password> [camera-ip] [camera-password]`

Deploy commands live in `docs/ai/task-routing.md`.

Large work:
- Use `docs/exec-plans/template.md` for cross-cutting or high-risk changes.

Tool adapters are generated:
- `CLAUDE.md`
- `.github/copilot-instructions.md`
- `.github/instructions/*.instructions.md`
- `.cursor/rules/*.mdc`
- `.qodo/workflows/*.toml`

Regenerate adapters:
- `python scripts/ai/build_instruction_files.py`

Validate repo governance:
- `python scripts/ai/validate_ai_repo.py`
