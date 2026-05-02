#!/usr/bin/env python3
"""Validate draft traceability records and code annotations.

This checker is intentionally conservative and repository-local. It verifies
that the controlled markdown records define IDs, the CSV matrix links those
IDs, and code annotations reference known IDs.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ID_RE = re.compile(
    r"\b(?:UN|SYS|SWR|HWR|ARCH|SWA|HWA|HAZ|RISK|RC|DFMEA|SEC|THREAT|SC|TC)-\d{3}\b"
)
ANNOTATION_RE = re.compile(r"\b(?:REQ|RISK|SEC|TEST):\s*([A-Z0-9,\-;\s]+)")

DEFINITION_FILES = {
    "UN": [ROOT / "docs/intended-use/user-needs.md"],
    "SYS": [ROOT / "docs/requirements/system-requirements.md"],
    "SWR": [ROOT / "docs/requirements/software-requirements.md"],
    "HWR": [ROOT / "docs/requirements/hardware-requirements.md"],
    "ARCH": [ROOT / "docs/architecture/system-architecture.md"],
    "SWA": [ROOT / "docs/architecture/software-architecture.md"],
    "HWA": [ROOT / "docs/architecture/hardware-architecture.md"],
    "HAZ": [ROOT / "docs/risk/hazard-analysis.md"],
    "RISK": [ROOT / "docs/risk/hazard-analysis.md"],
    "RC": [ROOT / "docs/risk/risk-control-verification.md"],
    "DFMEA": [ROOT / "docs/risk/dfmea.md"],
    "SEC": [ROOT / "docs/cybersecurity/security-plan.md"],
    "THREAT": [ROOT / "docs/cybersecurity/threat-model.md"],
    "SC": [ROOT / "docs/cybersecurity/security-risk-analysis.md"],
    "TC": [ROOT / "docs/verification-validation/test-cases.md"],
}

MATRIX_PATH = ROOT / "docs/traceability/traceability-matrix.csv"
CONTROLLED_DOC_ROOTS = [
    ROOT / "docs/intended-use",
    ROOT / "docs/requirements",
    ROOT / "docs/architecture",
    ROOT / "docs/risk",
    ROOT / "docs/cybersecurity",
    ROOT / "docs/verification-validation",
    ROOT / "docs/traceability",
    ROOT / "docs/quality-records",
]
CODE_ROOTS = [
    ROOT / "app",
    ROOT / "scripts",
    ROOT / "tools",
    ROOT / ".github",
    ROOT / "meta-home-monitor",
    ROOT / "config",
    ROOT / "swupdate",
]
CODE_SUFFIXES = {
    ".bb",
    ".bbappend",
    ".conf",
    ".css",
    ".html",
    ".inc",
    ".js",
    ".py",
    ".service",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}
EXCLUDED_CODE_PATTERNS = [
    re.compile(r"/__pycache__/"),
    re.compile(r"^app/server/monitor/static/js/(?:alpine|hls|htmx)\.min\.js$"),
]
REQUIREMENT_PREFIXES = {"SYS", "SWR", "HWR"}
ARCHITECTURE_PREFIXES = {"ARCH", "SWA", "HWA"}


def prefix_of(identifier: str) -> str:
    return identifier.split("-", 1)[0]


def ids_in_text(text: str) -> set[str]:
    return set(ID_RE.findall(text))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_definitions() -> tuple[dict[str, set[Path]], list[str]]:
    definitions: dict[str, set[Path]] = defaultdict(set)
    errors: list[str] = []
    for expected_prefix, paths in DEFINITION_FILES.items():
        for path in paths:
            if not path.exists():
                errors.append(f"Missing definition file: {path.relative_to(ROOT)}")
                continue
            for identifier in ids_in_text(read_text(path)):
                if prefix_of(identifier) == expected_prefix:
                    definitions[identifier].add(path)
    return definitions, errors


def collect_controlled_doc_refs() -> dict[str, set[Path]]:
    refs: dict[str, set[Path]] = defaultdict(set)
    for root in CONTROLLED_DOC_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            for identifier in ids_in_text(read_text(path)):
                refs[identifier].add(path)
    return refs


def read_matrix() -> tuple[list[dict[str, str]], list[str]]:
    if not MATRIX_PATH.exists():
        return [], [f"Missing matrix: {MATRIX_PATH.relative_to(ROOT)}"]
    with MATRIX_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        return [], ["Traceability matrix has no rows"]
    required = {
        "User Need",
        "System Requirement",
        "Software Requirement",
        "Hardware Requirement",
        "Architecture",
        "Risk",
        "Risk Control",
        "Security Threat",
        "Security Control",
        "Code Reference",
        "Test Case",
        "Test Result/Status",
    }
    missing = sorted(required - set(rows[0]))
    if missing:
        return rows, [f"Traceability matrix missing columns: {', '.join(missing)}"]
    return rows, []


def matrix_ids_by_column(rows: list[dict[str, str]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for column, value in row.items():
            out[column].update(ids_in_text(value or ""))
    return out


def row_has_any(
    row: dict[str, str], columns: list[str], prefix: str | None = None
) -> bool:
    identifiers: set[str] = set()
    for column in columns:
        identifiers.update(ids_in_text(row.get(column, "")))
    if prefix is None:
        return bool(identifiers)
    return any(prefix_of(identifier) == prefix for identifier in identifiers)


def is_traceable_code_file(path: Path) -> bool:
    if not path.is_file() or path.suffix not in CODE_SUFFIXES:
        return False
    rel = path.relative_to(ROOT).as_posix()
    return not any(pattern.search(rel) for pattern in EXCLUDED_CODE_PATTERNS)


def collect_traceable_code_files() -> list[Path]:
    files: list[Path] = []
    for root in CODE_ROOTS:
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*") if is_traceable_code_file(path))
    return sorted(set(files))


def collect_code_annotations() -> tuple[
    dict[str, set[Path]], dict[Path, set[str]], list[str]
]:
    refs: dict[str, set[Path]] = defaultdict(set)
    req_refs_by_file: dict[Path, set[str]] = defaultdict(set)
    malformed: list[str] = []
    for path in collect_traceable_code_files():
        try:
            text = read_text(path)
        except UnicodeDecodeError:
            continue
        for match in ANNOTATION_RE.finditer(text):
            ids = ids_in_text(match.group(1))
            if not ids:
                malformed.append(
                    f"{path.relative_to(ROOT)} has annotation without valid ID: "
                    f"{match.group(0)!r}"
                )
            for identifier in ids:
                refs[identifier].add(path)
                if (
                    match.group(0).startswith("REQ:")
                    and prefix_of(identifier) in REQUIREMENT_PREFIXES
                ):
                    req_refs_by_file[path].add(identifier)
    return refs, req_refs_by_file, malformed


def requirement_trace_complete(requirement_id: str, rows: list[dict[str, str]]) -> bool:
    req_columns = [
        "User Need",
        "System Requirement",
        "Software Requirement",
        "Hardware Requirement",
    ]
    rows_for_req = [
        row
        for row in rows
        if any(requirement_id in ids_in_text(row.get(col, "")) for col in req_columns)
    ]
    return any(
        row_has_any(row, ["User Need"], "UN")
        and row_has_any(row, ["System Requirement"], "SYS")
        and any(
            prefix_of(identifier) in ARCHITECTURE_PREFIXES
            for identifier in ids_in_text(row.get("Architecture", ""))
        )
        for row in rows_for_req
    )


def main() -> int:
    # REQ: SWR-019; RISK: RISK-009; SEC: SC-009; TEST: TC-020
    failures: list[str] = []
    warnings: list[str] = []

    definitions, definition_errors = collect_definitions()
    failures.extend(definition_errors)
    known = set(definitions)

    doc_refs = collect_controlled_doc_refs()
    rows, matrix_errors = read_matrix()
    failures.extend(matrix_errors)
    by_column = matrix_ids_by_column(rows)
    matrix_ids = set().union(*by_column.values()) if by_column else set()

    traceable_code_files = collect_traceable_code_files()
    code_refs, req_refs_by_file, malformed_annotations = collect_code_annotations()
    failures.extend(malformed_annotations)

    for identifier in sorted(set(doc_refs) | matrix_ids | set(code_refs)):
        if identifier not in known:
            locations = doc_refs.get(identifier, set()) | code_refs.get(
                identifier, set()
            )
            loc = ", ".join(sorted(str(p.relative_to(ROOT)) for p in locations))
            failures.append(
                f"Undefined ID referenced: {identifier}" + (f" ({loc})" if loc else "")
            )

    for identifier in sorted(known):
        if identifier not in matrix_ids:
            failures.append(
                f"Defined ID missing from traceability matrix: {identifier}"
            )

    req_columns = [
        "User Need",
        "System Requirement",
        "Software Requirement",
        "Hardware Requirement",
    ]
    for identifier in sorted(
        i for i in known if prefix_of(i) in {"UN", "SYS", "SWR", "HWR"}
    ):
        linked = any(
            identifier in ids_in_text(row.get(col, ""))
            for row in rows
            for col in req_columns
        )
        if not linked:
            failures.append(
                f"Requirement ID not linked in requirement columns: {identifier}"
            )

    for swr in sorted(i for i in known if prefix_of(i) == "SWR"):
        linked_tests = [
            row
            for row in rows
            if swr in ids_in_text(row.get("Software Requirement", ""))
            and row_has_any(row, ["Test Case"], "TC")
        ]
        if not linked_tests:
            failures.append(f"Software requirement without linked test: {swr}")

    for risk in sorted(i for i in known if prefix_of(i) == "RISK"):
        linked_controls = [
            row
            for row in rows
            if risk in ids_in_text(row.get("Risk", ""))
            and row_has_any(row, ["Risk Control"], "RC")
        ]
        if not linked_controls:
            failures.append(f"Risk without linked control: {risk}")

    for control in sorted(i for i in known if prefix_of(i) == "RC"):
        linked_tests = [
            row
            for row in rows
            if control in ids_in_text(row.get("Risk Control", ""))
            and row_has_any(row, ["Test Case"], "TC")
        ]
        if not linked_tests:
            failures.append(f"Risk control without verification test: {control}")

    for threat in sorted(i for i in known if prefix_of(i) == "THREAT"):
        linked_controls = [
            row
            for row in rows
            if threat in ids_in_text(row.get("Security Threat", ""))
            and row_has_any(row, ["Security Control"], "SC")
        ]
        if not linked_controls:
            failures.append(f"Threat without linked security control: {threat}")

    for control in sorted(i for i in known if prefix_of(i) == "SC"):
        linked_tests = [
            row
            for row in rows
            if control in ids_in_text(row.get("Security Control", ""))
            and row_has_any(row, ["Test Case"], "TC")
        ]
        if not linked_tests:
            failures.append(f"Security control without verification test: {control}")

    for tc in sorted(i for i in known if prefix_of(i) == "TC"):
        rows_for_tc = [
            row for row in rows if tc in ids_in_text(row.get("Test Case", ""))
        ]
        if not rows_for_tc:
            failures.append(f"Test case missing from matrix: {tc}")
            continue
        if not any(row_has_any(row, req_columns) for row in rows_for_tc):
            failures.append(f"Test case without linked requirement: {tc}")

    if not code_refs:
        warnings.append("No code-level traceability annotations found")

    for path in traceable_code_files:
        if not req_refs_by_file.get(path):
            failures.append(
                f"Traceable code file without REQ annotation: {path.relative_to(ROOT)}"
            )

    for path, req_ids in sorted(
        req_refs_by_file.items(), key=lambda item: str(item[0].relative_to(ROOT))
    ):
        for requirement_id in sorted(req_ids):
            if not requirement_trace_complete(requirement_id, rows):
                failures.append(
                    f"{path.relative_to(ROOT)} REQ {requirement_id} does not trace "
                    "to user need, system requirement, and architecture"
                )

    print("Traceability check")
    print(f"- Defined IDs: {len(known)}")
    print(f"- Matrix rows: {len(rows)}")
    print(f"- Code annotation IDs: {len(code_refs)}")
    print(f"- Traceable code files: {len(traceable_code_files)}")
    print(f"- Code files with REQ annotations: {len(req_refs_by_file)}")
    if warnings:
        print("\nWarnings:")
        for item in warnings:
            print(f"- {item}")
    if failures:
        print("\nFailures:")
        for item in failures:
            print(f"- {item}")
        return 1
    print("\nTraceability check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
