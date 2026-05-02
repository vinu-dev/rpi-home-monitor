<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
---
applyTo: "docs/**,README.md,CHANGELOG.md,AGENTS.md,CLAUDE.md"
---

# Docs Instructions

- `docs/ai/` is canonical for agent behavior.
- Keep adapters short and linked back to canonical docs.
- Do not duplicate long policy text across tool-specific files.
- Run the repo AI validator after edits.
- Run `python tools/traceability/check_traceability.py` after traceability-affecting edits.
- Keep README, changelog, and runbooks aligned with live product behavior.
- Use `docs/doc-map.yml` to route docs into current records, guides, history, or archive.
