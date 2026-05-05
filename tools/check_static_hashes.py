# REQ: SWR-048; RISK: RISK-019; SEC: SC-018; TEST: TC-045
"""Pin vendored browser assets to reviewed SHA-256 digests."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SHA256 = {
    "app/camera/camera_streamer/static/qrcode.min.js": (
        "bb2365e4902f4f84852cf4025e6f6a60325a682aeafa43fb63b7fc8f098d1ef2"
    ),
    "app/server/monitor/static/qrcode.min.js": (
        "bb2365e4902f4f84852cf4025e6f6a60325a682aeafa43fb63b7fc8f098d1ef2"
    ),
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    failures: list[str] = []
    for relative_path, expected in sorted(EXPECTED_SHA256.items()):
        path = ROOT / relative_path
        if not path.exists():
            failures.append(f"missing vendored asset: {relative_path}")
            continue
        actual = sha256_of(path)
        if actual != expected:
            failures.append(
                f"sha256 mismatch for {relative_path}: expected {expected}, got {actual}"
            )

    if failures:
        for failure in failures:
            print(f"check_static_hashes: FAIL - {failure}", file=sys.stderr)
        return 1

    print(
        "check_static_hashes: OK - " + ", ".join(sorted(EXPECTED_SHA256)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
