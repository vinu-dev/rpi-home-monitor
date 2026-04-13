<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
---
applyTo: "app/camera/**"
---

# Camera Instructions

- Preserve the lifecycle state machine and platform abstraction.
- Keep the post-provisioning status UI aligned with live HTTPS behavior.
- Run `pytest app/camera/tests/ -v`.
- Run `ruff check .` and `ruff format --check .` before handoff.
- Validate on hardware for pairing, WiFi, hostname, TLS, or streaming changes.
