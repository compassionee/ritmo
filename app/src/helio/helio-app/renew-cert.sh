#!/bin/bash
# Renews the Tailscale certificate used by Helio on the home network and restarts the service if it changed.
set -euo pipefail
NAME=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tailscale cert --cert-file "$TMP/tls.crt" --key-file "$TMP/tls.key" "$NAME" >/dev/null
if ! cmp -s "$TMP/tls.crt" /etc/helio/tls.crt 2>/dev/null; then
  install -m 644 -o root -g root "$TMP/tls.crt" /etc/helio/tls.crt
  install -m 640 -o root -g nogroup "$TMP/tls.key" /etc/helio/tls.key
  systemctl restart helio-app
  echo "Certificate updated for $NAME"
fi
