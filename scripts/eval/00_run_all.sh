#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

STAMP=$(date -u +%Y%m%d_%H%M%S)
DIR="reports/$STAMP"
mkdir -p "$DIR"
echo "Writing report to scripts/eval/$DIR/"

run() {
  local script="$1"
  echo; echo "════════ $script ════════"
  bash "$script" 2>&1 | tee "$DIR/${script%.sh}.txt"
}

ITER="${ITER:-100}"; export ITER
export AUTHZ_JSON="$PWD/$DIR/13_authz_matrix.json" ISO_JSON="$PWD/$DIR/14_isolation_raw.jsonl"
python3 00_allocation_check.py 2>&1 | tee "$DIR/00_allocation_check.txt"
run 01_service_health.sh
run 02_api_latency.sh
[ -z "${SKIP_LOAD:-}" ] && run 03_concurrency_load.sh
echo; echo "════════ 04_scoring_pipeline_lag.sh ════════"
RUNS="${SCORING_RUNS:-100}" bash 04_scoring_pipeline_lag.sh 2>&1 | tee "$DIR/04_scoring_pipeline_lag.txt"
echo; echo "════════ 05_lab_provision_time.sh ════════"
RUNS="${LAB_RUNS:-30}" bash 05_lab_provision_time.sh 2>&1 | tee "$DIR/05_lab_provision_time.txt"
run 06_resource_usage.sh
run 07_db_stats.sh
run 08_event_bus_stats.sh
run 09_tls_check.sh
run 10_prometheus_snapshot.sh
run 12_functional_smoke.sh
run 13_authorisation_probe.sh
run 14_isolation_probe.sh
echo; echo "════════ 15_teardown_profile.sh ════════"
RUNS="${TEARDOWN_RUNS:-5}" bash 15_teardown_profile.sh 2>&1 | tee "$DIR/15_teardown_profile.txt"
run 17_requirement_checks.sh
run 18_security_controls.sh

echo; echo "Report archived in scripts/eval/$DIR/"
