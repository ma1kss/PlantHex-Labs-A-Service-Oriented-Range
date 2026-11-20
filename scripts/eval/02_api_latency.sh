#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
ITER="${ITER:-30}"

admin_token
echo "=== API latency, $ITER sequential requests per endpoint ($(date -Is)) ==="
echo "base=$SOR_BASE"
hr

bench() {  # bench <label> <url> [extra curl args...]
  local label="$1" url="$2"; shift 2
  local out
  out=$(for _ in $(seq "$ITER"); do
    $CURL -o /dev/null -w '%{time_total}\n' "$@" "$url"
  done | percentiles)
  printf "%-34s %s\n" "$label" "$out"
}

bench "GET / (SPA index, TLS)"        "$SOR_BASE/"
bench "GET /js/main.js (static)"      "$SOR_BASE/js/main.js"
bench "GET /health (edge)"            "$SOR_BASE/health"
bench "GET /iam/health (proxied)"     "$SOR_BASE/iam/health"
bench "GET /iam/auth/validate"        "$SOR_BASE/iam/auth/validate"   -H "Authorization: Bearer $ADMIN_TOK"
bench "GET /iam/users"                "$SOR_BASE/iam/users"           -H "Authorization: Bearer $ADMIN_TOK"
bench "GET /scenario/challenges"      "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK"
bench "GET /learning/leaderboard"     "$SOR_BASE/learning/leaderboard?limit=100" -H "Authorization: Bearer $ADMIN_TOK"
bench "GET /learning/leaderboard/teams" "$SOR_BASE/learning/leaderboard/teams"   -H "Authorization: Bearer $ADMIN_TOK"
bench "GET /learning/teams"           "$SOR_BASE/learning/teams"      -H "Authorization: Bearer $ADMIN_TOK"
bench "GET /iam/profile"              "$SOR_BASE/iam/profile"         -H "Authorization: Bearer $ADMIN_TOK"

# Login is the heaviest call by design (bcrypt) — measured separately.
out=$(for _ in $(seq "$ITER"); do
  $CURL -o /dev/null -w '%{time_total}\n' -X POST "$SOR_BASE/iam/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$SOR_ADMIN_USER\",\"password\":\"$SOR_ADMIN_PASS\"}"
done | percentiles)
printf "%-34s %s\n" "POST /iam/auth/login (bcrypt)" "$out"
