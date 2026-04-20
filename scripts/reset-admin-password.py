#!/usr/bin/env python3
"""Reset an admin user's password from the device console.

Recovery path for a forgotten admin password (issue #100). Must be run
directly on the device — over SSH or an attached keyboard/monitor —
because it requires read/write access to /data/config/users.json.

Usage
-----

    sudo /opt/monitor/scripts/reset-admin-password.py \
        --username admin \
        --password 'temp-pass-12345'

The target user's password is rewritten with a fresh bcrypt hash at the
same cost factor as the running app (see monitor.auth.hash_password),
and ``must_change_password`` is set to ``true`` so the admin is forced
to pick a new password on their first login after this reset. The
temporary password you pass in never needs to be remembered long-term.

Security model
--------------

Anyone who can run this script can already ``sudo`` on the device —
which is the same boundary as ``rm -rf /data`` or ``systemctl stop``.
See ADR-0009: physical / SSH access = operator-trusted. The monitor
service does **not** need to be restarted; ``Store.get_user`` reads
``users.json`` from disk on each call, so the new hash takes effect
immediately.

Exit codes
----------

    0 — success
    1 — usage error (missing args, short password, etc.)
    2 — store file not found or unreadable
    3 — no admin user in the store, or the named user is not an admin
    4 — write failed (permissions, disk full, etc.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# All third-party imports are deferred to main() so ``--help`` works on
# a box where monitor isn't importable (fresh image, dependency issue).


def _find_app_dir() -> Path:
    """Return the directory containing the monitor app package.

    The script can live in /opt/monitor/scripts on the deployed image or
    in repo_root/scripts during development. Resolve by walking up
    until we find a directory with ``app/server/monitor`` or
    ``monitor``.
    """
    here = Path(__file__).resolve()
    # Deployed layout: /opt/monitor/scripts/reset-admin-password.py,
    # monitor package at /opt/monitor/monitor.
    for up in (here.parent.parent, here.parent.parent.parent):
        if (up / "monitor" / "__init__.py").is_file():
            return up
        if (up / "app" / "server" / "monitor" / "__init__.py").is_file():
            return up / "app" / "server"
    raise RuntimeError(
        "Could not locate the monitor package. Expected it at "
        "/opt/monitor/monitor/ (deployed) or app/server/monitor/ (repo)."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset an admin user's password on a running Home Monitor device.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Admin username to reset. Optional — if omitted and exactly one "
        "admin exists, that user is reset automatically. Required when more "
        "than one admin is present.",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Temporary password to install (minimum 12 characters). The user "
        "will be forced to change it on first login.",
    )
    parser.add_argument(
        "--store",
        default="/data/config",
        help="Directory containing users.json (default: /data/config).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing users.json.",
    )
    args = parser.parse_args()

    if len(args.password) < 12:
        print("error: --password must be at least 12 characters", file=sys.stderr)
        return 1

    store_dir = Path(args.store)
    users_path = store_dir / "users.json"
    if not users_path.is_file():
        print(f"error: {users_path} not found or not readable", file=sys.stderr)
        return 2

    # Import the monitor package via absolute path so the script works
    # out-of-tree (i.e. the working directory is not the repo root).
    try:
        app_dir = _find_app_dir()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(app_dir))

    try:
        from monitor.auth import hash_password  # type: ignore[import-not-found]
        from monitor.store import Store  # type: ignore[import-not-found]
    except ImportError as exc:
        print(f"error: failed to import monitor package: {exc}", file=sys.stderr)
        return 2

    # Same Store instance the app uses — atomic writes, same on-disk format.
    store = Store(data_dir=str(store_dir))

    admins = [u for u in store.get_users() if u.role == "admin"]
    if not admins:
        print("error: no admin user present in users.json", file=sys.stderr)
        return 3

    if args.username:
        target = next((u for u in admins if u.username == args.username), None)
        if target is None:
            names = ", ".join(u.username for u in admins)
            print(
                f"error: no admin user named {args.username!r}. "
                f"Admin users present: {names}",
                file=sys.stderr,
            )
            return 3
    else:
        if len(admins) > 1:
            names = ", ".join(u.username for u in admins)
            print(
                f"error: multiple admin users present ({names}); "
                "pass --username to choose one.",
                file=sys.stderr,
            )
            return 3
        target = admins[0]

    print(f"Resetting password for admin user {target.username!r} (id={target.id})")
    if args.dry_run:
        print("--dry-run set; no changes written.")
        return 0

    target.password_hash = hash_password(args.password)
    target.must_change_password = True
    try:
        store.save_user(target)
    except OSError as exc:
        print(f"error: failed to write users.json: {exc}", file=sys.stderr)
        return 4

    # Best-effort audit line — write directly to the audit file the app
    # reads. If the file isn't present yet we skip; the event is less
    # important than the reset itself. The running app ingests this on
    # the next poll.
    try:
        from datetime import UTC, datetime

        audit_path = store_dir / "audit.log"
        line = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "PASSWORD_RESET_VIA_CLI",
            "user": "cli",
            "ip": "local",
            "detail": f"admin password reset for user {target.id} via reset-admin-password.py",
        }
        import json as _json

        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(line) + "\n")
    except Exception as exc:  # pragma: no cover
        print(f"warning: failed to write audit line: {exc}", file=sys.stderr)

    print(
        f"OK — {target.username!r} must change their password on next login.\n"
        "Temporary password is only valid once; the change-password flow "
        "will run automatically when they sign in."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
