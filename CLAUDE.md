<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
# Claude Adapter

This file is the Claude-specific entrypoint for the repository.
The canonical, tool-neutral source of truth lives in
[`docs/ai/index.md`](docs/ai/index.md), with navigation metadata in
[`docs/doc-map.yml`](docs/doc-map.yml).

Start here:
1. [`docs/README.md`](docs/README.md)
2. [`docs/doc-map.yml`](docs/doc-map.yml)
3. [`docs/ai/index.md`](docs/ai/index.md)
4. [`docs/ai/mission-and-goals.md`](docs/ai/mission-and-goals.md)
5. [`docs/ai/repo-map.md`](docs/ai/repo-map.md)
6. [`docs/ai/execution-rules.md`](docs/ai/execution-rules.md)
7. [`docs/ai/medical-traceability.md`](docs/ai/medical-traceability.md)
8. [`docs/ai/validation-and-release.md`](docs/ai/validation-and-release.md)

Claude-specific notes:
- respect [`.claude/settings.json`](.claude/settings.json)
- do not loosen sensitive-file denies without explicit security review
- use subagents in [`.claude/agents/`](.claude/agents/) for larger tasks
- use `docs/doc-map.yml` to avoid treating archived/history docs as current truth
- use `/memory` to inspect loaded instructions when behavior seems inconsistent
- keep `CLAUDE.md` concise because it is loaded as project memory
- keep this file as an adapter, not the full handbook
- if this file and `docs/ai/` disagree, `docs/ai/` wins
