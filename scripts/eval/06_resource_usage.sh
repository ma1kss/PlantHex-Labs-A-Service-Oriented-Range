#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

echo "=== Resource usage ($(date -Is)) ==="
echo "── docker stats (one-shot) ──"
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}' \
  $(docker ps -q --filter name=sor_) | sort

echo; echo "── SOR image sizes ──"
docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | grep -E '^sor/' | sort

echo; echo "── volume sizes ──"
docker system df -v 2>/dev/null | grep -E 'infrastructure_|VOLUME NAME' | awk '{printf "%-40s %s\n", $1, $NF}'

echo; echo "── totals ──"
echo "containers running: $(docker ps -q --filter name=sor_ | wc -l)"
echo "host memory:"
free -h | sed 's/^/  /'
echo "host load: $(cat /proc/loadavg)"
