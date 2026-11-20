#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose -f infrastructure/docker-compose.yml"

# Self-signed TLS pair for the web edge (the portal serves HTTPS when present).
CERT_DIR="infrastructure/certs"
if [[ ! -f "$CERT_DIR/server.crt" || ! -f "$CERT_DIR/server.key" ]]; then
    echo "==> generating self-signed TLS certificate in $CERT_DIR"
    mkdir -p "$CERT_DIR"
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
        -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.crt" \
        -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
fi

# Keys and passwords (RS256 token keys, per-service event-signing keys,
# event/flag encryption keys, per-service DB passwords) — created once,
# rotated with scripts/security/gen_secrets.sh --rotate.
scripts/security/gen_secrets.sh

if [[ "${1:-}" == "--rebuild" ]]; then
    echo "==> rebuilding all images from scratch"
    $COMPOSE build --no-cache
fi

echo "==> starting SOR stack"
$COMPOSE up -d --build

echo
echo "==> waiting for containers to settle"
sleep 4

echo
$COMPOSE ps

cat <<'EOF'

──────────────────────────────────────────────────────────────────
SOR is up.

  Web UI       https://localhost:8000/  or  https://localhost:8443/
               (self-signed cert — accept the browser warning)
  Operator UIs (bound to 127.0.0.1 only — not reachable from the network
  or from exercise instances):
  Consul UI    http://localhost:8500/ui
  RabbitMQ UI  http://localhost:15672      (sor_admin / infrastructure/secrets/rmq_admin.pw)
  Portainer    http://localhost:9000
  Prometheus   http://localhost:9090       Grafana http://localhost:3000

  Logs:    docker logs -f sor_iam     (or any sor_* container)
  Stop:    ./stop.sh
──────────────────────────────────────────────────────────────────
EOF
