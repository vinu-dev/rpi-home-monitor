#!/bin/sh
# REQ: SWR-034, SWR-043; RISK: RISK-002, RISK-019; SEC: SC-017; TEST: TC-032, TC-040
# =============================================================
# generate-certs.sh — First-boot CA and server certificate setup
#
# Called on first boot when /data/certs/ca.crt does not exist.
# Creates a local Certificate Authority and server TLS certificate.
# =============================================================
set -e

CERTS_DIR="/data/certs"
CONFIG_DIR="/data/config"
MONITOR_GROUP="${MONITOR_GROUP:-monitor}"
CA_KEY="$CERTS_DIR/ca.key"
CA_CERT="$CERTS_DIR/ca.crt"
SERVER_KEY="$CERTS_DIR/server.key"
SERVER_CERT="$CERTS_DIR/server.crt"
SERVER_CSR="$CERTS_DIR/server.csr"
GUI_SERVER_KEY="$CERTS_DIR/gui-server.key"
GUI_SERVER_CERT="$CERTS_DIR/gui-server.crt"

set_server_key_permissions() {
    [ -f "$SERVER_KEY" ] || return 0
    chown "root:$MONITOR_GROUP" "$SERVER_KEY" 2>/dev/null || true
    chmod 0640 "$SERVER_KEY"
}

set_ca_key_permissions() {
    [ -f "$CA_KEY" ] || return 0
    chown root:root "$CA_KEY" 2>/dev/null || true
    chmod 0600 "$CA_KEY"
}

set_gui_key_permissions() {
    [ -f "$GUI_SERVER_KEY" ] || return 0
    chown monitor:monitor "$GUI_SERVER_KEY" 2>/dev/null || true
    chmod 0600 "$GUI_SERVER_KEY"
}

set_public_cert_permissions() {
    for cert in "$CA_CERT" "$SERVER_CERT" "$GUI_SERVER_CERT"; do
        [ -f "$cert" ] || continue
        chmod 0644 "$cert"
    done
}

repair_cert_permissions() {
    mkdir -p "$CERTS_DIR/cameras" "$CERTS_DIR/status" "$CONFIG_DIR/nginx-client-cert.d"
    chown monitor:monitor "$CERTS_DIR" "$CERTS_DIR/cameras" "$CERTS_DIR/status" 2>/dev/null || true
    chmod 0750 "$CERTS_DIR" "$CERTS_DIR/cameras" "$CERTS_DIR/status"
    set_ca_key_permissions
    set_server_key_permissions
    set_gui_key_permissions
    set_public_cert_permissions
}

# Only generate if CA doesn't exist yet. Existing certs still get their
# ownership/modes repaired on every boot before nginx, MediaMTX, and monitor.
if [ -f "$CA_CERT" ]; then
    echo "Certificates already exist, repairing permissions."
    repair_cert_permissions
    exit 0
fi

echo "Generating local CA and server certificates..."

mkdir -p "$CERTS_DIR/cameras" "$CONFIG_DIR/nginx-client-cert.d"

# Generate CA private key
openssl ecparam -genkey -name prime256v1 -out "$CA_KEY"
set_ca_key_permissions

# Generate self-signed CA certificate (10 years)
openssl req -new -x509 -key "$CA_KEY" -out "$CA_CERT" \
    -days 3650 -subj "/CN=HomeMonitor CA/O=HomeMonitor"

# Generate server private key
openssl ecparam -genkey -name prime256v1 -out "$SERVER_KEY"
set_server_key_permissions

# Generate server CSR
openssl req -new -key "$SERVER_KEY" -out "$SERVER_CSR" \
    -subj "/CN=home-monitor/O=HomeMonitor"

# Create SAN extension file
SAN_EXT="$CERTS_DIR/san.cnf"
printf "subjectAltName=DNS:home-monitor,DNS:home-monitor.local,DNS:localhost,IP:127.0.0.1\n" > "$SAN_EXT"

# Sign server cert with CA (1 year)
openssl x509 -req -in "$SERVER_CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$SERVER_CERT" -days 365 \
    -extfile "$SAN_EXT"

# Cleanup temporary files
rm -f "$SERVER_CSR" "$SAN_EXT"

# Browser-facing nginx cert starts as a copy of the existing server cert.
# Later setup can replace gui-server.crt/key with a local-CA-signed GUI cert
# without touching MediaMTX/camera trust on server.crt/key.
cp "$SERVER_CERT" "$GUI_SERVER_CERT"
cp "$SERVER_KEY" "$GUI_SERVER_KEY"
repair_cert_permissions

echo "Certificates generated successfully:"
echo "  CA:     $CA_CERT"
echo "  Server: $SERVER_CERT"
echo "  GUI:    $GUI_SERVER_CERT"
