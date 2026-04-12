"""
Password policy enforcement (ADR-0011, NIST SP 800-63B).

Rules:
- Minimum 12 characters (no maximum below 64)
- No composition rules (no forced uppercase/number/symbol)
- Blocked against top 10,000 breached passwords
- Allow spaces and Unicode
"""

import os

_blocklist: set[str] | None = None

# Bundled blocklist file (top 10K most common passwords)
_BLOCKLIST_PATH = os.path.join(
    os.path.dirname(__file__), "data", "password-blocklist.txt"
)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def _load_blocklist() -> set[str]:
    """Load the password blocklist from disk. Lazy-loaded once."""
    global _blocklist
    if _blocklist is not None:
        return _blocklist
    _blocklist = set()
    if os.path.isfile(_BLOCKLIST_PATH):
        with open(_BLOCKLIST_PATH, encoding="utf-8", errors="ignore") as f:
            for line in f:
                pw = line.strip().lower()
                if pw:
                    _blocklist.add(pw)
    return _blocklist


def validate_password(password: str) -> str:
    """Validate a password against the policy.

    Returns empty string if valid, error message if invalid.
    """
    if not password:
        return "Password is required"

    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"

    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters"

    # Check against breached password blocklist
    blocklist = _load_blocklist()
    if password.lower() in blocklist:
        return "This password is too common. Choose a different one"

    return ""
