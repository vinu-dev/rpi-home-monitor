#!/usr/bin/env python3
"""Static guards for the versioning SSOT design (1.4.3).

The "single source of truth" policy lives in
``docs/architecture/versioning.md``. This script enforces it.

Each check below maps to one of the seven guardrails from §G of
that document. Failures print a specific remediation pointing back
at the design section.

Run from the repo root:

    python3 scripts/check_versioning_design.py

Exit code 0 on success, 1 on any failure.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"

# --- Helpers -------------------------------------------------------


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


# --- Check 1 — sw-versions recipe is templated ---------------------


def check_sw_versions_recipe_templated() -> list[str]:
    """The recipe must NOT install a static file. It must template
    ``${DISTRO_VERSION}`` into ``/etc/sw-versions`` at build time."""
    failures: list[str] = []
    recipe = (
        REPO_ROOT
        / "meta-home-monitor"
        / "recipes-core"
        / "sw-versions"
        / "sw-versions_1.0.bb"
    )
    if not recipe.is_file():
        return [f"missing recipe: {recipe}"]
    text = recipe.read_text(encoding="utf-8")
    # Static install of a baseline file is the regression we're guarding.
    bad_patterns = [
        r"install -m \d+ \$\{WORKDIR\}/sw-versions\b",
        r'SRC_URI\s*=\s*"file://sw-versions"',
    ]
    for pat in bad_patterns:
        if re.search(pat, text):
            failures.append(
                f"{recipe.name}: matches static-baseline pattern /{pat}/ "
                "— design §J step 1 requires a templated do_install that "
                'echoes "home-monitor ${DISTRO_VERSION}" instead.'
            )
    if "DISTRO_VERSION" not in text:
        failures.append(
            f"{recipe.name}: does not reference ${{DISTRO_VERSION}} — the "
            "templated do_install must use the distro variable so the "
            "value tracks the VERSION file."
        )
    # The legacy static baseline file should be deleted entirely.
    legacy_baseline = recipe.parent / "files" / "sw-versions"
    if legacy_baseline.exists():
        failures.append(
            f"legacy static baseline still present at {legacy_baseline} — "
            "the templated recipe makes this redundant; remove it (and "
            "its parent files/ directory if empty)."
        )
    return failures


# --- Check 2 — release_version helper is byte-identical in 3 places -


def check_release_version_helper_canonical() -> list[str]:
    """The shared helper exists at one canonical path plus an
    identical copy in each of the camera and server packages.
    All three must be byte-identical so neither package can drift
    from the canonical source.
    """
    failures: list[str] = []
    canonical = REPO_ROOT / "app" / "shared" / "release_version" / "release_version.py"
    camera_copy = (
        REPO_ROOT / "app" / "camera" / "camera_streamer" / "release_version.py"
    )
    server_copy = REPO_ROOT / "app" / "server" / "monitor" / "release_version.py"
    for p in (canonical, camera_copy, server_copy):
        if not p.is_file():
            failures.append(f"missing release_version helper: {p}")
    if failures:
        return failures
    h_canonical = _sha256(canonical)
    h_camera = _sha256(camera_copy)
    h_server = _sha256(server_copy)
    if h_camera != h_canonical:
        failures.append(
            f"release_version drift: camera copy diverged from canonical\n"
            f"  canonical: {canonical}  sha256={h_canonical}\n"
            f"  camera:    {camera_copy}  sha256={h_camera}\n"
            "Re-copy from canonical: cp app/shared/release_version/release_version.py "
            "app/camera/camera_streamer/release_version.py"
        )
    if h_server != h_canonical:
        failures.append(
            f"release_version drift: server copy diverged from canonical\n"
            f"  canonical: {canonical}  sha256={h_canonical}\n"
            f"  server:    {server_copy}  sha256={h_server}\n"
            "Re-copy from canonical: cp app/shared/release_version/release_version.py "
            "app/server/monitor/release_version.py"
        )
    return failures


# --- Check 3 — no app code reads /etc/sw-versions ------------------


def check_no_app_reads_sw_versions() -> list[str]:
    """After 1.4.3, application code MUST NOT read /etc/sw-versions
    for display/heartbeat purposes. The image-side SSOT is
    /etc/os-release VERSION_ID via release_version().

    We pattern-match on actual file-system access syntax —
    ``open("/etc/sw-versions")``, ``Path("/etc/sw-versions")``,
    ``with open('/etc/sw-versions')`` — NOT mentions in comments
    or docstrings. The migration commits intentionally name the
    old path in their explanatory docstrings; flagging those would
    force us to either delete the migration explanation or
    obfuscate it, neither of which serves future readers.
    """
    failures: list[str] = []
    # Catches open(..., '/etc/sw-versions'), Path('/etc/sw-versions'),
    # PosixPath, etc. — any Python construct that actually opens or
    # constructs a path object pointing at the file.
    pattern = re.compile(rb"""(open|Path|PosixPath)\s*\(\s*["']/etc/sw-versions""")
    scan_roots = [
        REPO_ROOT / "app" / "camera" / "camera_streamer",
        REPO_ROOT / "app" / "server" / "monitor",
    ]
    for root in scan_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            data = path.read_bytes()
            if pattern.search(data):
                failures.append(
                    f"{path.relative_to(REPO_ROOT)}: opens /etc/sw-versions "
                    "directly — design §C requires reads to go through "
                    "release_version() (which reads /etc/os-release "
                    "VERSION_ID instead)."
                )
    return failures


# --- Check 4 — release_version is the only firmware-version source --


def check_release_version_helper_is_only_reader() -> list[str]:
    """No app file outside the helper module itself may parse
    /etc/os-release directly. (One-helper rule.)

    Same pattern-tightening as check 3: only flag actual
    ``open("/etc/os-release")`` / ``Path("/etc/os-release")`` calls,
    not docstring mentions. The migration commits explain the move
    in prose; that prose stays.

    Exceptions: ``api/system.py`` exposes a richer os-release reader
    for the system-info endpoint (PRETTY_NAME, VARIANT_ID, etc.);
    that reader is the documented exception per design §B.
    """
    failures: list[str] = []
    pattern = re.compile(rb"""(open|Path|PosixPath)\s*\(\s*["']/etc/os-release""")
    allowed = {
        REPO_ROOT / "app" / "camera" / "camera_streamer" / "release_version.py",
        REPO_ROOT / "app" / "server" / "monitor" / "release_version.py",
        # Documented exception: server system-info endpoint reads
        # multiple os-release fields for the dashboard. We don't
        # collapse it into release_version() because release_version()
        # is intentionally narrow.
        REPO_ROOT / "app" / "server" / "monitor" / "api" / "system.py",
    }
    scan_roots = [
        REPO_ROOT / "app" / "camera" / "camera_streamer",
        REPO_ROOT / "app" / "server" / "monitor",
    ]
    for root in scan_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if path in allowed:
                continue
            data = path.read_bytes()
            if pattern.search(data):
                failures.append(
                    f"{path.relative_to(REPO_ROOT)}: opens /etc/os-release "
                    "directly. Either route through release_version() "
                    "(preferred) or add the file to the allowed-list in "
                    "scripts/check_versioning_design.py with a rationale."
                )
    return failures


# --- Check 5 — sw-description templates carry @@VERSION@@ ----------


def check_sw_description_templates_use_placeholder() -> list[str]:
    """The SWU manifest templates must use ``@@VERSION@@`` rather
    than a hardcoded version string. ``build-swu.sh`` substitutes
    the placeholder at build time from the VERSION file.
    """
    failures: list[str] = []
    for tpl in (
        REPO_ROOT / "swupdate" / "sw-description.camera",
        REPO_ROOT / "swupdate" / "sw-description.server",
    ):
        if not tpl.is_file():
            failures.append(f"missing template: {tpl}")
            continue
        text = tpl.read_text(encoding="utf-8")
        if "@@VERSION@@" not in text:
            failures.append(
                f"{tpl.name}: does not contain @@VERSION@@ placeholder. "
                "build-swu.sh would not be able to inject the release "
                "version. Re-add the placeholder per design §B."
            )
        # Catch hardcoded semver-shaped strings in the version field.
        m = re.search(r'^\s*version\s*=\s*"([^"]+)"\s*;\s*$', text, re.MULTILINE)
        if m and m.group(1) != "@@VERSION@@":
            failures.append(
                f"{tpl.name}: version field is hardcoded as "
                f'"{m.group(1)}" — must be "@@VERSION@@".'
            )
    return failures


# --- Check 6 — semver tag policy (informational, advisory) ---------


def check_release_sh_validates_semver() -> list[str]:
    """release.sh's validate_version must accept full semver
    including pre-release suffixes per design §E.
    """
    failures: list[str] = []
    rs = REPO_ROOT / "scripts" / "release.sh"
    if not rs.is_file():
        return [f"missing {rs}"]
    text = rs.read_text(encoding="utf-8")
    # Find the validate_version function and check its regex.
    m = re.search(
        r'validate_version\(\)\s*\{[^}]+?\[\[\s*"\$v"\s*=~\s*([^\]]+)\]\]',
        text,
        re.DOTALL,
    )
    if not m:
        # Couldn't introspect; not a hard fail.
        return []
    expr = m.group(1).strip()
    # Strict X.Y.Z regex `^[0-9]+\.[0-9]+\.[0-9]+$` is the legacy. Per
    # design §E we want pre-release support: optional ``-PRERELEASE`` after.
    if expr.endswith('+$"') or "-" in expr or "PRERELEASE" in expr:
        return []
    # Strict-only regex hit — advisory failure (won't block builds, just nudges).
    failures.append(
        f"{rs.name}: validate_version regex looks strict X.Y.Z only "
        f"(found {expr}). Per design §E, extend to allow "
        "`X.Y.Z(-PRERELEASE)?` for rc/dev tags. Not blocking, but "
        "outdated against the SSOT design."
    )
    return failures


# --- Check 7 — VERSION file matches CHANGELOG header ---------------


def check_version_matches_changelog() -> list[str]:
    """``VERSION`` must equal the latest ``## [X.Y.Z]`` heading in
    CHANGELOG.md (or the [Unreleased] entry must precede a [X.Y.Z]
    that matches VERSION).
    """
    failures: list[str] = []
    cl = REPO_ROOT / "CHANGELOG.md"
    if not cl.is_file():
        return [f"missing {cl}"]
    v = _read_version()
    text = cl.read_text(encoding="utf-8")
    # First versioned heading after [Unreleased].
    m = re.search(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", text, re.MULTILINE)
    if not m:
        return [f"{cl.name}: no `## [X.Y.Z]` heading found"]
    if m.group(1) != v:
        failures.append(
            f"VERSION ({v}) does not match the first CHANGELOG heading "
            f"`## [{m.group(1)}]`. Run `./scripts/release.sh prepare {v}` "
            "or hand-edit the CHANGELOG to match."
        )
    return failures


# --- Driver --------------------------------------------------------

CHECKS = [
    ("sw-versions recipe is templated", check_sw_versions_recipe_templated),
    (
        "release_version helper byte-identical in 3 places",
        check_release_version_helper_canonical,
    ),
    ("no app code reads /etc/sw-versions", check_no_app_reads_sw_versions),
    (
        "release_version is the only os-release reader (one-helper rule)",
        check_release_version_helper_is_only_reader,
    ),
    (
        "sw-description templates carry @@VERSION@@",
        check_sw_description_templates_use_placeholder,
    ),
    ("release.sh validates semver", check_release_sh_validates_semver),
    ("VERSION matches CHANGELOG", check_version_matches_changelog),
]


def main() -> int:
    total_failures = 0
    for label, fn in CHECKS:
        fails = fn()
        if fails:
            total_failures += len(fails)
            print(f"FAIL [{label}]")
            for f in fails:
                print(f"  - {f}")
        else:
            print(f"OK   [{label}]")
    if total_failures:
        print()
        print(
            f"check_versioning_design: {total_failures} failure(s). See "
            "docs/architecture/versioning.md for the policy."
        )
        return 1
    print()
    print("check_versioning_design: all guards green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
