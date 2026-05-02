#!/usr/bin/env python3
# REQ: SWR-055; RISK: RISK-009; SEC: SC-009; TEST: TC-020, TC-045
"""Build generated tool adapters from the canonical AI docs."""

from __future__ import annotations

import argparse
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]
AUTO = (
    "<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. "
    "Run `python scripts/ai/build_instruction_files.py`. -->"
)


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip() + "\n"


def render_agents() -> str:
    return dedent(
        f"""\
        {AUTO}
        # Agent Operating System

        This repository is designed to be a gold-standard workspace for
        agentic product development. This file is the short, tool-neutral
        entrypoint for any coding agent.

        Canonical source of truth:
        - [`docs/README.md`](docs/README.md)
        - [`docs/doc-map.yml`](docs/doc-map.yml)
        - [`docs/ai/index.md`](docs/ai/index.md)

        Read next:
        - [`docs/README.md`](docs/README.md)
        - [`docs/ai/mission-and-goals.md`](docs/ai/mission-and-goals.md)
        - [`docs/ai/repo-map.md`](docs/ai/repo-map.md)
        - [`docs/ai/working-agreement.md`](docs/ai/working-agreement.md)
        - [`docs/ai/engineering-standards.md`](docs/ai/engineering-standards.md)
        - [`docs/ai/execution-rules.md`](docs/ai/execution-rules.md)
        - [`docs/ai/medical-traceability.md`](docs/ai/medical-traceability.md)
        - [`docs/ai/design-standards.md`](docs/ai/design-standards.md)
        - [`docs/ai/validation-and-release.md`](docs/ai/validation-and-release.md)
        - [`docs/exec-plans/template.md`](docs/exec-plans/template.md)

        Core rules:
        - work from an explicit product or operator goal
        - prefer design-level fixes over local patches
        - keep tool adapters short and keep canonical policy in `docs/ai/`
        - maintain requirements, risk, security, test, and code traceability
        - run the right validation for the area you touched
        - do not commit directly to `main`

        Key validation:
        - repo governance: `python tools/docs/check_doc_map.py`, `python scripts/ai/validate_repo_ai_setup.py`, `pre-commit run --all-files`
        - server: `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .`
        - camera: `pytest app/camera/tests/ -v`, `ruff check .`, `ruff format --check .`
        - Yocto: `bitbake -p` and VM build for affected images
        - hardware deploys: `bash scripts/smoke-test.sh <server-ip> <password> [camera-ip] [camera-password]`

        Tool adapters:
        - `CLAUDE.md`
        - `.github/copilot-instructions.md`
        - `.github/instructions/*.instructions.md`
        - `.cursor/rules/*.mdc`
        - `.qodo/workflows/*.toml`
        """
    )


def render_claude() -> str:
    return dedent(
        f"""\
        {AUTO}
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
        - use subagents in [`.claude/agents/`](.claude/agents/) for larger tasks
        - use `docs/doc-map.yml` to avoid treating archived/history docs as current truth
        - keep this file as an adapter, not the full handbook
        - if this file and `docs/ai/` disagree, `docs/ai/` wins
        """
    )


def render_copilot() -> str:
    return dedent(
        f"""\
        {AUTO}
        # GitHub Copilot Repository Instructions

        Read [`AGENTS.md`](../AGENTS.md) first.

        Core rules:
        - start from [`docs/README.md`](../docs/README.md) and [`docs/doc-map.yml`](../docs/doc-map.yml)
        - follow [`docs/ai/index.md`](../docs/ai/index.md)
        - keep changes scoped and update docs when behavior changes
        - use the correct validation for the area you touched
        - maintain traceability for meaningful changes
        - do not commit directly to `main`
        - preserve the existing repo architecture

        Path-specific instructions live under [`.github/instructions/`](./instructions/).
        """
    )


def render_instruction(title: str, apply_to: str, body: str) -> str:
    return f'{AUTO}\n---\napplyTo: "{apply_to}"\n---\n\n# {title}\n\n{body}\n'


def render_cursor(
    description: str, globs: list[str], body: str, always: bool = False
) -> str:
    glob_lines = "\n".join(f"  - {item}" for item in globs)
    always_line = "true" if always else "false"
    return (
        f"{AUTO}\n"
        "---\n"
        f"description: {description}\n"
        "globs:\n"
        f"{glob_lines}\n"
        f"alwaysApply: {always_line}\n"
        "---\n\n"
        f"{body}\n"
    )


