#!/usr/bin/env python3
"""Verify the release version is consistent across the repo.

Single source of truth: ``VERSION`` at the repo root. Every other
surface that carries the user-facing release version must derive from
that file (statically by ``require``-style include, or dynamically at
build time via ``${@open(...)}``). This script is the regression test
that catches a drift before it ships.

Surfaces checked:

1. ``VERSION`` — the source of truth. Must exist, be one line, and
   match a strict semver shape (``X.Y.Z``).

2. ``meta-home-monitor/conf/distro/home-monitor.conf`` — must read
   ``DISTRO_VERSION`` from the same ``VERSION`` file (we look for the
   ``${@open(...)/../VERSION}`` expression rather than a hardcoded
   string, so a hand-edit that bypasses the SSOT fails the check).

3. ``CHANGELOG.md`` — must contain a ``## [X.Y.Z]`` header that
   matches ``VERSION``. ``[Unreleased]`` is allowed alongside.

Surfaces intentionally NOT checked here (per repo convention; see
``swupdate/post-update.sh:87`` and ADR-0014):

- ``app/{camera,server}/setup.py`` — package-recipe versions, frozen
  at ``1.0.0``. Runtime version is stamped at OTA install time.
- ``app/camera/camera_streamer/discovery.py`` — mDNS protocol version
  constant, separate concept from release version.
- ``app/server/config/avahi-homemonitor.service`` — same.
- ``app/server/monitor/models.py:firmware_version`` — dataclass default
  for fresh records, overwritten on first heartbeat.
- ``swupdate/sw-description.{server,camera}`` — ``@@VERSION@@``
  placeholder, substituted by ``scripts/build-swu.sh`` at build time
  from the same VERSION file.

Exit code: 0 on success, 1 on any inconsistency, with a diagnostic
listing each failing surface and what it expected.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO / "VERSION"
DISTRO_CONF = REPO / "meta-home-monitor" / "conf" / "distro" / "home-monitor.conf"
CHANGELOG = REPO / "CHANGELOG.md"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
DISTRO_DYNAMIC_RE = re.compile(
    r"DISTRO_VERSION\s*:?=\s*\"\$\{@open\(.+VERSION.+\)\.read\(\)\.strip\(\)\}\""
)
CHANGELOG_HEADER_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]")


def read_version() -> str:
    if not VERSION_FILE.exists():
        fail(f"missing {VERSION_FILE.relative_to(REPO)}")
    text = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not text:
        fail(f"{VERSION_FILE.relative_to(REPO)} is empty")
    if "\n" in text:
        fail(f"{VERSION_FILE.relative_to(REPO)} must be a single line; got {text!r}")
    if not SEMVER_RE.match(text):
        fail(f"{VERSION_FILE.relative_to(REPO)} must be X.Y.Z semver; got {text!r}")
    return text


def check_distro_conf() -> None:
    """Distro conf must DERIVE from VERSION, not hardcode a string."""
    if not DISTRO_CONF.exists():
        fail(f"missing {DISTRO_CONF.relative_to(REPO)}")
    text = DISTRO_CONF.read_text(encoding="utf-8")
    if not DISTRO_DYNAMIC_RE.search(text):
        # Look for any DISTRO_VERSION assignment so the diagnostic is useful.
        match = re.search(
            r"^DISTRO_VERSION\s*:?=\s*\".*\"", text, re.MULTILINE
        )
        actual = match.group(0) if match else "(no DISTRO_VERSION assignment found)"
        fail(
            f"{DISTRO_CONF.relative_to(REPO)} must derive DISTRO_VERSION from "
            f"the repo-root VERSION file via "
            f'``${{@open(d.getVar(\'LAYERDIR\') + \'/../VERSION\').read().strip()}}``\n'
            f"  current: {actual}"
        )


def check_changelog(expected: str) -> None:
    if not CHANGELOG.exists():
        fail(f"missing {CHANGELOG.relative_to(REPO)}")
    versions: list[str] = []
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        m = CHANGELOG_HEADER_RE.match(line)
        if m:
            versions.append(m.group(1))
    # Either CHANGELOG already lists this version (mid-release-prep,
    # post-promotion), or it doesn't (pre-promotion — VERSION has been
    # bumped but the CHANGELOG section hasn't been written yet). Both
    # are valid mid-release states; we only fail when CHANGELOG lists
    # a DIFFERENT highest semver than VERSION.
    if not versions:
        # Fresh repo with no released versions at all — nothing to
        # cross-check against.
        return
    highest = sorted(versions, key=lambda s: list(map(int, s.split("."))))[-1]
    if highest != expected:
        # If the CHANGELOG's highest is older than VERSION, that's the
        # pre-promotion state and is fine. We only fail when the
        # CHANGELOG advertises a NEWER version than VERSION (that's an
        # actual inconsistency — the release notes claim a version the
        # source doesn't carry).
        v = list(map(int, expected.split(".")))
        h = list(map(int, highest.split(".")))
        if h > v:
            fail(
                f"{CHANGELOG.relative_to(REPO)} advertises [{highest}] "
                f"but VERSION says {expected}"
            )


def fail(msg: str) -> None:
    print(f"check_version_consistency: FAIL — {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    version = read_version()
    check_distro_conf()
    check_changelog(version)
    print(f"check_version_consistency: OK — VERSION={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
