# REQ: SWR-050; RISK: RISK-018; SEC: SC-019; TEST: TC-044
"""Static regression tests for camera-streamer systemd hardening.

Filed in response to a 1.4.0 → 1.4.1 OTA blocker on three cameras
stuck on 1.3.0. Their `camera-streamer.service` shipped with
``ProtectSystem=strict`` and ``ReadWritePaths=/data`` only — no
``/var/lib/camera-ota`` — which made the systemd namespace see the
OTA spool directory as read-only even though the underlying
filesystem was rw. The dashboard's `/api/ota/upload` handler then
failed every upload with::

    {"error": "Write failed: [Errno 30] Read-only file system:
              '/var/lib/camera-ota/staging/update.swu.partial'"}

The fix is one line in the unit. The CHALLENGE is preventing the
class of regression from coming back: a future PR could quietly
remove ``/var/lib/camera-ota`` from ``ReadWritePaths``, or move the
spool dir, and the only surface that would catch it is hardware
verification — which is too late.

This test parses ``app/camera/config/camera-streamer.service`` at
build time and asserts the hardening directives MATCH the set of
runtime-writable paths declared in
``REQUIRED_WRITABLE_PATHS`` below. If ``camera_streamer`` ever
starts writing to a new path, the developer adding that write must
also add it here AND to ``ReadWritePaths`` — otherwise CI fails.

This is a *static* test. It doesn't actually run the service or
invoke namespacing — just parses the unit and checks the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
UNIT_FILE = REPO_ROOT / "app" / "camera" / "config" / "camera-streamer.service"

# --- The contract --------------------------------------------------
#
# Map of writable-path → which code path uses it. Adding a new entry
# here means BOTH the code AND the unit must agree:
#   * The code must actually need to write to the path at runtime
#     (otherwise this test is over-permissive — listing extra paths
#     would silently broaden the hardening surface)
#   * The unit's `ReadWritePaths=` must include the path under
#     `ProtectSystem=strict` (otherwise the runtime fails as in the
#     1.3.0 OTA bug above).
#
# When a future PR adds a new writable path:
#   1. Add it here with a clear "USED BY:" comment
#   2. Add it to camera-streamer.service `ReadWritePaths=`
#   3. Both this test and runtime stay in sync.

REQUIRED_WRITABLE_PATHS: dict[str, str] = {
    "/data": (
        "USED BY: camera_streamer.config (camera.conf), "
        "camera_streamer.factory_reset, recordings, certs, motion log, "
        "wifi profiles. Persists across A/B OTA. The most fundamental "
        "writable path; without this nothing works."
    ),
    "/var/lib/camera-ota": (
        "USED BY: camera_streamer.ota_installer, status_server's "
        "/api/ota/upload handler, camera-ota-installer.service spool. "
        "Hosts the trigger file, staged bundle, install status JSON. "
        "Missing this → 1.3.0 OTA-stuck regression — see CHANGELOG 1.4.2."
    ),
}

# Optional read-only protection settings expected on the unit.
# Encoded here so a future PR removing ProtectSystem entirely
# (the way to "fix" a missing ReadWritePaths the WRONG way) also
# fails this test.
REQUIRED_HARDENING: dict[str, str] = {
    "ProtectSystem": "strict",
    "ProtectHome": "true",
    "PrivateTmp": "true",
}


def _parse_directive_list(raw: str) -> list[str]:
    """Parse a systemd directive value that's a space-separated path
    list. Honours backslash-newline continuations and ignores
    inline comments (which systemd doesn't actually allow, but be
    defensive in case someone adds one)."""
    cleaned = raw.replace("\\\n", " ")
    # Strip trailing comments per systemd grammar (only at start of
    # line, but be safe).
    cleaned = re.split(r"\s+#", cleaned, maxsplit=1)[0]
    return [p for p in cleaned.split() if p]


def _read_directive(name: str) -> tuple[str, list[str]]:
    """Find a single occurrence of ``name=...`` in the unit. Returns
    the raw RHS and a parsed list. Asserts exactly one occurrence so
    a duplicated/conflicting directive doesn't slip through."""
    text = UNIT_FILE.read_text(encoding="utf-8")
    matches = re.findall(rf"^\s*{re.escape(name)}=(.*)$", text, re.MULTILINE)
    # Drop blank/comment-only matches.
    matches = [m for m in matches if m.strip() and not m.strip().startswith("#")]
    assert matches, f"{UNIT_FILE.name} is missing required directive '{name}='"
    assert len(matches) == 1, (
        f"{UNIT_FILE.name} declares '{name}=' more than once "
        f"({len(matches)} times) — systemd takes the last one, "
        f"which is brittle. Collapse into a single directive."
    )
    raw = matches[0].strip()
    return raw, _parse_directive_list(raw)


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------


