#!/usr/bin/env python3
"""Validate local Markdown links in the repository's AI operating system docs."""

from __future__ import annotations

import re
from pathlib import Path

from build_instruction_files import ROOT

MARKDOWN_FILES = [
    ROOT / "README.md",
    ROOT / "CHANGELOG.md",
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    *sorted((ROOT / "docs").rglob("*.md")),
]

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _normalize_target(source: Path, target: str) -> Path | None:
    clean = target.strip()
    if clean.startswith(("http://", "https://", "mailto:", "#")):
        return None

    clean = clean.split("#", 1)[0]
    if not clean:
        return None

    if clean.startswith("/"):
        return ROOT / clean.lstrip("/")

    return (source.parent / clean).resolve()


def main() -> int:
    failures: list[str] = []

    for markdown_file in MARKDOWN_FILES:
        content = markdown_file.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(content):
            target = _normalize_target(markdown_file, raw_target)
            if target is None:
                continue
            try:
                target.relative_to(ROOT)
            except ValueError:
                failures.append(
                    f"{markdown_file.relative_to(ROOT)} links outside repo: {raw_target}"
                )
                continue
            if not target.exists():
                failures.append(
                    f"{markdown_file.relative_to(ROOT)} has broken link: {raw_target}"
                )

    if failures:
        for failure in failures:
            print(failure)
        return 1

    print("Markdown link validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
