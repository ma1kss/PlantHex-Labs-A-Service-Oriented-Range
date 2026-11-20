#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
admin_token
AUTH=(-H "Authorization: Bearer $ADMIN_TOK")

echo "=== Service health ($(date -Is)) ==="
hr
printf "%-16s %-28s %-8s\n" "SERVICE" "HEALTH" "READY"
for p in iam scenario events monitoring data learning orchestration; do
  h=$($CURL -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$SOR_BASE/$p/health" || echo ERR)
  r=$($CURL "${AUTH[@]}" "$SOR_BASE/$p/ready" | pyjson '"ok" if d.get("ready") else "NOT-READY: "+str({k:v for k,v in d.get("checks",{}).items() if v!="ok"})' 2>/dev/null || echo "?")
  printf "%-16s %-28s %-8s\n" "$p" "HTTP $h" "$r"
done
h=$($CURL -o /dev/null -w '%{http_code}' "$SOR_BASE/health" || echo ERR)
printf "%-16s %-28s\n" "portal (edge)" "HTTP $h"

hr; echo "Consul catalog (via edge):"
$CURL "$SOR_BASE/api/services" | pyjson '", ".join(sorted(d))'

hr; echo "Container states:"
docker ps -a --filter name=sor_ --format '{{.Names}}\t{{.Status}}' | sort

hr; echo "Unhealthy / restarting containers (empty = good):"
docker ps -a --filter name=sor_ --format '{{.Names}}\t{{.Status}}' \
  | grep -Ei 'unhealthy|restarting|exited' || echo "(none)"
