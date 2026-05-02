#!/usr/bin/env python3
# REQ: SWR-055; RISK: RISK-009; SEC: SC-009; TEST: TC-020, TC-045
"""Enforce baseline shell conventions for repository scripts."""

from __future__ import annotations

from build_instruction_files import ROOT

EXPECTED_SHEBANG = "#!/usr/bin/env bash"
EXPECTED_STRICT_MODE = "set -euo pipefail"


def main() -> int:
    failures: list[str] = []

    for script in sorted((ROOT / "scripts").glob("*.sh")):
        content = script.read_text(encoding="utf-8")
        lines = content.splitlines()
        if not lines:
            failures.append(f"{script.relative_to(ROOT)} is empty")
            continue
        if lines[0] != EXPECTED_SHEBANG:
            failures.append(
                f"{script.relative_to(ROOT)} must use shebang: {EXPECTED_SHEBANG}"
            )
        if EXPECTED_STRICT_MODE not in content:
            failures.append(
                f"{script.relative_to(ROOT)} must enable strict mode with '{EXPECTED_STRICT_MODE}'"
            )

    if failures:
        for failure in failures:
            print(failure)
        return 1

    print("Shell script convention checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
