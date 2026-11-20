#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
RUN="scripts/pipeline_runs/$(date -u +%Y%m%d_%H%M%S)"; mkdir -p "$RUN"
COMPOSE="docker compose -f infrastructure/docker-compose.yml"
T0=$(date +%s)

stage() {  # stage <n> <name> <command...>
  local n="$1" name="$2"; shift 2
  local t=$(date +%s)
  printf "[%s] stage %s %-8s … " "$(date -u +%H:%M:%S)" "$n" "$name"
  if "$@" > "$RUN/$n-$name.log" 2>&1; then
    echo "ok ($(( $(date +%s) - t )) s)"; echo "$n $name ok $(( $(date +%s) - t ))" >> "$RUN/summary.txt"
  else
    echo "FAILED ($(( $(date +%s) - t )) s) — see $RUN/$n-$name.log"
    echo "$n $name FAILED" >> "$RUN/summary.txt"; tail -15 "$RUN/$n-$name.log"; exit 1
  fi
}

static_checks() { python3 -m py_compile cyber_range_core/*.py services/*/*.py && echo "compiled OK"; }

scan_gate() {
  [ -n "${SKIP_SCAN:-}" ] && { echo "skipped"; return 0; }
  local fail=0
  for img in $($COMPOSE config --images | grep '^sor/'); do
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro -v trivy_cache:/root/.cache \
      aquasec/trivy:0.58.1@sha256:ab70a02200597efa04748f210f793936eb647cbcdb0ea69cc30b226d6f5a22c7 \
      image --quiet --scanners vuln --severity CRITICAL --ignore-unfixed --exit-code 1 "$img" \
      && echo "$img: no fixable CRITICAL" || { echo "$img: fixable CRITICAL found"; fail=1; }
  done
  return $fail
}

wait_ready() {
  for _ in $(seq 240); do
    notok=$($COMPOSE ps --format '{{.Name}} {{.State}} {{.Health}}' | awk '$2!="running" && $1!="sor_db_init" || ($3!="" && $3!="healthy")' | wc -l)
    passing=$(curl -s localhost:8500/v1/health/state/passing | python3 -c 'import sys,json; print(len({c["ServiceName"] for c in json.load(sys.stdin) if c["ServiceName"]}))' 2>/dev/null)
    tgt=$(curl -s localhost:9090/api/v1/targets | python3 -c 'import sys,json; t=json.load(sys.stdin)["data"]["activeTargets"]; print(sum(1 for x in t if x["health"]=="up"), len(t))' 2>/dev/null)
    if [ "$notok" = 0 ] && [ "${passing:-0}" -ge 8 ] && [ "${tgt%% *}" = "${tgt##* }" ] && [ "${tgt##* }" -ge 9 ]; then
      echo "ready: services passing=$passing, targets up=$tgt"; return 0
    fi
    sleep 1
  done
  echo "not ready: notok=$notok passing=$passing targets=$tgt"; return 1
}

verify() {
  bash scripts/eval/12_functional_smoke.sh && bash scripts/eval/13_authorisation_probe.sh
}

echo "pipeline run $RUN"
stage 1 static  static_checks
stage 2 secrets scripts/security/gen_secrets.sh
stage 3 build   $COMPOSE build
stage 4 scan    scan_gate
stage 5 deploy  $COMPOSE up -d --remove-orphans
stage 6 ready   wait_ready
stage 7 verify  verify
echo "pipeline passed in $(( $(date +%s) - T0 )) s" | tee -a "$RUN/summary.txt"
