#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
RUNS="${RUNS:-3}"

admin_token
CHAL=$($CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "next((c['id'] for c in d if c.get('challenge_type')=='lab'), next((c['id'] for c in d), ''))")
[ -z "$CHAL" ] && { echo "No challenge available — create one first."; exit 1; }
FLAG=$($CURL "$SOR_BASE/scenario/challenges/$CHAL/flag" -H "Authorization: Bearer $ADMIN_TOK" | pyjson 'd.get("flag","")')
[ -z "$FLAG" ] && { echo "Challenge $CHAL has no recoverable flag."; exit 1; }

echo "=== Scoring pipeline lag, $RUNS runs (challenge=$CHAL) ($(date -Is)) ==="
hr
ACK_MS=$(mktemp); LAG_MS=$(mktemp); MISSED=0

for i in $(seq "$RUNS"); do
  U="evallag$RANDOM"
  PTOK=$(make_temp_player "$U")
  T0=$(now_ms)
  $CURL -o /dev/null -X POST "$SOR_BASE/scenario/challenges/$CHAL/submit" \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $PTOK" \
    -d "{\"flag\":\"$FLAG\"}"
  T1=$(now_ms)
  # Poll until the solve is visible on the learner's record.
  SCORED=""
  for _ in $(seq 200); do
    SC=$($CURL "$SOR_BASE/learning/me" -H "Authorization: Bearer $PTOK" | pyjson 'd["solve_count"]')
    if [ "$SC" != "0" ]; then SCORED=$(now_ms); break; fi
    sleep 0.05
  done
  if [ -n "$SCORED" ]; then
    echo "run $i: submit HTTP round-trip $((T1 - T0)) ms · submit→score-visible $((SCORED - T0)) ms"
    echo "$((T1 - T0))" >> "$ACK_MS"; echo "$((SCORED - T0))" >> "$LAG_MS"
  else
    echo "run $i: NOT SCORED within 10 s — check sor_learning logs"; MISSED=$((MISSED+1))
  fi
  delete_user_by_name "$U"     # also removes the participant row via the bus
done

hr
ms_to_s() { awk '{printf "%.6f\n", $1/1000}'; }
echo -n "submit acknowledged : "; ms_to_s < "$ACK_MS" | percentiles
echo -n "                      "; meansd < "$ACK_MS"
echo -n "score visible       : "; ms_to_s < "$LAG_MS" | percentiles
echo -n "                      "; meansd < "$LAG_MS"
echo "not scored in 10 s  : $MISSED / $RUNS"
echo "note: score-visible is polled (~50 ms sleep + one HTTP round trip per poll),"
echo "      so it over-estimates the true pipeline lag by up to one poll interval."
rm -f "$ACK_MS" "$LAG_MS"
