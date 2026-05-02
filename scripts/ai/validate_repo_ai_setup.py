# REQ: SWR-055; RISK: RISK-009; SEC: SC-009; TEST: TC-020, TC-045
"""Validate the repository AI operating-system surface."""

from __future__ import annotations

import re

from build_instruction_files import ROOT, generated_files

REQUIRED_CANONICAL = [
    "docs/README.md",
    "docs/doc-map.yml",
    "docs/ai/index.md",
    "docs/ai/mission-and-goals.md",
    "docs/ai/repo-map.md",
    "docs/ai/working-agreement.md",
    "docs/ai/engineering-standards.md",
    "docs/ai/execution-rules.md",
    "docs/ai/design-standards.md",
    "docs/ai/validation-and-release.md",
    "docs/exec-plans/template.md",
    ".claude/settings.json",
    ".claude/agents/reviewer.md",
    ".claude/agents/hardware-smoke.md",
    ".claude/agents/yocto.md",
    ".github/pull_request_template.md",
    ".github/skills/hardware-smoke/SKILL.md",
    ".github/skills/yocto-guardrails/SKILL.md",
    ".gitattributes",
    ".pre-commit-config.yaml",
    ".github/workflows/test.yml",
    "scripts/ai/build_instruction_files.py",
    "scripts/ai/check_doc_links.py",
    "scripts/ai/check_shell_scripts.py",
    "tools/docs/check_doc_map.py",
]

EXPECTED_CURSOR = {
    ".cursor/rules/00-repo-overview.mdc",
    ".cursor/rules/10-goals-and-plans.mdc",
    ".cursor/rules/20-server-python.mdc",
    ".cursor/rules/30-camera-python.mdc",
    ".cursor/rules/40-yocto.mdc",
    ".cursor/rules/50-validation.mdc",
    ".cursor/rules/60-design-standards.mdc",
}

EXPECTED_QODO = {
    ".qodo/workflows/implement.toml",
    ".qodo/workflows/review.toml",
    ".qodo/workflows/server-smoke.toml",
    ".qodo/workflows/yocto-validate.toml",
}

REQUIRED_BACKLINK = "docs/ai/index.md"
ADAPTERS_WITH_BACKLINKS = {
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
}
CI_REQUIRED_PATHS = {
    "app/**",
    "meta-home-monitor/**",
    "config/**",
    "swupdate/**",
    "docs/**",
    ".github/**",
    ".claude/**",
    ".cursor/**",
    ".qodo/**",
    "scripts/**",
    "README.md",
    "CHANGELOG.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".pre-commit-config.yaml",
    ".gitattributes",
    "pyproject.toml",
}
CI_REQUIRED_SNIPPETS = {
    "python tools/docs/check_doc_map.py",
    "python scripts/ai/validate_repo_ai_setup.py",
    "python scripts/ai/check_doc_links.py",
    "python scripts/ai/check_shell_scripts.py",
    "pre-commit run --all-files",
    "shellcheck -S warning -e SC1091,SC1111,SC2012,SC2034 scripts/*.sh",
    "bash -n scripts/*.sh",
    "--cov-fail-under=85",
    "--cov-fail-under=80",
}
AUTO_RE = re.compile(r"^\s*[-*]\s+`([^`]+)`", re.MULTILINE)


def _collect(relative_root: str, suffix: str) -> set[str]:
    root = ROOT / relative_root
    if not root.exists():
        return set()
    return {
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in root.rglob(f"*{suffix}")
        if path.is_file()
    }


def _check_markdown_command_paths(relative_path: str, failures: list[str]) -> None:
    content = (ROOT / relative_path).read_text(encoding="utf-8")
    for command in AUTO_RE.findall(content):
        token = command.split()[0]
        if "/" not in token:
            continue
        candidate = token.rstrip(".,:)")
        if "*" in candidate or candidate.startswith(("http://", "https://")):
            continue
        if not (ROOT / candidate).exists():
            failures.append(
                f"Markdown command in {relative_path} references missing path: {candidate}"
            )


def main() -> int:
    failures: list[str] = []

    for relative_path in REQUIRED_CANONICAL:
        if not (ROOT / relative_path).exists():
            failures.append(f"Missing required canonical file: {relative_path}")

    for relative_path, content in generated_files().items():
        target = ROOT / relative_path
        expected = content.replace("\r\n", "\n").rstrip() + "\n"
        if not target.exists():
            failures.append(f"Missing generated adapter: {relative_path}")
            continue
        actual = target.read_text(encoding="utf-8")
        if actual != expected:
            failures.append(f"Generated adapter is stale: {relative_path}")

    cursor_files = _collect(".cursor/rules", ".mdc")
    unexpected_cursor = sorted(cursor_files - EXPECTED_CURSOR)
    if unexpected_cursor:
        failures.append(
            "Unexpected Cursor rules present: " + ", ".join(unexpected_cursor)
        )

    qodo_files = _collect(".qodo/workflows", ".toml")
    unexpected_qodo = sorted(qodo_files - EXPECTED_QODO)
    if unexpected_qodo:
        failures.append(
            "Unexpected Qodo workflows present: " + ", ".join(unexpected_qodo)
        )

    for relative_path in ADAPTERS_WITH_BACKLINKS:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        if REQUIRED_BACKLINK not in content:
            failures.append(
                f"Adapter does not link back to canonical docs: {relative_path}"
            )

    workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
    for required_path in sorted(CI_REQUIRED_PATHS):
        if required_path not in workflow:
            failures.append(
                f"CI workflow is missing required path coverage entry: {required_path}"
            )
    for snippet in sorted(CI_REQUIRED_SNIPPETS):
        if snippet not in workflow:
            failures.append(
                "CI workflow is missing required governance check or threshold: "
                f"{snippet}"
            )

    for relative_path in (
        "docs/ai/index.md",
        "docs/ai/validation-and-release.md",
        "AGENTS.md",
        "CLAUDE.md",
    ):
        _check_markdown_command_paths(relative_path, failures)

    if failures:
        for failure in failures:
            print(failure)
        return 1

    print("AI repo setup validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
