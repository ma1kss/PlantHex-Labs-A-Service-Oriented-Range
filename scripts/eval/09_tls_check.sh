#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
HOSTPORT="${HOSTPORT:-localhost:8443}"

echo "=== TLS / edge security ($(date -Is)) ==="
echo "── certificate ──"
echo | openssl s_client -connect "$HOSTPORT" 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -fingerprint -sha256 2>/dev/null \
  || echo "TLS handshake FAILED on $HOSTPORT"

echo; echo "── negotiated protocol/cipher ──"
echo | openssl s_client -connect "$HOSTPORT" 2>/dev/null \
  | grep -E '^(Protocol|Cipher| +Protocol| +Cipher)' | head -4

echo; echo "── handshake overhead (10 samples) ──"
for _ in $(seq 10); do
  curl -sk -o /dev/null -w '%{time_appconnect}\n' "https://$HOSTPORT/health"
done | percentiles

echo; echo "── edge auth enforcement (expect 401s) ──"
for path in /iam/users /learning/participants /scenario/sessions /events/announcements; do
  code=$($CURL -o /dev/null -w '%{http_code}' "$SOR_BASE$path")
  printf "  %-28s no-token → HTTP %s\n" "$path" "$code"
done

echo; echo "── plaintext HTTP on the TLS port is rejected (expect failure) ──"
curl -s -o /dev/null -m 5 "http://$HOSTPORT/health" && echo "  UNEXPECTED: plaintext worked" \
  || echo "  ok — plaintext refused"
