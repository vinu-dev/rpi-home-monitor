<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
---
applyTo: "app/server/**"
---

# Server Instructions

- Keep routes thin and logic in services.
- Preserve auth, CSRF, and session behavior unless the task explicitly changes it.
- Run `pytest app/server/tests/ -v`.
- Update docs if API, security, or deploy behavior changed.
