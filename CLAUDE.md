<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
# Claude Code Adapter

This repository uses a canonical AI operating system under `docs/ai/`.
Treat that directory and the technical docs under `docs/` as the source
of truth.

Required reading order:
1. `AGENTS.md`
2. `docs/ai/index.md`
3. `docs/ai/core-principles.md`
4. `docs/ai/design-standards.md`
5. `docs/ai/workflow-and-validation.md`
6. `docs/ai/task-routing.md`

Claude-specific notes:
- Use project settings from `.claude/settings.json`.
- Use subagents in `.claude/agents/` when they fit the task.
- For large tasks, create or follow an exec plan from `docs/exec-plans/`.

This file is intentionally short. Do not duplicate policy here.
