"""Single image-side release-version helper for camera + server.

Both Yocto recipes (camera-streamer, monitor-server) copy this file
into their own package's namespace at build time:

  /opt/camera/camera_streamer/release_version.py    (camera image)
  /opt/monitor/monitor/release_version.py            (server image)

Source of truth: this single file. The CI guard
``scripts/check_versioning_design.py`` asserts both recipes install
it from the canonical location and that no second copy with diverged
content exists.

The runtime read path is intentionally minimal:

  /etc/os-release VERSION_ID
        ↑   (templated by Yocto's os-release.bbappend from
            ${DISTRO_VERSION}, which itself reads the repo-root
            ``VERSION`` file at build time)

This is the image-side single source of truth chosen in
``docs/architecture/versioning.md`` §C. Older images shipped a
hardcoded ``/etc/sw-versions`` and got it wrong on every
fresh-flashed prod card; this helper points at the right file.

The function is module-level so the same import works in both
``camera_streamer`` and ``monitor`` packages with no plumbing.
The result is cached on first read — release version doesn't change
during a process lifetime, and the file is on the rootfs (no IO
hit worth re-paying on every heartbeat).

Reset behaviour: ``_clear_cache()`` is exposed for tests and for
SIGHUP/reload paths if any caller eventually wants to refresh
without restarting the process. Production code should not call it.
"""

from __future__ import annotations

import threading

__all__ = ["_clear_cache", "release_version"]

_OS_RELEASE_PATH = "/etc/os-release"
_KEY = "VERSION_ID"

_lock = threading.Lock()
_cached: str | None = None


def _parse(path: str) -> str:
    """Parse os-release(5) and return the value of ``VERSION_ID``.

    os-release has a deceptively simple shell-assignment grammar:
    ``KEY=value`` per line, value may be quoted with single or double
    quotes. We don't honour escape sequences (the file is generated
    by Yocto, never hand-edited; values are trivially ASCII).
    Returns empty string on any failure so callers can render
    ``"unknown"`` instead of crashing.
    """
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if not sep or key != _KEY:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                return value
    except OSError:
        return ""
    return ""


def release_version(os_release_path: str | None = None) -> str:
    """Return the product release version (e.g. ``"1.4.3"``).

    Reads ``/etc/os-release VERSION_ID`` once and caches the result.
    Returns an empty string when the file is missing or malformed —
    callers display ``"unknown"`` rather than failing loudly, mirroring
    the older ``_get_firmware_version`` semantics this replaces.

    Tests pass an explicit path to bypass the cache.
    """
    global _cached
    if os_release_path is not None:
        # Test mode — uncached read against an explicit path.
        return _parse(os_release_path)
    if _cached is not None:
        return _cached
    with _lock:
        if _cached is None:
            _cached = _parse(_OS_RELEASE_PATH)
    return _cached


def _clear_cache() -> None:
    """Reset the cached value. For tests and SIGHUP-style reloads."""
    global _cached
    with _lock:
        _cached = None
