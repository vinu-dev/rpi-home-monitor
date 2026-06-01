# REQ: SWR-010, SWR-038, SWR-046; RISK: RISK-004, RISK-019; SEC: SC-003, SC-018; TEST: TC-013, TC-036, TC-043
"""Shared OTA bundle ordering policy.

The server and camera run in different Python packages, but they must
make the same safety decision about an update bundle: never install an
older, parseable release over a newer running image by accident.

This module is copied into both package namespaces at build time:

  /opt/monitor/monitor/ota_policy.py
  /opt/camera/camera_streamer/ota_policy.py

It deliberately has no Flask, requests, packaging, or filesystem
dependencies so the Pi Zero camera path can use it too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "UpdateDecision",
    "classify_update",
    "compare_versions",
    "is_blocked_downgrade",
    "versions_match",
]

_SEMVER_RE = re.compile(
    r"^v?"
    r"(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)
_NUM_RE = re.compile(r"^(0|[1-9]\d*)$")
_BUILD_PROFILE_VERSION_RE = re.compile(
    r"^(?P<base>v?\d+\.\d+\.\d+)-(?P<profile>dev|prod)$"
)


@dataclass(frozen=True)
class _ParsedVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]


@dataclass(frozen=True)
class UpdateDecision:
    """Result of comparing a staged bundle with the running image."""

    relation: str
    allowed: bool
    current_version: str
    target_version: str
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "relation": self.relation,
            "allowed": self.allowed,
            "current_version": self.current_version,
            "target_version": self.target_version,
            "reason": self.reason,
        }


def _parse(value: str) -> _ParsedVersion | None:
    text = (value or "").strip()
    if not text:
        return None
    match = _SEMVER_RE.match(text)
    if not match:
        return None
    prerelease = tuple((match.group("pre") or "").split("."))
    if prerelease == ("",):
        prerelease = ()
    return _ParsedVersion(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=prerelease,
    )


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    # SemVer: absence of prerelease sorts after any prerelease for the
    # same major/minor/patch (1.2.3 > 1.2.3-rc.1).
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1

    for l_part, r_part in zip(left, right, strict=False):
        if l_part == r_part:
            continue
        l_numeric = _NUM_RE.match(l_part) is not None
        r_numeric = _NUM_RE.match(r_part) is not None
        if l_numeric and r_numeric:
            l_num = int(l_part)
            r_num = int(r_part)
            return (l_num > r_num) - (l_num < r_num)
        if l_numeric:
            return -1
        if r_numeric:
            return 1
        return (l_part > r_part) - (l_part < r_part)

    return (len(left) > len(right)) - (len(left) < len(right))


def compare_versions(left: str, right: str) -> int | None:
    """Compare two SemVer-like release strings.

    Returns ``-1`` when ``left < right``, ``0`` when equal, ``1`` when
    ``left > right``. Returns ``None`` if either side is missing or not
    parseable as ``X.Y.Z`` with an optional SemVer prerelease/build
    suffix. Unknown versions are not ordered because forcing a decision
    there would strand older recovery bundles.
    """

    left_parsed = _parse(left)
    right_parsed = _parse(right)
    if left_parsed is None or right_parsed is None:
        return None

    left_base = (left_parsed.major, left_parsed.minor, left_parsed.patch)
    right_base = (right_parsed.major, right_parsed.minor, right_parsed.patch)
    if left_base != right_base:
        return (left_base > right_base) - (left_base < right_base)
    return _compare_prerelease(left_parsed.prerelease, right_parsed.prerelease)


def _strip_build_profile_suffix(version: str) -> str:
    match = _BUILD_PROFILE_VERSION_RE.match((version or "").strip())
    if not match:
        return version
    return match.group("base")


def versions_match(actual: str, expected: str) -> bool:
    """Return True when runtime and target labels refer to the same image.

    Runtime firmware reports plain ``VERSION_ID`` values such as ``1.6.10``.
    Lab/dev SWU labels may include a build profile suffix such as
    ``v1.6.10-dev``. For activation confirmation those are the same image,
    while real prereleases like ``1.6.10-dev.20260601`` remain distinct.
    """

    actual = str(actual or "")
    expected = str(expected or "")
    if not expected:
        return False
    candidates = (
        (actual, expected),
        (_strip_build_profile_suffix(actual), _strip_build_profile_suffix(expected)),
    )
    for actual_candidate, expected_candidate in candidates:
        if actual_candidate == expected_candidate:
            return True
        ordering = compare_versions(actual_candidate, expected_candidate)
        if ordering == 0:
            return True
    return False


def classify_update(current_version: str, target_version: str) -> UpdateDecision:
    """Classify an OTA bundle against the running image.

    Downgrades are blocked only when both versions are parseable. That
    gives modern, correctly-versioned bundles strict protection while
    still allowing an operator to use a legacy recovery bundle whose
    metadata predates this policy.
    """

    current = (current_version or "").strip()
    target = (target_version or "").strip()
    if versions_match(current, target):
        return UpdateDecision(
            relation="same",
            allowed=True,
            current_version=current,
            target_version=target,
        )
    ordering = compare_versions(target, current)
    if ordering is None:
        return UpdateDecision(
            relation="unknown",
            allowed=True,
            current_version=current,
            target_version=target,
        )
    if ordering < 0:
        reason = (
            f"Rejected older update {target or 'unknown'}; "
            f"current version is {current or 'unknown'}."
        )
        return UpdateDecision(
            relation="downgrade",
            allowed=False,
            current_version=current,
            target_version=target,
            reason=reason,
        )
    if ordering == 0:
        return UpdateDecision(
            relation="same",
            allowed=True,
            current_version=current,
            target_version=target,
        )
    return UpdateDecision(
        relation="upgrade",
        allowed=True,
        current_version=current,
        target_version=target,
    )


def is_blocked_downgrade(current_version: str, target_version: str) -> bool:
    return classify_update(current_version, target_version).blocked
