#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
RUNS="${RUNS:-5}"
admin_token
LAB_ID=$($CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "next((c['id'] for c in sorted(d, key=lambda c: c.get('lab_services') or 9) if c.get('challenge_type')=='lab'), '')")
U="evaltd$RANDOM"; PTOK=$(make_temp_player "$U")
PUID=$($CURL "$SOR_BASE/iam/users" -H "Authorization: Bearer $ADMIN_TOK" | pyjson "next(u['id'] for u in d if u['username']=='$U')")
echo "=== Teardown profile, $RUNS runs ($(date -Is)) ==="
hr
TMP=$(mktemp)
for i in $(seq "$RUNS"); do
  SID=$($CURL -X POST "$SOR_BASE/scenario/challenges/$LAB_ID/session" -H "Authorization: Bearer $PTOK" | pyjson 'd.get("id","")')
  [ -z "$SID" ] && { echo "run $i: launch failed"; continue; }
  for cid in $(docker ps -q --filter "label=sor.player_id=$PUID"); do
    name=$(docker inspect -f '{{.Name}}' "$cid" | sed 's#^/##')
    kind=$(docker inspect -f '{{index .Config.Labels "sor.kind"}}' "$cid")
    cmd=$(docker inspect -f '{{json .Config.Cmd}}' "$cid" | cut -c1-60)
    t0=$(now_ms); docker stop -t 5 "$cid" >/dev/null; t1=$(now_ms)
    ec=$(docker inspect -f '{{.State.ExitCode}}' "$cid")
    echo "$kind $((t1 - t0)) $ec" >> "$TMP"
    printf "run %d  %-14s stop %5d ms  exit=%-3s %s\n" "$i" "$kind" "$((t1 - t0))" "$ec" "$cmd"
  done
  t2=$(now_ms)
  $CURL -o /dev/null -X DELETE "$SOR_BASE/scenario/sessions/$SID" -H "Authorization: Bearer $PTOK"
  t3=$(now_ms)
  echo "api-remainder $((t3 - t2)) 0" >> "$TMP"
  printf "run %d  %-14s %5d ms  (remove stopped containers + network, DB update)\n" "$i" "api-remainder" "$((t3 - t2))"
done
hr
echo "mean stop time per container kind (exit 137 = SIGKILL after grace period, i.e. SIGTERM ignored):"
python3 - "$TMP" <<'EOF'
import sys, collections, statistics
d = collections.defaultdict(list); ex = collections.defaultdict(collections.Counter)
for l in open(sys.argv[1]):
    k, ms, ec = l.split(); d[k].append(int(ms)); ex[k][ec] += 1
for k, xs in d.items():
    sd = statistics.stdev(xs) if len(xs) > 1 else 0
    print(f"  {k:14s} n={len(xs):2d}  mean={statistics.mean(xs):6.0f} ± {sd:4.0f} ms  exit codes {dict(ex[k])}")
EOF
rm -f "$TMP"; delete_user_by_name "$U"
