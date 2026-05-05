#!/usr/bin/env bash
# Start Agentry against this target repository.
#
# Runs in foreground until you Ctrl-C or close the terminal. There is no
# systemd install - every reboot, you run this script again.
#
# On first run, this script creates a local Python venv at
# <target>/agentry/.venv/ and pip-installs agentry into it. On subsequent
# runs it just activates the venv and starts the orchestrator.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
TARGET_ROOT="$(dirname "$SCRIPT_DIR")"
VENV="$SCRIPT_DIR/.venv"
INSTALL_REF_FILE="$VENV/.agentry-install-ref"
AGENTRY_REPO="https://github.com/vinu-dev/agentry.git"
AGENTRY_REF="${AGENTRY_INSTALL_REF:-f89a32ca6d73504678cb05147aecdb5986139658}"
AGENTRY_BIN="$VENV/bin/agentry"
FORCE_INSTALL="${AGENTRY_FORCE_INSTALL:-}"

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

# Create venv on first run; reinstall agentry when the pinned ref changes.
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "==> First-time setup: creating venv at $VENV"
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
fi

INSTALLED_REF=""
if [[ -f "$INSTALL_REF_FILE" ]]; then
    INSTALLED_REF="$(tr -d '[:space:]' < "$INSTALL_REF_FILE")"
fi
if [[ "$INSTALLED_REF" != "$AGENTRY_REF" ]]; then
    if [[ "$#" -gt 0 && -x "$AGENTRY_BIN" && "$FORCE_INSTALL" != "1" && "$FORCE_INSTALL" != "true" && "$FORCE_INSTALL" != "TRUE" && "$FORCE_INSTALL" != "yes" && "$FORCE_INSTALL" != "YES" ]]; then
        echo "==> Agentry install ref is missing or different; using existing venv for this CLI command."
        echo "==> Stop Agentry and set AGENTRY_FORCE_INSTALL=1 to refresh the venv to $AGENTRY_REF."
    else
        echo "==> Installing agentry from GitHub at $AGENTRY_REF"
        "$VENV/bin/python" -m pip install --upgrade --force-reinstall "git+$AGENTRY_REPO@$AGENTRY_REF"
        printf '%s\n' "$AGENTRY_REF" > "$INSTALL_REF_FILE"
        echo "==> Agentry install complete"
    fi
fi

if [[ ! -x "$AGENTRY_BIN" ]]; then
    echo "agentry binary not found at $AGENTRY_BIN - venv may be corrupted"
    echo "Delete agentry/.venv and re-run this script."
    exit 1
fi

echo "==> Starting agentry against $TARGET_ROOT"
if [[ "$#" -gt 0 ]]; then
    echo "==> Running agentry $*"
    exec "$AGENTRY_BIN" "$@"
fi

echo "==> Running doctor"
"$AGENTRY_BIN" doctor --target "$TARGET_ROOT"
exec "$AGENTRY_BIN" start --target "$TARGET_ROOT"
