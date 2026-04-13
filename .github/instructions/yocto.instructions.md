<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. Run `python scripts/ai/build_instruction_files.py`. -->
---
applyTo: "meta-home-monitor/**,config/**"
---

# Yocto Instructions

- Do not put permanent project policy in `local.conf`.
- Keep machine policy in machine config, distro policy in distro config, and packaging in recipes.
- Run `bitbake -p` for affected images.
- Use the build VM for real Yocto builds.
