#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
K="${K:-3}"; MODE="${MODE:-restart}"
EXPECTED_TARGETS="${EXPECTED_TARGETS:-9}"   # 8 services + Prometheus itself
COMPOSE="docker compose -f ../../infrastructure/docker-compose.yml"
EXPECTED_SVCS="consumer-portal-service data-logging-service events-service iam-service learning-service monitoring-service orchestration-service scenario-service"

echo "=== Redeploy repeatability: K=$K, MODE=$MODE ($(date -Is)) ==="
hr
OK_CYCLES=0; READY_MS=$(mktemp)
for i in $(seq "$K"); do
  if [ "$MODE" = clean ]; then $COMPOSE down -v >/dev/null 2>&1; else $COMPOSE down >/dev/null 2>&1; fi
  T0=$(now_ms)
  $COMPOSE up -d >/dev/null 2>&1 || { echo "cycle $i: compose up FAILED"; continue; }
  # Wait (max 240 s) until: no container starting/unhealthy, catalogue complete
  # and passing, all Prometheus targets up.
  READY=""; CAT=""; TGT=""
  for _ in $(seq 240); do
    NOTOK=$($COMPOSE ps --format '{{.Name}} {{.State}} {{.Health}}' 2>/dev/null \
            | awk '$2!="running" || ($3!="" && $3!="healthy")' | wc -l)
    CAT=$(curl -s localhost:8500/v1/health/state/passing | python3 -c '
import sys,json; print(" ".join(sorted({c["ServiceName"] for c in json.load(sys.stdin) if c["ServiceName"]})))' 2>/dev/null)
    REG=$(curl -s localhost:8500/v1/catalog/services | python3 -c 'import sys,json;print("consul" in json.load(sys.stdin))' 2>/dev/null)
    TGT=$(curl -s localhost:9090/api/v1/targets | python3 -c '
import sys,json; t=json.load(sys.stdin)["data"]["activeTargets"]; print("%d/%d" % (sum(1 for x in t if x.get("health")=="up"), len(t)))' 2>/dev/null)
    missing=0; for s in $EXPECTED_SVCS; do echo " $CAT " | grep -q " $s " || missing=$((missing+1)); done
    up=${TGT%/*}; all=${TGT#*/}
    if [ "$NOTOK" = 0 ] && [ "$missing" = 0 ] && [ "$REG" = True ] && [ -n "$TGT" ] && [ "$up" = "$all" ] && [ "$all" -ge "$EXPECTED_TARGETS" ]; then
      READY=$(now_ms); break
    fi
    sleep 1
  done
  if [ -z "$READY" ]; then
    echo "cycle $i: NOT READY within 240 s (catalogue: $CAT; targets: $TGT)"; continue
  fi
  echo "$((READY - T0))" >> "$READY_MS"
  ADMIN_TOK=""
  if bash 12_functional_smoke.sh >/tmp/sor_smoke_$$.txt 2>&1; then SMOKE=PASS; else SMOKE=FAIL; fi
  SUM=$(grep -iE "passed|summary" /tmp/sor_smoke_$$.txt | tail -1)
  grep -E "^FAIL" /tmp/sor_smoke_$$.txt | sed 's/^/    /'; rm -f /tmp/sor_smoke_$$.txt
  NC=$($COMPOSE ps -q | wc -l)
  echo "cycle $i: ready in $((READY - T0)) ms · containers $NC healthy · catalogue: registry + $(echo $CAT | wc -w) services passing · prometheus targets $TGT up · smoke $SMOKE ($SUM)"
  [ "$SMOKE" = PASS ] && OK_CYCLES=$((OK_CYCLES+1))
done
hr
echo -n "up → all-ready : "; meansd < "$READY_MS"
echo "successful cycles (all checks + smoke suite): $OK_CYCLES / $K   [mode=$MODE]"
rm -f "$READY_MS"
