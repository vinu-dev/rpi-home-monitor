# REQ: SWR-069; RISK: RISK-020; SEC: SC-025; TEST: TC-055
"""Secret-redaction helpers for diagnostics and other export surfaces."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class _RedactPaths:
    users: tuple[str, ...] = (
        "users[*].password_hash",
        "users[*].totp_secret",
        "users[*].recovery_code_hashes",
    )
    cameras: tuple[str, ...] = ("cameras[*].pairing_secret",)
    settings: tuple[str, ...] = (
        "tailscale_auth_key",
        "webhook_destinations[*].secret",
        "webhook_destinations[*].custom_headers",
        "offsite_backup_access_key_id",
        "offsite_backup_secret_access_key",
    )


REDACT_PATHS = _RedactPaths()


def redact_secrets(obj, paths: list[str] | tuple[str, ...]):
    """Return a deep-copied structure with secret leaves replaced."""

    redacted = deepcopy(obj)
    for path in paths:
        _apply_path(redacted, _parse_path(path))
    return redacted


def _apply_path(current, segments: list[tuple[str, bool]]) -> None:
    if not segments:
        return

    key, is_wildcard = segments[0]
    rest = segments[1:]

    if not isinstance(current, dict) or key not in current:
        return

    value = current[key]
    if is_wildcard:
        if not isinstance(value, list):
            return
        if not rest:
            current[key] = REDACTED
            return
        for item in value:
            _apply_path(item, rest)
        return

    if not rest:
        current[key] = REDACTED
        return

    _apply_path(value, rest)


def _parse_path(path: str) -> list[tuple[str, bool]]:
    segments: list[tuple[str, bool]] = []
    for chunk in path.split("."):
        if chunk.endswith("[*]"):
            segments.append((chunk[:-3], True))
        else:
            segments.append((chunk, False))
    return segments
