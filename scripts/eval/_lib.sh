#!/usr/bin/env bash
SOR_BASE="${SOR_BASE:-https://localhost:8000}"
SOR_ADMIN_USER="${SOR_ADMIN_USER:-charon}"
SOR_ADMIN_PASS="${SOR_ADMIN_PASS:-charon}"

CURL="curl -sk"

# sor_token <user> <pass> → prints the JWT (empty on failure)
sor_token() {
  $CURL -X POST "$SOR_BASE/iam/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$1\",\"password\":\"$2\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null
}

# pyjson '<python expr over d>' — parse stdin JSON into d and print the expr
pyjson() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }

# percentiles — read one latency-in-seconds per line, print a summary line
percentiles() {
  python3 -c '
import sys, statistics
xs = sorted(float(l) for l in sys.stdin if l.strip())
if not xs:
    print("no data"); raise SystemExit
def q(p):
    i = (len(xs) - 1) * p; lo = int(i); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)
print(f"n={len(xs)}  min={xs[0]*1000:.1f}ms  p50={q(.5)*1000:.1f}ms  "
      f"p95={q(.95)*1000:.1f}ms  p99={q(.99)*1000:.1f}ms  "
      f"max={xs[-1]*1000:.1f}ms  mean={statistics.mean(xs)*1000:.1f}ms")'
}

# admin_token → cached admin JWT in $ADMIN_TOK
admin_token() {
  ADMIN_TOK="${ADMIN_TOK:-$(sor_token "$SOR_ADMIN_USER" "$SOR_ADMIN_PASS")}"
  if [ -z "$ADMIN_TOK" ]; then
    echo "FATAL: cannot log in as $SOR_ADMIN_USER — is the stack up at $SOR_BASE?" >&2
    exit 1
  fi
}

# make_temp_player <username> → creates a player via the admin token (skips the
# registration secret) and prints its JWT. Password is "evaltmp123".
make_temp_player() {
  admin_token
  $CURL -X POST "$SOR_BASE/iam/auth/register" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $ADMIN_TOK" \
    -d "{\"username\":\"$1\",\"password\":\"evaltmp123\",\"roles\":[\"player\"]}" >/dev/null
  sor_token "$1" "evaltmp123"
}

# delete_user_by_name <username> — removes the account (scoreboard row follows
# automatically via the iam.user.deleted event).
delete_user_by_name() {
  admin_token
  local uid
  uid=$($CURL "$SOR_BASE/iam/users" -H "Authorization: Bearer $ADMIN_TOK" \
    | pyjson "next((u['id'] for u in d if u['username']=='$1'), '')")
  [ -n "$uid" ] && $CURL -o /dev/null -X DELETE "$SOR_BASE/iam/users/$uid" \
    -H "Authorization: Bearer $ADMIN_TOK"
}

# Monotonic (system-wide CLOCK_MONOTONIC): immune to WSL wall-clock steps.
now_ms() { python3 -c 'import time;print(time.monotonic_ns()//1000000)'; }

# meansd — read one number (ms) per line from stdin, print "n · mean ± sd ·
# min–max ms". Reports sd only when n>1.
meansd() {
  python3 -c '
import sys, statistics
xs = [float(l) for l in sys.stdin if l.strip()]
if not xs:
    print("no data"); raise SystemExit
if len(xs) == 1:
    print(f"n=1  {xs[0]:.0f} ms")
else:
    sd = statistics.stdev(xs)
    print(f"n={len(xs)}  mean={statistics.mean(xs):.0f} ± {sd:.0f} ms  "
          f"(min={min(xs):.0f}  max={max(xs):.0f}  median={statistics.median(xs):.0f})")'
}

hr() { printf -- '─%.0s' {1..64}; echo; }

# RabbitMQ management API credentials: the operator account created by
# infrastructure/rabbit_init (the default guest account is removed).
RMQ_AUTH="${RMQ_AUTH:-sor_admin:$(cat "$(dirname "${BASH_SOURCE[0]}")/../../infrastructure/secrets/rmq_admin.pw" 2>/dev/null)}"
