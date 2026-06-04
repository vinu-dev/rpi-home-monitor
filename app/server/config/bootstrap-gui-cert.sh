#!/bin/sh
# Ensure nginx has browser-facing GUI TLS files without touching MediaMTX.
set -eu

CERTS_DIR="${MONITOR_CERTS_DIR:-/data/certs}"
GUI_CERT="$CERTS_DIR/gui-server.crt"
GUI_KEY="$CERTS_DIR/gui-server.key"
SERVER_CERT="$CERTS_DIR/server.crt"
SERVER_KEY="$CERTS_DIR/server.key"

mkdir -p "$CERTS_DIR"

if [ ! -f "$GUI_CERT" ] && [ -f "$SERVER_CERT" ]; then
    cp "$SERVER_CERT" "$GUI_CERT"
    chmod 0644 "$GUI_CERT"
fi

if [ ! -f "$GUI_KEY" ] && [ -f "$SERVER_KEY" ]; then
    cp "$SERVER_KEY" "$GUI_KEY"
    chmod 0600 "$GUI_KEY"
fi

chown monitor:monitor "$GUI_CERT" "$GUI_KEY" 2>/dev/null || true
