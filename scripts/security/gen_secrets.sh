#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../infrastructure"
D=secrets
ROTATE=0; [ "${1:-}" = "--rotate" ] && ROTATE=1
mkdir -p "$D/evt_pub"
umask 077

SERVICES="iam-service scenario-service events-service monitoring-service data-logging-service learning-service orchestration-service consumer-portal-service"
DB_SERVICES="iam scenario events learning orchestration"

need() { [ "$ROTATE" = 1 ] || [ ! -s "$1" ]; }

if need "$D/jwt_private.pem"; then
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$D/jwt_private.pem" 2>/dev/null
  openssl pkey -in "$D/jwt_private.pem" -pubout -out "$D/jwt_public.pem"
  echo "jwt: new RS256 key pair"
fi

for s in $SERVICES; do
  if need "$D/evt_$s.pem"; then
    openssl genpkey -algorithm ed25519 -out "$D/evt_$s.pem"
    openssl pkey -in "$D/evt_$s.pem" -pubout -out "$D/evt_pub/$s.pub"
    echo "events: new Ed25519 signing key for $s"
  fi
done

if need "$D/event_enc.key"; then
  openssl rand -base64 32 > "$D/event_enc.key"; echo "events: new AES-256-GCM key"
fi

# Fernet keys: newest first; on rotation the previous key stays as 2nd line so
# the scenario service can still decrypt and re-encrypt existing flags.
new_fernet() { openssl rand 32 | base64 | tr '+/' '-_'; }
if [ ! -s "$D/flag_keys" ]; then
  new_fernet > "$D/flag_keys"; echo "flags: new Fernet key"
elif [ "$ROTATE" = 1 ]; then
  { new_fernet; head -1 "$D/flag_keys"; } > "$D/flag_keys.new" && mv "$D/flag_keys.new" "$D/flag_keys"
  echo "flags: rotated Fernet key (previous kept for re-encryption)"
fi

RMQ_USERS="iam scenario events monitoring data learning orchestration portal admin"
for s in $RMQ_USERS; do
  if need "$D/rmq_$s.pw"; then
    openssl rand -hex 24 > "$D/rmq_$s.pw"; echo "broker: new password for $( [ $s = admin ] && echo sor_admin || echo svc_$s )"
  fi
done

for s in $DB_SERVICES; do
  if need "$D/db_$s.pw"; then
    openssl rand -hex 24 > "$D/db_$s.pw"; echo "db: new password for svc_$s"
  fi
done

# Containers run as root and read the files through bind mounts; keep them
# owner-only on the host.
chmod 600 "$D"/*.pem "$D"/*.key "$D"/flag_keys "$D"/db_*.pw "$D"/rmq_*.pw 2>/dev/null || true
chmod 644 "$D"/jwt_public.pem "$D"/evt_pub/*.pub
date -u +%Y-%m-%dT%H:%M:%SZ > "$D/.generated_at"
echo "secrets ready in infrastructure/$D ($( [ $ROTATE = 1 ] && echo rotated || echo ensured ))"
