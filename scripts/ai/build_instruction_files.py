"""Generate tool-specific instruction adapters from the canonical repo policy.

The canonical human-readable source of truth lives in docs/ai/ and docs/.
This script generates concise tool adapters that point back to those docs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]
AUTOGEN = (
    "<!-- AUTO-GENERATED FILE. DO NOT EDIT DIRECTLY. "
    "Run `python scripts/ai/build_instruction_files.py`. -->"
)


def render_agents() -> str:
    return dedent(
        f"""\
        {AUTOGEN}
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
        """
    )


def render_claude() -> str:
    return dedent(
        f"""\
        {AUTOGEN}
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
        """
    )


def render_copilot() -> str:
    return dedent(
        f"""\
        {AUTOGEN}
        # GitHub Copilot Repository Instructions

        Use `AGENTS.md` and `docs/ai/index.md` as the entrypoint to this repo.

        Non-negotiable expectations:
        - State the goal before implementing substantial work.
        - Follow `docs/ai/task-routing.md` for validation by subsystem.
        - Keep behavior, docs, and validation in sync.
        - Prefer enforceable workflow over ad-hoc conventions.

        Technical references:
        - `docs/architecture.md`
        - `docs/development-guide.md`
        - `docs/testing-guide.md`
        - `docs/build-setup.md`
        - `docs/hardware-setup.md`

        Large tasks should use `docs/exec-plans/template.md`.
        """
    )


def render_gh_instruction(title: str, apply_to: str, body: str) -> str:
    return dedent(
        f"""\
        {AUTOGEN}
        ---
        applyTo: "{apply_to}"
        ---

        # {title}

        {body}
        """
    )


def render_cursor_rule(
    description: str,
    globs: list[str],
    always_apply: bool,
    body: str,
) -> str:
    globs_yaml = "\n".join(f"  - {glob}" for glob in globs)
    return dedent(
        f"""\
        {AUTOGEN}
        ---
        description: {description}
        globs:
        {globs_yaml}
        alwaysApply: {"true" if always_apply else "false"}
        ---

        {body}
        """
    )


def render_qodo_workflow(name: str, description: str, instructions: str) -> str:
    return dedent(
        f'''\
        # AUTO-GENERATED FILE. Run `python scripts/ai/build_instruction_files.py`.
        name = "{name}"
        description = "{description}"
        instructions = """
        {instructions}
        """
        '''
    )


def generated_files() -> dict[str, str]:
    return {
        "AGENTS.md": render_agents(),
        "CLAUDE.md": render_claude(),
        ".github/copilot-instructions.md": render_copilot(),
        ".github/instructions/server.instructions.md": render_gh_instruction(
            "Server App Instructions",
            "app/server/**",
            dedent(
                """\
                Work through the service layer. Keep Flask routes thin. Run the
                server test and lint commands from `docs/ai/task-routing.md`.
                For API behavior changes, update the relevant docs and contract
                coverage. Read `docs/architecture.md` and `docs/testing-guide.md`
                before changing auth, sessions, TLS, storage, or API contracts.
                """
            ),
        ),
        ".github/instructions/camera.instructions.md": render_gh_instruction(
            "Camera App Instructions",
            "app/camera/**",
            dedent(
                """\
                Preserve the camera lifecycle model, status-server behavior, and
                hardware-aware platform abstractions. Treat mDNS, TLS, pairing,
                and provisioning as product behavior, not just implementation
                details. Run the camera validation commands from
                `docs/ai/task-routing.md`.
                """
            ),
        ),
        ".github/instructions/yocto.instructions.md": render_gh_instruction(
            "Yocto Instructions",
            "meta-home-monitor/**,config/**,scripts/build*.sh",
            dedent(
                """\
                Keep permanent product policy out of developer `local.conf`.
                Extend layers cleanly and document the impact. Build on the VM.
                Run at least `bitbake -p` and document any build or deploy
                implications in the PR.
                """
            ),
        ),
        ".github/instructions/docs.instructions.md": render_gh_instruction(
            "Documentation Instructions",
            "docs/**,README.md,CHANGELOG.md",
            dedent(
                """\
                Update canonical docs when behavior or workflow changes. Avoid
                duplicating long policy in multiple places. For AI guidance,
                canonical content lives in `docs/ai/`; generated adapters should
                be regenerated, not hand-edited.
                """
            ),
        ),
        ".cursor/rules/00-repo-overview.mdc": render_cursor_rule(
            "Repo overview and reading order",
            ["**/*"],
            True,
            dedent(
                """\
                Start at `AGENTS.md`, then `docs/ai/index.md`.

                Operate goal-first:
                - state the product or engineering goal
                - identify constraints
                - define exit criteria

                Use `docs/ai/task-routing.md` to decide which commands and docs
                apply to the current change.
                """
            ),
        ),
        ".cursor/rules/10-design-standards.mdc": render_cursor_rule(
            "Design and product quality standards",
            ["app/**", "docs/**", "templates/**"],
            False,
            dedent(
                """\
                The repository is a product sample, not just a code sample.

                Avoid generic AI output. Preserve explicit architecture patterns,
                deliberate UX, and maintainable module boundaries. Read
                `docs/ai/design-standards.md` before changing user-facing flows,
                architecture, or deployment behavior.
                """
            ),
        ),
        ".cursor/rules/20-server-python.mdc": render_cursor_rule(
            "Server Python rules",
            ["app/server/**"],
            False,
            dedent(
                """\
                Keep routes thin and business logic in services. Validate with:
                `ruff check app/ && ruff format --check app/ && pytest app/server/tests/ -v`
                """
            ),
        ),
        ".cursor/rules/30-camera-python.mdc": render_cursor_rule(
            "Camera Python rules",
            ["app/camera/**"],
            False,
            dedent(
                """\
                Preserve lifecycle, pairing, TLS, provisioning, and status-server
                semantics. Validate with:
                `ruff check app/ && ruff format --check app/ && pytest app/camera/tests/ -v`
                """
            ),
        ),
        ".cursor/rules/40-yocto.mdc": render_cursor_rule(
            "Yocto and image-policy rules",
            ["meta-home-monitor/**", "config/**", "scripts/build*.sh"],
            False,
            dedent(
                """\
                Keep permanent policy out of developer overrides. Use recipes,
                machine config, image config, and packagegroups. Validate with
                `bitbake -p` and document build impact.
                """
            ),
        ),
        ".cursor/rules/50-validation.mdc": render_cursor_rule(
            "Validation and rollout rules",
            ["**/*"],
            False,
            dedent(
                """\
                If behavior changes, update docs and validation in the same
                change. Large tasks should use `docs/exec-plans/template.md`.
                Hardware-affecting changes should include smoke evidence when the
                hardware is available.
                """
            ),
        ),
        ".qodo/workflows/implement.toml": render_qodo_workflow(
            "implement",
            "Implement a repository change while following the canonical AI policy.",
            dedent(
                """\
                Start at AGENTS.md, then docs/ai/index.md.
                State the goal, constraints, and exit criteria.
                Follow docs/ai/task-routing.md for validation.
                If the task is cross-cutting or risky, use docs/exec-plans/template.md.
                Do not hand-edit generated adapter files.
                """
            ).strip(),
        ),
        ".qodo/workflows/review.toml": render_qodo_workflow(
            "review",
            "Review a change for correctness, regressions, and missing validation.",
            dedent(
                """\
                Review for bugs, design regressions, stale docs, missing tests,
                and workflow drift. Treat docs and deploy paths as product
                artifacts. Prefer concrete findings with file references and
                missing validation evidence.
                """
            ).strip(),
        ),
    }


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.replace("\r\n", "\n").rstrip() + "\n"
    path.write_text(normalized, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate only")
    args = parser.parse_args()

    failures: list[str] = []
    for relative_path, content in generated_files().items():
        destination = ROOT / relative_path
        normalized = content.replace("\r\n", "\n").rstrip() + "\n"
        if args.check:
            if not destination.exists():
                failures.append(f"Missing generated file: {relative_path}")
                continue
            existing = destination.read_text(encoding="utf-8")
            if existing != normalized:
                failures.append(f"Out-of-date generated file: {relative_path}")
        else:
            write_file(destination, normalized)

    if failures:
        for failure in failures:
            print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
