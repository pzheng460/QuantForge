#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT="$ROOT/.keys/localhost-cert.pem"
KEY="$ROOT/.keys/localhost-key.pem"

mkdir -p "$ROOT/.keys"
chmod 700 "$ROOT/.keys"

if [ -s "$CERT" ] && [ -s "$KEY" ]; then
    echo "Local HTTPS certificate already exists."
    exit 0
fi

openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
    -keyout "$KEY" \
    -out "$CERT" \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    >/dev/null 2>&1

chmod 600 "$KEY" "$CERT"
echo "Created local HTTPS certificate for localhost and 127.0.0.1."
