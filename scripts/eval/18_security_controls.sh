#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf "  PASS  %s\n" "$*"; }
bad() { FAIL=$((FAIL+1)); printf "  FAIL  %s\n" "$*"; }
admin_token
A=(-H "Authorization: Bearer $ADMIN_TOK"); J=(-H 'Content-Type: application/json')
SUF=$RANDOM
SECRETS=../../infrastructure/secrets

echo "=== Security & data controls ($(date -Is)) ==="
hr

# H. bus-message injection (SR-FR-IL-34, SEC-004, SEC-020, SEC-014)
echo "H. forged event injected into the broker"
U="evalinj$SUF"; PT=$(make_temp_player "$U")
PUID=$($CURL "$SOR_BASE/iam/users" "${A[@]}" | pyjson "next(u['id'] for u in d if u['username']=='$U')")
CHAL=$($CURL "$SOR_BASE/scenario/challenges" "${A[@]}" | pyjson 'd[0]["id"]')
rejected() { for c in sor_learning:8008 sor_data:8007 sor_events:8004; do
  docker exec "${c%%:*}" curl -s "localhost:${c##*:}/metrics"; done | awk '/^sor_events_rejected_total/ {s+=$NF} END {print s+0}'; }
rej0=$(rejected)
FORGED=$(python3 -c "
import json; print(json.dumps({'event':'scenario.flag.submitted','service':'scenario-service',
  'timestamp':'2026-01-01T00:00:00+00:00','correlation_id':'evalforged','user_id':'$PUID',
  'username':'$U','challenge_id':'$CHAL','correct':True,'difficulty':'hard'}))")
INJ=$(date -u +%Y-%m-%dT%H:%M:%S)
pub=$(curl -s -u "$RMQ_AUTH" -H 'content-type: application/json' -X POST \
  "localhost:15672/api/exchanges/%2F/cyber_range.events/publish" \
  -d "$(python3 -c "import json,sys; print(json.dumps({'properties':{},'routing_key':'scenario.flag.submitted','payload':sys.argv[1],'payload_encoding':'string'}))" "$FORGED")")
sleep 3
sc=$($CURL "$SOR_BASE/learning/me" -H "Authorization: Bearer $PT" | pyjson 'd["solve_count"]')
rej1=$(rejected)
echo "    broker accepted the publish: $pub"
[ "$sc" = 0 ] && ok "IL-34/SEC-004: forged 'correct flag' event did not score (solve_count=$sc)" \
              || bad "forged event was scored (solve_count=$sc)"
python3 -c "import sys; sys.exit(0 if float('$rej1') > float('$rej0') else 1)" \
  && ok "IL-34: every consumer of the key dropped and counted it (sor_events_rejected_total $rej0 → $rej1)" \
  || bad "rejection not counted ($rej0 → $rej1)"
# (the alert window is 5 min, so require it to start after the injection)
T0=$(now_ms); got=""
for _ in $(seq 45); do
  got=$($CURL "$SOR_BASE/monitoring/alerts" "${A[@]}" | pyjson "next((a['active_since'][:19] for a in d['firing'] if a['alertname']=='SorForgedBusMessage' and a['active_since'][:19] >= '$INJ'), '')")
  [ -n "$got" ] && break; sleep 2
done
[ -n "$got" ] && ok "SEC-014: alert SorForgedBusMessage became active at $got (injection $INJ), visible $(( ($(now_ms)-T0)/1000 )) s later" \
              || bad "no SorForgedBusMessage alert within 90 s"
delete_user_by_name "$U"

# I. tamper-evident archive (SEC-012, SEC-004, SEC-013)
echo; echo "I. audit archive hash chain"
V=$($CURL "$SOR_BASE/data/integrity" "${A[@]}")
echo "    $(echo "$V" | pyjson '"records=%s documented_deletions=%s unchained=%s intact=%s" % (d["records_checked"], d["documented_deletions"], d["unchained_records"], d["intact"])')"
[ "$(echo "$V" | pyjson 'd["intact"]')" = True ] && ok "SEC-012: hash chain intact over the whole archive" || bad "chain not intact: $V"
# tamper one record directly in the store (operator access on 127.0.0.1)
REC=$(curl -s "localhost:9200/sor-records/_search?size=1&sort=seq:asc&q=routing_key:iam.user.login" | python3 -c '
import sys,json; h=json.load(sys.stdin)["hits"]["hits"][0]; print(h["_id"], h["_source"]["seq"]); json.dump(h["_source"], open("/tmp/sor_tamper_orig.json","w"))')
read -r RID RSEQ <<<"$REC"
curl -s -o /dev/null -X POST "localhost:9200/sor-records/_update/$RID?refresh=true" -H 'content-type: application/json' \
  -d '{"doc":{"message":"iam.user.login username=someone-else"}}'
V2=$($CURL "$SOR_BASE/data/integrity" "${A[@]}")
P=$(echo "$V2" | pyjson '"; ".join("seq %s: %s" % (p["seq"], p["problem"]) for p in d["problems"])')
[ "$(echo "$V2" | pyjson 'd["intact"]')" = False ] && echo "$P" | grep -q "seq $RSEQ: content modified" \
  && ok "SEC-012: direct edit of record seq $RSEQ in the store detected ($P)" || bad "tampering not detected: $V2"
curl -s -o /dev/null -X PUT "localhost:9200/sor-records/_doc/$RID?refresh=true" -H 'content-type: application/json' \
  -d @/tmp/sor_tamper_orig.json; rm -f /tmp/sor_tamper_orig.json
[ "$($CURL "$SOR_BASE/data/integrity" "${A[@]}" | pyjson 'd["intact"]')" = True ] \
  && ok "SEC-012: chain verifies again after the original record was restored" || bad "chain broken after restore"
ID=$($CURL -X POST "$SOR_BASE/data/ingest" "${A[@]}" "${J[@]}" -d '{"kind":"log","routing_key":"eval.probe","message":"evalprobe record to delete"}' | pyjson 'd["id"]')
sleep 1; $CURL -o /dev/null -X DELETE "$SOR_BASE/data/logs/$ID" "${A[@]}"; sleep 1
V3=$($CURL "$SOR_BASE/data/integrity" "${A[@]}")
[ "$(echo "$V3" | pyjson 'd["intact"]')" = True ] && [ "$(echo "$V3" | pyjson 'd["documented_deletions"]')" -ge 1 ] \
  && ok "SEC-012/SEC-018: an administrator's deletion is itself recorded; chain still verifies ($(echo "$V3" | pyjson 'd["documented_deletions"]') documented)" \
  || bad "deletion broke the chain: $V3"

# J. real-time alerting (SR-FR-QoS-41, SEC-011)
echo; echo "J. alerting on a service outage"
docker stop sor_orchestration >/dev/null; T0=$(now_ms); fired=""
for _ in $(seq 60); do
  fired=$($CURL "$SOR_BASE/monitoring/alerts" "${A[@]}" | pyjson "next((a['summary'] for a in d['firing'] if a['alertname']=='SorServiceMissing'), '')")
  [ -n "$fired" ] && break; sleep 2
done
TF=$(( ($(now_ms) - T0) / 1000 ))
[ -n "$fired" ] && ok "QoS-41: SorServiceMissing firing ${TF} s after the service stopped (\"$fired\")" || bad "no alert within 120 s"
docker start sor_orchestration >/dev/null; T0=$(now_ms); res=""
for _ in $(seq 60); do
  res=$($CURL "$SOR_BASE/monitoring/alerts" "${A[@]}" | pyjson "next((a['state'] for a in d['recent_transitions'] if a['alertname']=='SorServiceMissing' and a['state']=='resolved'), '')")
  [ -n "$res" ] && break; sleep 2
done
[ -n "$res" ] && ok "QoS-41: alert resolved $(( ($(now_ms)-T0)/1000 )) s after restart" || bad "alert not resolved within 120 s"
sleep 2
n=$($CURL "$SOR_BASE/data/logs?routing=monitoring.alert.&limit=5" "${A[@]}" | pyjson 'd["total"]')
[ "${n:-0}" -ge 2 ] && ok "QoS-41/SEC-013: firing and resolved alerts archived ($n monitoring.alert.* records)" || bad "alerts not archived ($n)"

# K. metrics in the unified archive (SR-FR-IFL-42)
echo; echo "K. metrics snapshots"
n=$($CURL "$SOR_BASE/data/logs?routing=monitoring.metrics.snapshot&kind=telemetry&limit=1" "${A[@]}" | pyjson 'd["total"]')
[ "${n:-0}" -ge 1 ] && ok "IFL-42: $n per-service metric snapshots stored next to logs and events" || bad "no metric snapshots in the archive"

# L. ingest / export controls (SEC-004, SEC-013, SEC-017, IFL-44)
echo; echo "L. ingest and export"
U="evalingest$SUF"; PT=$(make_temp_player "$U")
c=$($CURL -o /dev/null -w '%{http_code}' -X POST "$SOR_BASE/data/ingest" -H "Authorization: Bearer $PT" "${J[@]}" -d '{"routing_key":"x"}')
[ "$c" = 403 ] && ok "SEC-004: trainee cannot write into the archive (HTTP $c)" || bad "trainee ingest → HTTP $c"
delete_user_by_name "$U"
ID=$($CURL -X POST "$SOR_BASE/data/ingest" "${A[@]}" "${J[@]}" \
      -d '{"kind":"log","routing_key":"eval.probe","service":"iam-service","timestamp":"2020-01-01T00:00:00Z","message":"evalprobe"}' | pyjson 'd["id"]')
sleep 1
R=$(curl -s "localhost:9200/sor-records/_doc/$ID" | pyjson '"%s|%s" % (d["_source"]["service"], d["_source"]["timestamp"][:4])')
[ "${R%%|*}" = "direct:$SOR_ADMIN_USER" ] && [ "${R##*|}" != 2020 ] \
  && ok "SEC-013: admin record attributed to '${R%%|*}', server timestamp (claimed 2020 kept only as a field)" || bad "ingest attribution: $R"
$CURL -o /dev/null -X DELETE "$SOR_BASE/data/logs/$ID" "${A[@]}"
X=$($CURL "$SOR_BASE/data/logs/export?fmt=xml&routing=iam.user.login" "${A[@]}")
echo "$X" | python3 -c 'import sys,xml.dom.minidom as m; d=m.parseString(sys.stdin.read()); print(len(d.getElementsByTagName("record")))' >/tmp/sor_xml_n 2>/dev/null \
  && ok "IFL-44: XML export is well-formed ($(cat /tmp/sor_xml_n) records; CSV/JSON/NDJSON also offered)" || bad "XML export invalid"
rm -f /tmp/sor_xml_n; sleep 1
n=$($CURL "$SOR_BASE/data/logs?routing=data.export.performed&limit=1" "${A[@]}" | pyjson 'd["total"]')
[ "${n:-0}" -ge 1 ] && ok "SEC-017: exports are recorded in the tamper-evident archive ($n export records)" || bad "export not audited"

# M. flags encrypted at rest (SEC-005)
echo; echo "M. flags at rest"
plain=$(docker exec sor_postgres psql -U sor -d sor -tAc "select count(*) from challenges where flag<>'' and flag not like 'enc:%'")
total=$(docker exec sor_postgres psql -U sor -d sor -tAc "select count(*) from challenges where flag<>''")
FCH=$(docker exec sor_postgres psql -U sor -d sor -tAc "select id from challenges where flag<>'' limit 1")
fl=$($CURL "$SOR_BASE/scenario/challenges/$FCH/flag" "${A[@]}" | pyjson 'd.get("flag","")')
[ "$plain" = 0 ] && [ -n "$fl" ] && ok "SEC-005: $total/$total stored flags encrypted (Fernet); administrator can still recover them" \
                                  || bad "plaintext flags: $plain/$total (recovered: ${fl:+yes})"

# N. per-service database roles (SEC-008)
echo; echo "N. database roles"
try() { docker exec -e PGPASSWORD="$(cat $SECRETS/db_$1.pw)" sor_postgres \
          psql -h localhost -U "svc_$1" -d sor -tAc "$2" 2>&1 | head -1; }
r1=$(try learning "select count(*) from users"); r2=$(try iam "select count(*) from solves")
r3=$(try scenario "select count(*) from participants"); r4=$(try learning "select count(*) from solves")
[ "$(echo "$r1 $r2 $r3" | grep -o "permission denied" | wc -l)" = 3 ] && [[ "$r4" =~ ^[0-9]+$ ]] \
  && ok "SEC-008: svc_learning↛users, svc_iam↛solves, svc_scenario↛participants denied; own tables readable ($r4 solves)" \
  || bad "role separation: [$r1] [$r2] [$r3] own:[$r4]"

# O. Docker socket proxy (SEC-008)
echo; echo "O. Docker API exposure of Scenario Management"
pc() { docker exec sor_scenario curl -s -o /dev/null -w '%{http_code}' -X "$1" "http://sor_docker_proxy:2375$2"; }
ok_list=$(pc GET /containers/json); v=$(pc GET /volumes); sys=$(pc GET /system/df)
ex=$(pc POST /exec/x/start); sec=$(pc GET /secrets); sw=$(pc GET /swarm)
[ "$ok_list" = 200 ] && [ "$v$sys$ex$sec$sw" = 403403403403403 ] \
  && ok "SEC-008: proxy allows containers ($ok_list) but refuses volumes/system/exec/secrets/swarm (403)" \
  || bad "proxy: containers=$ok_list volumes=$v system=$sys exec=$ex secrets=$sec swarm=$sw"
docker exec sor_scenario test -S /var/run/docker.sock && bad "scenario still mounts the raw Docker socket" \
  || ok "SEC-008: Scenario Management holds no Docker socket"

# S. per-service broker accounts (SEC-008)
echo; echo "S. broker accounts"
docker exec -i sor_learning python - <<'PY'
import asyncio, aio_pika
from app import settings          # svc_learning's own broker credentials
async def main():
    async def attempt(name, fn):
        conn = await aio_pika.connect(settings.RABBITMQ_URL)
        try:
            ch = await conn.channel()
            await fn(ch); return name, "allowed"
        except Exception as e:
            return name, "refused" if "ACCESS_REFUSED" in str(e) else f"error {type(e).__name__}"
        finally:
            await conn.close()
    async def own(ch):   await ch.declare_queue("learning-service.scoring", durable=True, passive=True)
    async def other(ch):
        q = await ch.declare_queue("portal.sessions", durable=True, passive=True)
        await q.get(no_ack=False, fail=False)
    async def new_q(ch): await ch.declare_queue("evil.queue", durable=True)
    async def pub(ch):
        ex = await ch.get_exchange("cyber_range.events", ensure=False)
        await ex.publish(aio_pika.Message(b"{}"), routing_key="probe.noop")
    res = dict([await attempt(n, f) for n, f in
                (("own queue", own), ("other service's queue", other),
                 ("create a new queue", new_q), ("publish to exchange", pub))])
    ok = res == {"own queue": "allowed", "other service's queue": "refused",
                 "create a new queue": "refused", "publish to exchange": "allowed"}
    print(("  PASS  SEC-008: " if ok else "  FAIL  ") + "svc_learning broker rights: " +
          ", ".join(f"{k}={v}" for k, v in res.items()))
asyncio.run(main())
PY
c=$(curl -s -o /dev/null -w '%{http_code}' -u guest:guest localhost:15672/api/whoami)
[ "$c" = 401 ] && ok "SEC-008: default guest broker account removed (HTTP $c)" || bad "guest account still works (HTTP $c)"

# P. White-team controls in lab definitions (SEC-027)
echo; echo "P. lab definition controls"
docker exec -i sor_scenario python - <<'PY'
from lab_runtime import parse_compose
cases = {
  "privileged":      "services:\n  a:\n    image: alpine\n    privileged: true\n",
  "host mount":      "services:\n  a:\n    image: alpine\n    volumes: ['/:/host']\n",
  "host network":    "services:\n  a:\n    image: alpine\n    network_mode: host\n",
  "SYS_ADMIN cap":   "services:\n  a:\n    image: alpine\n    cap_add: [SYS_ADMIN]\n",
}
for name, y in cases.items():
    try:
        parse_compose(y); print(f"  FAIL  {name} accepted")
    except ValueError as e:
        print(f"  PASS  SEC-027: lab definition with {name} rejected ({str(e)[:70]})")
s = parse_compose("services:\n  a:\n    image: alpine\n    user: '1000:1000'\n    read_only: true\n    cap_add: [NET_RAW]\n")[0]
ok = s["user"] == "1000:1000" and s["read_only"] and s["cap_add"] == ["NET_RAW"]
print(("  PASS  SEC-027: designer may set user=1000:1000, read_only, cap_add=[NET_RAW]" if ok else "  FAIL  white-team options lost"))
PY

# Q. challenge deletion consistency (SR-FR-IFL-43)
echo; echo "Q. challenge deletion"
U="evalcons$SUF"; PT=$(make_temp_player "$U")
NEWC=$($CURL -X POST "$SOR_BASE/scenario/challenges" "${A[@]}" -F title="evalcons$SUF" -F flag="FLAG{evalcons}" \
        -F difficulty=hard -F visible=true | pyjson 'd.get("id","")')
$CURL -o /dev/null -X POST "$SOR_BASE/scenario/challenges/$NEWC/submit" -H "Authorization: Bearer $PT" "${J[@]}" -d '{"flag":"FLAG{evalcons}"}'
sleep 2; before=$($CURL "$SOR_BASE/learning/me" -H "Authorization: Bearer $PT" | pyjson '"%s/%s" % (d["solve_count"], d["total_score"])')
$CURL -o /dev/null -X DELETE "$SOR_BASE/scenario/challenges/$NEWC" "${A[@]}"; sleep 2
after=$($CURL "$SOR_BASE/learning/me" -H "Authorization: Bearer $PT" | pyjson '"%s/%s" % (d["solve_count"], d["total_score"])')
[ "$before" = "1/30" ] && [ "$after" = "0/0" ] && ok "IFL-43: solve+points removed when the challenge was deleted (solves/points $before → $after)" \
                                              || bad "deletion consistency: $before → $after"
delete_user_by_name "$U"

# R. per-exercise enrolment (F07, SR-FR-BPL-23)
echo; echo "R. exercise enrolment"
U="evalenr$SUF"; PT=$(make_temp_player "$U")
PUID=$($CURL "$SOR_BASE/iam/users" "${A[@]}" | pyjson "next(u['id'] for u in d if u['username']=='$U')")
EV=$($CURL -X POST "$SOR_BASE/scenario/events" "${A[@]}" "${J[@]}" \
      -d '{"name":"evalexercise","starts_at":"2030-01-01T09:00:00Z","ends_at":"2030-01-01T17:00:00Z","challenge_ids":[]}' | pyjson 'd.get("id","")')
c=$($CURL -o /dev/null -w '%{http_code}' -X POST "$SOR_BASE/learning/exercises/$EV/enrolments" "${A[@]}" "${J[@]}" \
      -d "{\"user_id\":\"$PUID\",\"username\":\"$U\",\"team\":\"red-1\",\"role\":\"red\"}")
mine=$($CURL "$SOR_BASE/learning/me/exercises" -H "Authorization: Bearer $PT" | pyjson '"%s/%s/%s" % (d[0]["event_id"][:8], d[0]["team"], d[0]["role"]) if d else ""')
bad_role=$($CURL -o /dev/null -w '%{http_code}' -X POST "$SOR_BASE/learning/exercises/$EV/enrolments" "${A[@]}" "${J[@]}" \
      -d "{\"user_id\":\"$PUID\",\"role\":\"god\"}")
[ "$c" = 201 ] && [ "$mine" = "${EV:0:8}/red-1/red" ] && [ "$bad_role" = 422 ] \
  && ok "BPL-23/F07: trainee enrolled in exercise ${EV:0:8} as team red-1, role red; unknown role refused" \
  || bad "enrolment: create=$c mine=$mine bad_role=$bad_role"
$CURL -o /dev/null -X DELETE "$SOR_BASE/scenario/events/$EV" "${A[@]}"; sleep 2
left=$($CURL "$SOR_BASE/learning/me/exercises" -H "Authorization: Bearer $PT" | pyjson 'len(d)')
[ "$left" = 0 ] && ok "F07: enrolments removed with the exercise" || bad "enrolments left after exercise deletion: $left"
delete_user_by_name "$U"

hr
echo "SUMMARY: $PASS passed, $FAIL failed (part P lines printed separately)"
[ "$FAIL" -eq 0 ]