def render_qodo(name: str, description: str, prompt: str) -> str:
    return (
        "# AUTO-GENERATED FILE. Run `python scripts/ai/build_instruction_files.py`.\n"
        f'name = "{name}"\n'
        f'description = "{description}"\n'
        'prompt = """\n'
        f"{prompt}\n"
        '"""\n'
    )


def generated_files() -> dict[str, str]:
    return {
        "AGENTS.md": render_agents(),
        "CLAUDE.md": render_claude(),
        ".github/copilot-instructions.md": render_copilot(),
        ".github/instructions/server.instructions.md": render_instruction(
            "Server Instructions",
            "app/server/**",
            "- Keep routes thin and logic in services.\n"
            "- Preserve auth, CSRF, and session behavior unless the task explicitly changes it.\n"
            "- Run `pytest app/server/tests/ -v`.\n"
            "- Run `ruff check .` and `ruff format --check .` before handoff.\n"
            "- Update docs if API, security, or deploy behavior changed.",
        ),
        ".github/instructions/camera.instructions.md": render_instruction(
            "Camera Instructions",
            "app/camera/**",
            "- Preserve the lifecycle state machine and platform abstraction.\n"
            "- Keep the post-provisioning status UI aligned with live HTTPS behavior.\n"
            "- Run `pytest app/camera/tests/ -v`.\n"
            "- Run `ruff check .` and `ruff format --check .` before handoff.\n"
            "- Validate on hardware for pairing, WiFi, hostname, TLS, or streaming changes.",
        ),
        ".github/instructions/yocto.instructions.md": render_instruction(
            "Yocto Instructions",
            "meta-home-monitor/**,config/**",
            "- Do not put permanent project policy in `local.conf`.\n"
            "- Keep machine policy in machine config, distro policy in distro config, and packaging in recipes.\n"
            "- Run `bitbake -p` for affected images.\n"
            "- Update release docs when build inputs or artifact paths change.\n"
            "- Use the build VM for real Yocto builds.",
        ),
        ".github/instructions/docs.instructions.md": render_instruction(
            "Docs Instructions",
            "docs/**,README.md,CHANGELOG.md,AGENTS.md,CLAUDE.md",
            "- `docs/ai/` is canonical for agent behavior.\n"
            "- Keep adapters short and linked back to canonical docs.\n"
            "- Do not duplicate long policy text across tool-specific files.\n"
            "- Run the repo AI validator after edits.\n"
            "- Run `python tools/traceability/check_traceability.py` after traceability-affecting edits.\n"
            "- Keep README, changelog, and runbooks aligned with live product behavior.\n"
            "- Use `docs/doc-map.yml` to route docs into current records, guides, history, or archive.",
        ),
        ".cursor/rules/00-repo-overview.mdc": render_cursor(
            "Repo-wide overview and source-of-truth rules.",
            ["**/*"],
            "# Repo Overview\n\n"
            "- Read `AGENTS.md` first.\n"
            "- Start docs navigation at `docs/README.md` and `docs/doc-map.yml`.\n"
            "- Treat `docs/ai/` as the canonical AI operating system.\n"
            "- Keep tool adapters short and linked back to `docs/ai/`.\n"
            "- Work toward a clear product goal and success criteria.\n"
            "- Do not commit directly to `main`.\n",
            always=True,
        ),
        ".cursor/rules/10-goals-and-plans.mdc": render_cursor(
            "Goal framing and large-task planning rules.",
            ["**/*"],
            "# Goals And Plans\n\n"
            "- Start from the user or operator outcome, not just the local code change.\n"
            "- For larger or riskier work, use `docs/exec-plans/template.md`.\n"
            "- Treat `docs/history/` and `docs/archive/` as context, not current source of truth.\n"
            "- Prefer design-level fixes over symptom patches.\n"
            "- Note assumptions when the requested goal is underspecified.\n",
            always=True,
        ),
        ".cursor/rules/20-server-python.mdc": render_cursor(
            "Server-side Flask and service-layer rules.",
            ["app/server/**"],
            "# Server Rules\n\n"
            "- Keep routes thin and business logic in services.\n"
            "- Preserve app-factory structure and constructor injection.\n"
            "- Validate with `pytest app/server/tests/ -v`.\n"
            "- Update docs if API, auth, or runtime behavior changes.\n",
        ),
        ".cursor/rules/30-camera-python.mdc": render_cursor(
            "Camera runtime, pairing, hostname, and HTTPS status rules.",
            ["app/camera/**"],
            "# Camera Rules\n\n"
            "- Preserve the lifecycle state machine and platform abstraction.\n"
            "- Validate camera HTTPS, status, pairing, and hostname behavior on hardware for relevant changes.\n"
            "- Validate with `pytest app/camera/tests/ -v`.\n"
            "- Keep the post-provisioning URL and live runtime behavior aligned.\n",
        ),
        ".cursor/rules/40-yocto.mdc": render_cursor(
            "Yocto and release guardrails.",
            ["meta-home-monitor/**", "config/**", "scripts/build*.sh"],
            "# Yocto And Release Rules\n\n"
            "- No permanent project policy in `local.conf`.\n"
            "- Put machine policy in machine config and distro policy in distro config.\n"
            "- Run `bitbake -p` for affected images.\n"
            "- Build on the VM for real Yocto validation.\n",
        ),
        ".cursor/rules/50-validation.mdc": render_cursor(
            "Validation rules for tests, smoke, and deployment.",
            ["**/*"],
            "# Validation Rules\n\n"
            "- Run the right validation for the area you changed.\n"
            "- Governance changes must pass the repo validator and pre-commit suite.\n"
            "- Hardware-affecting changes require smoke verification.\n"
            "- Treat smoke scripts and deploy runbooks as code, not comments.\n"
            "- If device behavior and docs disagree, update the repo to match reality.\n",
            always=True,
        ),
        ".cursor/rules/60-design-standards.mdc": render_cursor(
            "Product and UI design standards.",
            ["app/**", "docs/**"],
            "# Design Standards\n\n"
            "- Optimize for clarity, confidence, and truthful status communication.\n"
            "- Support mobile-first usage.\n"
            "- Always provide clear loading, empty, and error states.\n"
            "- Avoid generic AI-looking layouts or undocumented UX shortcuts.\n",
        ),
        ".qodo/workflows/implement.toml": render_qodo(
            "implement",
            "Implement a repository change while following the canonical AI policy.",
            "Start at AGENTS.md, then docs/ai/index.md.\n"
            "State the goal, constraints, and exit criteria.\n"
            "Follow docs/ai/repo-map.md and docs/ai/validation-and-release.md.\n"
            "If the task is cross-cutting or risky, use docs/exec-plans/template.md.",
        ),
        ".qodo/workflows/review.toml": render_qodo(
            "review",
            "Review a change for correctness, regressions, and missing validation.",
            "Review for bugs, design regressions, stale docs, missing tests, and workflow drift.\n"
            "Treat docs and deploy paths as product artifacts.\n"
            "Prefer concrete findings with file references and missing validation evidence.",
        ),
        ".qodo/workflows/server-smoke.toml": render_qodo(
            "server-smoke",
            "Run the repository smoke verification flow for the live server and optional camera.",
            "Review the deployment context, run the repository smoke verification flow,\n"
            "report pass/fail/skip clearly, and call out drift between the repo and live hardware behavior.",
        ),
        ".qodo/workflows/yocto-validate.toml": render_qodo(
            "yocto-validate",
            "Validate Yocto-facing changes with the repository guardrails.",
            "Inspect the Yocto-related change, confirm the policy is in the correct layer,\n"
            "run the required parse checks, and report whether the repo and build workflow still match the docs.",
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Fail if generated files are stale."
    )
    args = parser.parse_args()

    failures: list[str] = []
    for rel_path, content in generated_files().items():
        target = ROOT / rel_path
        expected = normalize(content)
        if args.check:
            if not target.exists():
                failures.append(f"Missing generated file: {rel_path}")
                continue
            actual = normalize(target.read_text(encoding="utf-8"))
            if actual != expected:
                failures.append(f"Out-of-date generated file: {rel_path}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(expected, encoding="utf-8", newline="\n")

    if failures:
        for failure in failures:
            print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
