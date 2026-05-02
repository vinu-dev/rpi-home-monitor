#!/usr/bin/env python3
# REQ: SWR-055; RISK: RISK-009; SEC: SC-009; TEST: TC-020, TC-045
"""Validate the documentation map used by humans and AI agents."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
MAP = DOCS / "doc-map.yml"

REQUIRED_KEYS = {
    "path",
    "title",
    "type",
    "status",
    "audience",
    "source_of_truth",
}
ALLOWED_TOP_LEVEL_MARKDOWN = {"README.md"}
CURRENT_SOURCE_PREFIXES = {
    "docs/README.md",
    "docs/ai/",
    "docs/intended-use/",
    "docs/requirements/",
    "docs/architecture/",
    "docs/risk/",
    "docs/cybersecurity/",
    "docs/verification-validation/",
    "docs/traceability/",
    "docs/quality-records/",
}


def parse_doc_map() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for raw_line in MAP.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- path:"):
            if current is not None:
                entries.append(current)
            current = {"path": line.split(":", 1)[1].strip().strip('"')}
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key.strip()] = value.strip().strip('"')

    if current is not None:
        entries.append(current)
    return entries


def is_source_of_truth(entry: dict[str, str]) -> bool:
    return entry.get("source_of_truth", "").lower() == "true"


def main() -> int:
    failures: list[str] = []

    if not MAP.exists():
        print("Missing docs/doc-map.yml")
        return 1

    entries = parse_doc_map()
    if not entries:
        failures.append("docs/doc-map.yml has no entries")

    mapped_paths = {entry.get("path", "") for entry in entries}
    for entry in entries:
        path = entry.get("path", "")
        missing_keys = sorted(REQUIRED_KEYS - set(entry))
        if missing_keys:
            failures.append(f"{path or '<missing path>'} missing keys: {missing_keys}")
        if not path:
            continue
        target = ROOT / path
        if not target.exists():
            failures.append(f"Mapped documentation path does not exist: {path}")
        if is_source_of_truth(entry) and path.startswith(
            ("docs/archive/", "docs/history/")
        ):
            failures.append(
                f"Archived or historical path marked source_of_truth: {path}"
            )

    for prefix in sorted(CURRENT_SOURCE_PREFIXES):
        if prefix not in mapped_paths:
            failures.append(f"Current source prefix missing from doc map: {prefix}")

    for markdown_file in DOCS.glob("*.md"):
        if markdown_file.name not in ALLOWED_TOP_LEVEL_MARKDOWN:
            failures.append(
                "Top-level docs should be routed through guides/history/records: "
                f"{markdown_file.relative_to(ROOT)}"
            )

    if failures:
        for failure in failures:
            print(failure)
        return 1

    print("Documentation map validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
