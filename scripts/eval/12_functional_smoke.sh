#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh

PASS=0; FAIL=0
chk() {  # chk <label> <expected> <actual>
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "PASS  $1"
  else FAIL=$((FAIL+1)); echo "FAIL  $1  (expected '$2', got '$3')"; fi
}

admin_token
SECRET=$($CURL "$SOR_BASE/iam/settings/registration-secret" -H "Authorization: Bearer $ADMIN_TOK" | pyjson 'd["secret"]')
U="evalsmoke$RANDOM"

echo "=== Functional smoke ($(date -Is)) ==="

# Registration path (public, with the real secret)
code=$($CURL -o /dev/null -w '%{http_code}' -X POST "$SOR_BASE/iam/auth/register" \
  -H 'Content-Type: application/json' -d "{\"username\":\"$U\",\"password\":\"abc\",\"secret\":\"$SECRET\"}")
chk "short password rejected (422)" 422 "$code"
msg=$($CURL -X POST "$SOR_BASE/iam/auth/register" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$U\",\"password\":\"abc\",\"secret\":\"$SECRET\"}" | pyjson 'd["detail"]')
chk "human-readable validation msg" "Password must be at least 6 characters." "$msg"
code=$($CURL -o /dev/null -w '%{http_code}' -X POST "$SOR_BASE/iam/auth/register" \
  -H 'Content-Type: application/json' -d "{\"username\":\"$U\",\"password\":\"abcdef\",\"secret\":\"WRONG\"}")
chk "wrong secret rejected (403)" 403 "$code"
code=$($CURL -o /dev/null -w '%{http_code}' -X POST "$SOR_BASE/iam/auth/register" \
  -H 'Content-Type: application/json' -d "{\"username\":\"$U\",\"password\":\"abcdef\",\"secret\":\"$SECRET\"}")
chk "register with secret (201)" 201 "$code"

PTOK=$(sor_token "$U" abcdef)
chk "login works" "yes" "$([ -n "$PTOK" ] && echo yes || echo no)"

# Role-gated visibility
IID=$($CURL "$SOR_BASE/iam/users" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "next((u['id'] for u in d if 'instructor' in u['roles']), '')")
TMP_INSTR=""
if [ -z "$IID" ]; then  # fresh deployment: use a throw-away instructor
  TMP_INSTR="evalinstr$RANDOM"
  IID=$($CURL -X POST "$SOR_BASE/iam/auth/register" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $ADMIN_TOK" \
    -d "{\"username\":\"$TMP_INSTR\",\"password\":\"evaltmp123\",\"roles\":[\"instructor\"]}" \
    | pyjson 'd.get("id","")' 2>/dev/null)
fi
if [ -n "$IID" ]; then
  code=$($CURL -o /dev/null -w '%{http_code}' "$SOR_BASE/iam/profiles/$IID" -H "Authorization: Bearer $PTOK")
  chk "player cannot view instructor profile (403)" 403 "$code"
fi
code=$($CURL -o /dev/null -w '%{http_code}' "$SOR_BASE/learning/solves/revoke" -X POST \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $PTOK" -d '{"challenge_id":"x","user_id":"y"}')
chk "player cannot revoke solves (403)" 403 "$code"

# Solve → score → revoke → unsolved (uses the first recoverable-flag challenge)
CHAL=$($CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "next((c['id'] for c in d if c.get('challenge_type')=='lab'), next((c['id'] for c in d), ''))")
FLAG=$($CURL "$SOR_BASE/scenario/challenges/$CHAL/flag" -H "Authorization: Bearer $ADMIN_TOK" | pyjson 'd.get("flag","")')
if [ -n "$FLAG" ]; then
  ok=$($CURL -X POST "$SOR_BASE/scenario/challenges/$CHAL/submit" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $PTOK" -d "{\"flag\":\"$FLAG\"}" | pyjson 'd["correct"]')
  chk "correct flag accepted" "True" "$ok"
  sleep 2
  sc=$($CURL "$SOR_BASE/learning/me" -H "Authorization: Bearer $PTOK" | pyjson 'd["solve_count"]')
  chk "solve scored" 1 "$sc"
  UID_=$($CURL "$SOR_BASE/iam/auth/validate" -H "Authorization: Bearer $PTOK" | pyjson 'd["id"]')
  $CURL -o /dev/null -X POST "$SOR_BASE/learning/solves/revoke" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $ADMIN_TOK" -d "{\"challenge_id\":\"$CHAL\",\"user_id\":\"$UID_\"}"
  $CURL -o /dev/null -X DELETE "$SOR_BASE/scenario/submissions?challenge_id=$CHAL&user_id=$UID_" \
    -H "Authorization: Bearer $ADMIN_TOK"
  sc=$($CURL "$SOR_BASE/learning/me" -H "Authorization: Bearer $PTOK" | pyjson 'd["solve_count"]')
  chk "revoke removes the solve" 0 "$sc"
  solved=$($CURL "$SOR_BASE/scenario/my-solves" -H "Authorization: Bearer $PTOK" | pyjson 'len(d["solved"])')
  chk "challenge unsolved after revoke" 0 "$solved"
else
  echo "SKIP  solve/revoke block (no challenge with a recoverable flag)"
fi

# Deleted user disappears from the scoreboard
delete_user_by_name "$U"; [ -n "$TMP_INSTR" ] && delete_user_by_name "$TMP_INSTR"; sleep 2
on=$($CURL "$SOR_BASE/learning/leaderboard?limit=500" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "'$U' in [r['username'] for r in d]")
chk "deleted user off the scoreboard" "False" "$on"

hr
echo "RESULT: $PASS passed, $FAIL failed"
exit "$([ $FAIL -eq 0 ] && echo 0 || echo 1)"
