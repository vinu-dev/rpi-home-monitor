#!/usr/bin/env python3
"""Validate the repository AI operating system layout."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "docs/ai/index.md",
    "docs/ai/mission-and-goals.md",
    "docs/ai/repo-map.md",
    "docs/ai/working-agreement.md",
    "docs/ai/engineering-standards.md",
    "docs/ai/design-standards.md",
    "docs/ai/validation-and-release.md",
    "docs/exec-plans/template.md",
    ".claude/settings.json",
    ".github/copilot-instructions.md",
    ".github/pull_request_template.md",
    ".github/instructions/server.instructions.md",
    ".github/instructions/camera.instructions.md",
    ".github/instructions/yocto.instructions.md",
    ".github/instructions/docs.instructions.md",
    ".cursor/rules/00-repo-overview.mdc",
    ".cursor/rules/10-goals-and-plans.mdc",
    ".cursor/rules/20-server-python.mdc",
    ".cursor/rules/30-camera-python.mdc",
    ".cursor/rules/40-yocto-and-release.mdc",
    ".cursor/rules/50-testing-and-smoke.mdc",
    ".cursor/rules/60-design-standards.mdc",
    ".qodo/workflows/server-smoke.toml",
    ".qodo/workflows/yocto-validate.toml",
]

ADAPTERS = {
    "AGENTS.md": "docs/ai/index.md",
    "CLAUDE.md": "docs/ai/index.md",
    ".github/copilot-instructions.md": "AGENTS.md",
}

LINE_BUDGETS = {
    "AGENTS.md": 140,
    "CLAUDE.md": 80,
    ".github/copilot-instructions.md": 60,
}


def _error(message: str, errors: list[str]) -> None:
    errors.append(message)


def main() -> int:
    errors: list[str] = []

    for rel_path in REQUIRED_FILES:
        if not (ROOT / rel_path).exists():
            _error(f"Missing required file: {rel_path}", errors)

    for rel_path, needle in ADAPTERS.items():
        path = ROOT / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if needle not in text:
            _error(f"{rel_path} must reference {needle}", errors)

    for rel_path, max_lines in LINE_BUDGETS.items():
        path = ROOT / rel_path
        if not path.exists():
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > max_lines:
            _error(f"{rel_path} exceeds line budget ({line_count} > {max_lines})", errors)

    settings_path = ROOT / ".claude/settings.json"
    if settings_path.exists():
        try:
            json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _error(f".claude/settings.json is invalid JSON: {exc}", errors)

    ai_index = ROOT / "docs/ai/index.md"
    if ai_index.exists():
        ai_text = ai_index.read_text(encoding="utf-8")
        required_refs = [
            "mission-and-goals.md",
            "repo-map.md",
            "working-agreement.md",
            "engineering-standards.md",
            "design-standards.md",
            "validation-and-release.md",
        ]
        for ref in required_refs:
            if ref not in ai_text:
                _error(f"docs/ai/index.md must reference {ref}", errors)

    if errors:
        print("AI repo validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("AI repo validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
