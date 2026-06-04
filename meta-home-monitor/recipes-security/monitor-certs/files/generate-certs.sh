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
SERVER_CHAIN_CERT="$CERTS_DIR/server-browser-chain.crt"
SERVER_CSR="$CERTS_DIR/server.csr"
SERVER_LOCAL_CA_CERT="$CERTS_DIR/server-ca-local.crt"
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
    for cert in "$CA_CERT" "$SERVER_CERT" "$SERVER_CHAIN_CERT" "$SERVER_LOCAL_CA_CERT" "$GUI_SERVER_CERT"; do
        [ -f "$cert" ] || continue
        chmod 0644 "$cert"
    done
}

write_server_san_ext() {
    san_ext="$1"
    san="DNS:home-monitor,DNS:home-monitor.local,DNS:rpi-divinu,DNS:rpi-divinu.local,DNS:localhost,IP:127.0.0.1,IP:192.168.4.1"
    for ip in $(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1); do
        case "$ip" in
            127.*|169.254.*|*:*) continue ;;
            *.*) san="$san,IP:$ip" ;;
        esac
    done
    {
        printf "subjectAltName=%s\n" "$san"
        printf "extendedKeyUsage=serverAuth\n"
    } > "$san_ext"
}

build_browser_chain() {
    [ -f "$SERVER_CERT" ] || return 0
    tmp_chain="$SERVER_CHAIN_CERT.tmp"
    if [ -f "$SERVER_LOCAL_CA_CERT" ]; then
        cat "$SERVER_CERT" "$SERVER_LOCAL_CA_CERT" > "$tmp_chain"
    else
        cp "$SERVER_CERT" "$tmp_chain"
    fi
    mv "$tmp_chain" "$SERVER_CHAIN_CERT"
    chmod 0644 "$SERVER_CHAIN_CERT"
}

server_cert_has_required_sans() {
    [ -f "$SERVER_CERT" ] || return 1
    sans="$(openssl x509 -in "$SERVER_CERT" -noout -ext subjectAltName 2>/dev/null || true)"
    printf "%s" "$sans" | grep -q "DNS:rpi-divinu.local" || return 1
    printf "%s" "$sans" | grep -q "DNS:rpi-divinu" || return 1
    printf "%s" "$sans" | grep -q "IP Address:192.168.4.1" || return 1
    return 0
}

generate_server_certificate() {
    [ -f "$SERVER_KEY" ] || openssl ecparam -genkey -name prime256v1 -out "$SERVER_KEY"
    set_server_key_permissions

    openssl req -new -key "$SERVER_KEY" -out "$SERVER_CSR" \
        -subj "/CN=rpi-divinu/O=HomeMonitor"

    SAN_EXT="$CERTS_DIR/san.cnf"
    write_server_san_ext "$SAN_EXT"

    openssl x509 -req -in "$SERVER_CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" \
        -CAcreateserial -out "$SERVER_CERT" -days 365 \
        -sha256 -extfile "$SAN_EXT"

    rm -f "$SERVER_CSR" "$SAN_EXT"
    chmod 0644 "$SERVER_CERT"
    build_browser_chain
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
    if ! server_cert_has_required_sans; then
        echo "Server certificate is missing required LAN/browser SANs; regenerating."
        generate_server_certificate
    else
        build_browser_chain
    fi
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

# Generate server private key and cert with browser/camera SANs.
generate_server_certificate

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
