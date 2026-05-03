#!/usr/bin/env bash
# Start Agentry against this target repository.
#
# Runs in foreground until you Ctrl-C or close the terminal. There is no
# systemd install — every reboot, you run this script again.
#
# On first run, this script creates a local Python venv at
# <target>/agentry/.venv/ and pip-installs agentry into it. On subsequent
# runs it just activates the venv and starts the orchestrator.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
TARGET_ROOT="$(dirname "$SCRIPT_DIR")"
VENV="$SCRIPT_DIR/.venv"

# Locate Python.
PYTHON=""
for name in python3 python; do
    if command -v "$name" >/dev/null 2>&1; then
        PYTHON="$(command -v "$name")"
        break
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "Python not found on PATH."
    echo "Run scripts/install-deps.sh from the agentry repo first:"
    echo "  curl -fsSL https://raw.githubusercontent.com/vinu-dev/agentry/main/scripts/install-deps.sh | bash"
    exit 1
fi

# Create venv on first run; pip-install agentry into it.
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "==> First-time setup: creating venv at $VENV"
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    echo "==> Installing agentry from GitHub"
    "$VENV/bin/python" -m pip install 'git+https://github.com/vinu-dev/agentry.git'
    echo "==> Setup complete"
fi

if [[ ! -x "$VENV/bin/agentry" ]]; then
    echo "agentry binary not found at $VENV/bin/agentry — venv may be corrupted"
    echo "Delete agentry/.venv and re-run this script."
    exit 1
fi

echo "==> Starting agentry against $TARGET_ROOT"
exec "$VENV/bin/agentry" start --target "$TARGET_ROOT"
