# Yocto Guardrails

## When to use

Use this skill for recipe, distro, image, or machine-policy changes.

## Checklist

1. Confirm whether the change belongs in recipe, distro, machine, or config.
2. Avoid permanent policy in `local.conf`.
3. Run `bitbake -p`.
4. Build on the VM for affected images when practical.
5. Update docs if output paths, build commands, or deploy workflow changed.