def test_unit_file_exists():
    assert UNIT_FILE.is_file(), (
        f"camera-streamer.service not found at {UNIT_FILE}; "
        "did the recipe layout change?"
    )


@pytest.mark.parametrize(
    "key,expected",
    sorted(REQUIRED_HARDENING.items()),
)
def test_hardening_directive_present(key: str, expected: str):
    """Sanity-check the prerequisite hardening — without
    ProtectSystem=strict, the ReadWritePaths contract is moot."""
    raw, parts = _read_directive(key)
    assert parts == [expected], (
        f"{key}= must be exactly '{expected}' (got '{raw}'). "
        "Weakening this protection is a security regression — see "
        "docs/ai/execution-rules.md 'Security Posture Rule'. "
        "Strengthening it (e.g. swapping 'strict' for 'full' which "
        "doesn't exist in systemd) needs explicit approval and a "
        "matching update to the test contract."
    )


def test_readwritepaths_covers_every_runtime_writable_path():
    """The crux of this test. ``ReadWritePaths`` must enumerate
    every directory the camera process expects to write to at
    runtime. This is what prevented the 1.3.0 cameras from
    accepting OTA uploads."""
    raw, configured = _read_directive("ReadWritePaths")

    # Order doesn't matter; uniqueness does (duplicate entries
    # signal a careless merge).
    assert len(configured) == len(set(configured)), (
        f"ReadWritePaths has duplicate entries: {configured}"
    )

    configured_set = set(configured)
    required_set = set(REQUIRED_WRITABLE_PATHS)

    missing = required_set - configured_set
    extra = configured_set - required_set

    if missing:
        rationale = "\n".join(
            f"  - {p}: {REQUIRED_WRITABLE_PATHS[p]}" for p in sorted(missing)
        )
        pytest.fail(
            "camera-streamer.service ReadWritePaths is missing "
            "runtime-writable directories the camera process needs:\n"
            f"{rationale}\n\n"
            "If this regression ships, the dashboard OTA upload, "
            "motion log writes, or factory-reset flow will fail "
            "with `Read-only file system` — even though the "
            "underlying disk is rw — because systemd's namespace "
            "masks the path read-only. Add the missing path(s) "
            "back to ReadWritePaths in "
            f"{UNIT_FILE.relative_to(REPO_ROOT)}."
        )

    if extra:
        # An extra path is less catastrophic than a missing one —
        # it's over-permissive, not failing — but it still indicates
        # drift. Fail so the developer either (a) deletes the unused
        # path from the unit OR (b) adds it to
        # REQUIRED_WRITABLE_PATHS with a "USED BY" rationale.
        pytest.fail(
            "camera-streamer.service ReadWritePaths includes "
            f"directories not declared in REQUIRED_WRITABLE_PATHS: "
            f"{sorted(extra)}. Either remove from the unit or add "
            "to this test with a 'USED BY:' rationale describing "
            "which code path writes there."
        )


def test_readwritepaths_paths_are_absolute():
    """systemd interprets relative paths in ``ReadWritePaths`` as
    namespace-paths, which is almost never what we want. Catch the
    typo class early."""
    _, configured = _read_directive("ReadWritePaths")
    for p in configured:
        assert p.startswith("/"), (
            f"ReadWritePaths entry '{p}' is not an absolute path. "
            "systemd would interpret it relative to the unit's "
            "namespace root, which silently mis-targets the directive."
        )
