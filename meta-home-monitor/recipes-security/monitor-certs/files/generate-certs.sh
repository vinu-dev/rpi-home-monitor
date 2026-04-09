#!/bin/sh
# =============================================================
# generate-certs.sh — First-boot CA and server certificate setup
#
# Called on first boot when /data/certs/ca.crt does not exist.
# Creates a local Certificate Authority and server TLS certificate.
# =============================================================
set -e

CERTS_DIR="/data/certs"
CA_KEY="$CERTS_DIR/ca.key"
CA_CERT="$CERTS_DIR/ca.crt"
SERVER_KEY="$CERTS_DIR/server.key"
SERVER_CERT="$CERTS_DIR/server.crt"
SERVER_CSR="$CERTS_DIR/server.csr"

# Only run if CA doesn't exist yet (first boot)
if [ -f "$CA_CERT" ]; then
    echo "Certificates already exist, skipping generation."
    exit 0
fi

echo "Generating local CA and server certificates..."

mkdir -p "$CERTS_DIR/cameras"

# Generate CA private key
openssl ecparam -genkey -name prime256v1 -out "$CA_KEY"
chmod 600 "$CA_KEY"

# Generate self-signed CA certificate (10 years)
openssl req -new -x509 -key "$CA_KEY" -out "$CA_CERT" \
    -days 3650 -subj "/CN=HomeMonitor CA/O=HomeMonitor"

# Generate server private key
openssl ecparam -genkey -name prime256v1 -out "$SERVER_KEY"
chmod 600 "$SERVER_KEY"

# Generate server CSR
openssl req -new -key "$SERVER_KEY" -out "$SERVER_CSR" \
    -subj "/CN=home-monitor/O=HomeMonitor"

# Create SAN extension file
SAN_EXT="$CERTS_DIR/san.cnf"
printf "subjectAltName=DNS:home-monitor,DNS:localhost,IP:127.0.0.1\n" > "$SAN_EXT"

# Sign server cert with CA (1 year)
openssl x509 -req -in "$SERVER_CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$SERVER_CERT" -days 365 \
    -extfile "$SAN_EXT"

# Cleanup temporary files
rm -f "$SERVER_CSR" "$SAN_EXT"

echo "Certificates generated successfully:"
echo "  CA:     $CA_CERT"
echo "  Server: $SERVER_CERT"
