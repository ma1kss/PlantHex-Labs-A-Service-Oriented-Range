#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
RUNS="${RUNS:-10}"

admin_token
# Pick a lab challenge and report which one, so the timings are interpretable
# (a 3-service lab provisions slightly slower than a 2-service one).
read -r LAB_ID LAB_TITLE LAB_SVCS < <(
  $CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "next(( f'{c[\"id\"]} {c[\"title\"].replace(\" \",\"_\")} {c.get(\"lab_services\",\"?\")}'
                   for c in d if c.get('challenge_type')=='lab'), '')")
[ -z "${LAB_ID:-}" ] && { echo "No lab challenge exists — create one first."; exit 1; }

U="evallab$RANDOM"
PTOK=$(make_temp_player "$U")
echo "=== Lab provisioning, $RUNS runs ($(date -Is)) ==="
echo "challenge: ${LAB_TITLE//_/ } ($LAB_SVCS services, id ${LAB_ID:0:8})"
hr

LAUNCH_MS=$(mktemp); TEARDOWN_MS=$(mktemp); FETCH_MS=$(mktemp); TUN_MS=$(mktemp)
# Real WireGuard client (own container) so the first request is timed through
# the encrypted tunnel with the trainee's issued profile. TUNNEL=0 disables.
TUNNEL="${TUNNEL:-1}"
WGC="sor_eval_wgc05_$RANDOM"
DOCKER0=$(docker network inspect bridge -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}')
if [ "$TUNNEL" = 1 ]; then
  docker run -d --name "$WGC" --cap-add NET_ADMIN --sysctl net.ipv4.conf.all.src_valid_mark=1 \
    --entrypoint sh sor/wireguard:latest -c 'sleep 7200' >/dev/null 2>&1 || TUNNEL=0
fi
trap 'docker rm -f "$WGC" >/dev/null 2>&1' EXIT
FAILS=0
BASELINE=$(docker ps -aq --filter name=sor_lab_ | wc -l)

for i in $(seq "$RUNS"); do
  T0=$(now_ms)
  SESS=$($CURL -X POST "$SOR_BASE/scenario/challenges/$LAB_ID/session" -H "Authorization: Bearer $PTOK")
  T1=$(now_ms)
  SID=$(echo "$SESS"    | pyjson 'd.get("id","")')
  STATUS=$(echo "$SESS" | pyjson 'd.get("status","") or d.get("detail","")')
  if [ -z "$SID" ] || [ "$STATUS" != "running" ]; then
    echo "run $i: LAUNCH FAILED ($STATUS)"; FAILS=$((FAILS+1)); continue
  fi
  echo "$((T1 - T0))" >> "$LAUNCH_MS"

  # In-lab reachability from the WG container to the first web service (root
  # path — works whether it serves index.html or flag.txt).
  WG=$(docker ps --format '{{.Names}}' --filter name=sor_lab_ | grep _wg_ | head -1)
  FETCH="n/a"
  if [ -n "$WG" ]; then
    T2=$(now_ms)
    if docker exec "$WG" wget -qO- -T 5 http://web:8080/ >/dev/null 2>&1; then
      T3=$(now_ms); echo "$((T3 - T2))" >> "$FETCH_MS"; FETCH="$((T3 - T2))ms"
    fi
  fi

  # Through the tunnel: bring up the issued profile, time the first HTTP
  # request from the VPN client to the lab's web service.
  TUN="n/a"
  if [ "$TUNNEL" = 1 ]; then
    WEBIP=$(echo "$SESS" | pyjson 'next((e["ip"] for e in d.get("lab_endpoints",[]) if e["name"]=="web"), "")')
    $CURL "$SOR_BASE/scenario/sessions/$SID/wireguard.conf" -H "Authorization: Bearer $PTOK" \
      | sed "s#^Endpoint = .*:#Endpoint = $DOCKER0:#" \
      | docker exec -i "$WGC" sh -c 'wg-quick down wgc >/dev/null 2>&1; umask 077; cat > /etc/wireguard/wgc.conf && wg-quick up wgc >/dev/null 2>&1'
    T6=$(now_ms)
    if [ -n "$WEBIP" ] && docker exec "$WGC" wget -qO /dev/null -T 5 "http://$WEBIP:8080/" 2>/dev/null; then
      T7=$(now_ms); echo "$((T7 - T6))" >> "$TUN_MS"; TUN="$((T7 - T6))ms"
    fi
    docker exec "$WGC" sh -c 'wg-quick down wgc >/dev/null 2>&1; true'
  fi

  T4=$(now_ms)
  $CURL -o /dev/null -X DELETE "$SOR_BASE/scenario/sessions/$SID" -H "Authorization: Bearer $PTOK"
  T5=$(now_ms)
  echo "$((T5 - T4))" >> "$TEARDOWN_MS"
  echo "run $i: launch $((T1 - T0)) ms · in-lab fetch $FETCH · via VPN tunnel $TUN · teardown $((T5 - T4)) ms"
done

hr
echo -n "launch → running : "; meansd < "$LAUNCH_MS"
echo -n "in-lab fetch     : "; meansd < "$FETCH_MS"
echo -n "first req via VPN: "; meansd < "$TUN_MS"
echo -n "launch p50/p95   : "; awk '{printf "%.6f\n", $1/1000}' "$LAUNCH_MS" | percentiles
echo -n "teardown p50/p95 : "; awk '{printf "%.6f\n", $1/1000}' "$TEARDOWN_MS" | percentiles
echo -n "teardown         : "; meansd < "$TEARDOWN_MS"
AFTER=$(docker ps -aq --filter name=sor_lab_ | wc -l)
echo "launch failures  : $FAILS / $RUNS"
echo "leaked containers: $((AFTER - BASELINE))  (0 = clean teardown)"
echo "leaked lab nets  : $(docker network ls -q --filter name=sor_lab_ | wc -l)  (0 = all networks/subnets returned)"

rm -f "$LAUNCH_MS" "$TEARDOWN_MS" "$FETCH_MS" "$TUN_MS"
delete_user_by_name "$U"
