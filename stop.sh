#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose -f infrastructure/docker-compose.yml"

if [[ "${1:-}" == "--wipe" ]]; then
    echo "==> stopping SOR stack and wiping volumes"
    $COMPOSE down -v
    echo "==> done. All data is gone."
else
    echo "==> stopping SOR stack (volumes preserved)"
    $COMPOSE down
    echo "==> done. Volumes kept — run ./start.sh again to resume."
fi
