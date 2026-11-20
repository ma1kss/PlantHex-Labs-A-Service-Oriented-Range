#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
CONCURRENCY="${CONCURRENCY:-20}"
REQUESTS="${REQUESTS:-200}"

admin_token
OUT=$(mktemp)
echo "=== Concurrency load: $REQUESTS requests, $CONCURRENCY parallel ($(date -Is)) ==="
echo "endpoint: GET /learning/leaderboard?limit=100"
hr

T0=$(now_ms)
seq "$REQUESTS" | xargs -P "$CONCURRENCY" -I{} \
  curl -sk -o /dev/null -w '%{http_code} %{time_total}\n' \
  -H "Authorization: Bearer $ADMIN_TOK" \
  "$SOR_BASE/learning/leaderboard?limit=100" > "$OUT"
T1=$(now_ms)

WALL_MS=$((T1 - T0))
ERRS=$(awk '$1 != 200' "$OUT" | wc -l)
echo "wall time     : ${WALL_MS} ms"
echo "throughput    : $(python3 -c "print(f'{$REQUESTS/($WALL_MS/1000):.1f}')") req/s"
echo "non-200s      : $ERRS / $REQUESTS"
echo -n "latency       : "; awk '{print $2}' "$OUT" | percentiles

hr
echo "concurrent logins (bcrypt-bound): 20 requests, $CONCURRENCY parallel"
T0=$(now_ms)
seq 20 | xargs -P "$CONCURRENCY" -I{} \
  curl -sk -o /dev/null -w '%{http_code} %{time_total}\n' -X POST \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$SOR_ADMIN_USER\",\"password\":\"$SOR_ADMIN_PASS\"}" \
  "$SOR_BASE/iam/auth/login" > "$OUT"
T1=$(now_ms)
echo "wall time     : $((T1 - T0)) ms"
echo "non-200s      : $(awk '$1 != 200' "$OUT" | wc -l) / 20"
echo -n "latency       : "; awk '{print $2}' "$OUT" | percentiles
rm -f "$OUT"
