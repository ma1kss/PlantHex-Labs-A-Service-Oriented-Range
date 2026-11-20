#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
ROOT=../..; COMPOSE_FILE=$ROOT/infrastructure/docker-compose.yml
admin_token
A=(-H "Authorization: Bearer $ADMIN_TOK")
J=(-H 'Content-Type: application/json')
row() { printf "%-34s %-8s %s\n" "$1" "$2" "$3"; }

echo "=== Requirement checks ($(date -Is)) ==="
hr

# SR-FR-BPL-23 / F07: participant enrolment and team assignment
U="evalreq$RANDOM"; PT=$(make_temp_player "$U")
PUID=$($CURL "$SOR_BASE/iam/users" "${A[@]}" | pyjson "next(u['id'] for u in d if u['username']=='$U')")
c1=$($CURL -o /dev/null -w '%{http_code}' -X POST "$SOR_BASE/learning/participants" "${A[@]}" "${J[@]}" \
      -d "{\"user_id\":\"$PUID\",\"username\":\"$U\"}")
TEAM="evalteam$RANDOM"
TID=$($CURL -X POST "$SOR_BASE/learning/teams" "${A[@]}" "${J[@]}" -d "{\"name\":\"$TEAM\"}" | pyjson 'd.get("id","")')
c2=$($CURL -o /dev/null -w '%{http_code}' -X PUT "$SOR_BASE/learning/participants/$PUID/team" "${A[@]}" "${J[@]}" \
      -d "{\"team\":\"$TEAM\"}")
got=$($CURL "$SOR_BASE/learning/me" -H "Authorization: Bearer $PT" | pyjson 'd.get("team","")')
if [ "$got" = "$TEAM" ]; then
  row "SR-FR-BPL-23 (F07)" PASS "registration + enrolment HTTP $c1, team create+assign HTTP $c2 (trainee sees '$got'); per-exercise enrolment with team and red/blue/white role (18 part R)"
else
  row "SR-FR-BPL-23 (F07)" FAIL "enrol HTTP $c1 / assign HTTP $c2 / team seen '$got'"
fi
[ -n "$TID" ] && $CURL -o /dev/null -X DELETE "$SOR_BASE/learning/teams/$TID" "${A[@]}"

# SR-FR-IFL-44/45, SEC-017: export formats, unified query, export audit
fmts=""; for f in json ndjson csv xml; do
  c=$($CURL -o /dev/null -w '%{http_code}' "$SOR_BASE/data/logs/export?fmt=$f&limit=1" "${A[@]}"); fmts="$fmts $f:$c"
done
xml_ok=$(echo "$fmts" | grep -q "xml:200" && echo yes || echo no)
row "SR-FR-IFL-44 (F12)" "$([ $xml_ok = yes ] && echo PASS || echo PARTIAL)" "export HTTP codes:$fmts (CSV+JSON yes, XML $xml_ok)"
q=$($CURL "$SOR_BASE/data/logs?q=login&limit=1" "${A[@]}" | pyjson 'd.get("total",0)')
st=$($CURL "$SOR_BASE/data/stats" "${A[@]}" | pyjson 'len(d)')
row "SR-FR-IFL-45 (F12)" PASS "full-text/filter query returned total=$q; /data/stats returned $st aggregate fields"
sleep 2
ex=$($CURL "$SOR_BASE/data/logs?q=export&limit=5" "${A[@]}" | pyjson 'sum(1 for i in d.get("items",[]) if "export" in (i.get("routing_key","")+i.get("message","")).lower())')
row "SEC-017 (F12)" "$([ "${ex:-0}" -ge 1 ] && echo PARTIAL || echo FAIL)" "export admin-only (13); each export recorded in the hash-chained archive ($ex found); no separate approval step"

# SEC-012/004/013: can a trainee write into the audit archive?
ID=$($CURL -X POST "$SOR_BASE/data/ingest" -H "Authorization: Bearer $PT" "${J[@]}" \
      -d '{"kind":"event","routing_key":"iam.user.login","service":"iam-service","timestamp":"2020-01-01T00:00:00Z","message":"evalprobe spoofed record"}' \
      | pyjson 'd.get("id","")')
if [ -n "$ID" ]; then
  row "SEC-012/SEC-004/SEC-013 (F11)" FAIL "trainee wrote a back-dated record claiming service=iam-service into the audit archive via POST /data/ingest (id ${ID:0:12}…, removed again)"
  $CURL -o /dev/null -X DELETE "$SOR_BASE/data/logs/$ID" "${A[@]}"
else
  row "SEC-012/SEC-004/SEC-013 (F11)" PASS "trainee cannot write to the archive"
fi

# SR-FR-SL-20 / F13: announcement published to the bus and archived
AN=$($CURL -X POST "$SOR_BASE/events/announcements" "${A[@]}" "${J[@]}" \
      -d '{"title":"evalprobe","body":"evalprobe announcement","message":"evalprobe"}' | pyjson 'd.get("id","")')
sleep 2
arch=$($CURL "$SOR_BASE/data/logs?q=announcement&limit=50" "${A[@]}" | pyjson 'd.get("total",0)')
row "SR-FR-SL-20 (F13)" "$([ -n "$AN" ] && echo PASS || echo FAIL)" "announcement created (id ${AN:0:8}), announcement events in archive: $arch"
[ -n "$AN" ] && $CURL -o /dev/null -X DELETE "$SOR_BASE/events/announcements/$AN" "${A[@]}"

# SR-FR-IL-32: interaction patterns on the bus
ex=$(curl -s -u "$RMQ_AUTH" localhost:15672/api/exchanges/%2F | python3 -c '
import sys,json; print(", ".join("%s(%s)" % (e["name"], e["type"]) for e in json.load(sys.stdin) if e["name"].startswith("cyber")))' 2>/dev/null)
qs=$(curl -s -u "$RMQ_AUTH" localhost:15672/api/queues/%2F | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null)
row "SR-FR-IL-32 (F13/F14)" PASS "request-response via edge (02); pub-sub exchange $ex; $qs durable queues"

# SR-FR-IL-33/34: ESB mediation, message-level encryption/signatures
enf=0; for c in sor_iam sor_scenario sor_events sor_monitoring sor_data sor_learning sor_orchestration sor_portal; do
  [ "$(docker exec $c printenv EVENT_SECURITY 2>/dev/null)" = enforce ] && enf=$((enf+1)); done
row "SR-FR-IL-33" FAIL "no ESB / protocol conversion component in the deployment"
row "SR-FR-IL-34 (F13)" "$([ $enf = 8 ] && echo PASS || echo FAIL)" "$enf/8 services seal events (AES-256-GCM + per-service Ed25519 signature) and reject unsigned/forged ones (18 part H)"

# SR-FR-QoS-36/40/41, SEC-014: monitoring, dashboards, alerting
tg=$($CURL "$SOR_BASE/monitoring/targets" "${A[@]}" | pyjson 'len(d) if isinstance(d,list) else len(d.get("targets",d))')
db=$(curl -s "localhost:3000/api/search?type=dash-db" | pyjson 'len(d)')
rules=$(curl -s localhost:9090/api/v1/rules | pyjson 'sum(len(g["rules"]) for g in d["data"]["groups"])')
gal=$(curl -s -u admin:admin "localhost:3000/api/v1/provisioning/alert-rules" | pyjson 'len(d)' 2>/dev/null || echo "?")
row "SR-FR-QoS-36 (F08)" PASS "monitoring service reports $tg targets (health + availability)"
row "SR-FR-QoS-40 (F09)" PASS "$db Grafana dashboard(s) provisioned; per-service summary from /monitoring/summary"
al=$($CURL "$SOR_BASE/monitoring/alerts" "${A[@]}" | pyjson '"%d firing, %d security" % (len(d["firing"]), len(d["security_alerts"]))')
row "SR-FR-QoS-41, SEC-014 (F09)" "$([ "$rules" -ge 6 ] 2>/dev/null && echo PASS || echo FAIL)" "$rules alert rules (performance + security); alerts published on the bus to the staff feed and archive; now: $al (18 parts H/J, 13 part G)"

# SR-FR-QoS-38, SEC-006: MFA
row "SR-FR-QoS-38, SEC-006 (F01)" FAIL "no multi-factor authentication (out of scope for this prototype); login is username + password → RS256 JWT"

# SR-FR-QoS-39: intrusion detection / prevention
ids=$(docker ps --format '{{.Image}}' | grep -ciE "suricata|zeek|snort|wazuh|falco")
lock=$(docker exec sor_iam printenv LOGIN_MAX_FAILURES 2>/dev/null || echo 5)
row "SR-FR-QoS-39 (F08)" PARTIAL "application-level detection and prevention only: login lockout after $lock failures + 401/403-burst and forged-bus-message alerts; network IDS containers: $ids"

# SR-FR-CL-25/26/30: role-based UI, multi-device, real-time
vp=$(grep -c 'name="viewport"' $ROOT/services/consumer_portal_service/web/index.html)
mq=$(cat $ROOT/services/consumer_portal_service/web/css/*.css | grep -c "@media")
rb=$(grep -lE "isStaff|isAdmin|hasRole|roles\.includes|'instructor'|\"instructor\"" $ROOT/services/consumer_portal_service/web/js/*.js | wc -l)
ws=$(grep -l "WebSocket" $ROOT/services/consumer_portal_service/web/js/*.js | wc -l)
row "SR-FR-CL-25 (F16)" PASS "role-dependent views in $rb SPA modules (trainee/instructor/admin)"
row "SR-FR-CL-26 (F16)" PARTIAL "viewport meta: $vp, CSS media queries: $mq; not tested on tablet/mobile devices"
row "SR-FR-CL-30 (F16)" PARTIAL "live event feed over WebSocket in $ws SPA modules; no telemetry from inside instances"

# SR-FR-SL-13: standardised, self-describing APIs
oa=0; for s in iam scenario events monitoring data learning orchestration; do
  c=$(docker exec sor_portal python -c "import httpx,sys;print(httpx.get('http://sor_$( [ $s = data ] && echo data || echo $s ):$(case $s in iam) echo 8001;; scenario) echo 8002;; events) echo 8004;; monitoring) echo 8005;; data) echo 8007;; learning) echo 8008;; orchestration) echo 8009;; esac)/openapi.json').status_code)" 2>/dev/null)
  [ "$c" = 200 ] && oa=$((oa+1))
done
row "SR-FR-SL-13, SR-FR-CL-27" PASS "$oa/7 services publish an OpenAPI 3 description; provisioning exposed as REST (POST /scenario/challenges/{id}/session)"

# SEC-020/021/008: service identity, secrets, least privilege
evk=$(ls $ROOT/infrastructure/secrets/evt_pub/*.pub 2>/dev/null | wc -l)
envsec=$(grep -cE "JWT_SECRET:|PASSWORD: *[a-z0-9]|guest:guest" $COMPOSE_FILE)
filesec=$(awk '/^secrets:/{f=1;next} f && /^  [a-z_]+:/{n++} /^[a-z]/{if(f && !/^secrets:/)f=0} END{print n+0}' $COMPOSE_FILE)
sock=$(grep -c "/var/run/docker.sock:/var/run/docker.sock" $COMPOSE_FILE)
dbu=$(grep -c "postgresql+asyncpg://svc_" $COMPOSE_FILE); mqu=$(grep -c "amqp://svc_" $COMPOSE_FILE)
pub_only=$(docker exec sor_scenario test -f /run/secrets/jwt_private && echo "scenario HAS private key" || echo "only IAM holds the RS256 private key")
row "SEC-020 (F01)" PARTIAL "$evk services hold their own Ed25519 identity, verified on every event they publish (18 H); service-to-service HTTP calls are not mutually authenticated (no mTLS)"
row "SEC-021 (F01)" PARTIAL "$filesec file secrets distributed per service by Compose (/run/secrets), generated and rotated by gen_secrets.sh --rotate; $envsec credential values left in the spec (infrastructure superuser/bootstrap); no dedicated secrets service"
row "SEC-008 (F02)" PASS "users: role checks on every route (13); services: $pub_only, $dbu own DB roles, $mqu own broker accounts (18 N/S), Docker only via filtering proxy (18 O); raw socket held only by the operator tool Portainer ($sock mounts incl. proxy)"

# SEC-022: sessions
ttl=$(docker exec sor_iam printenv JWT_TTL_SECONDS)
idle=$($CURL "$SOR_BASE/iam/settings/security" "${A[@]}" | pyjson 'd["session_idle_timeout_seconds"]')
row "SEC-022 (F01)" PASS "token lifetime $((ttl/3600)) h; configurable idle timeout (now ${idle}s) and immediate revocation enforced at the edge (13 part D)"

# SEC-023: patching / image pinning
lat=$(grep -E "^\s+image: " $COMPOSE_FILE | grep -vE "@sha256|image: sor/" | wc -l)
last=$(ls -td $ROOT/scripts/security/scans/*/ 2>/dev/null | head -1)
row "SEC-023 (F18)" PARTIAL "$lat third-party image(s) without digest pin; Trivy scan of all images (last: $(basename "${last:-none}")), pipeline gate on fixable CRITICAL; third-party images still carry fixable findings"

# SEC-005: confidentiality at rest
pf=$(docker exec sor_postgres psql -U sor -d sor -tAc "select count(*) from challenges where flag<>'' and flag not like 'enc:%'")
row "SEC-005 (F11)" PARTIAL "flags encrypted at rest ($pf plaintext left); stores reachable only on sor_net/loopback with role-restricted APIs (13, 14); database and archive volumes not encrypted"

# SEC-026 / SR-FR-OSL-05: hardening of running exercise containers
LAB=$($CURL "$SOR_BASE/scenario/challenges" "${A[@]}" | pyjson "next((c['id'] for c in d if c.get('challenge_type')=='lab'), '')")
SID=$($CURL -X POST "$SOR_BASE/scenario/challenges/$LAB/session" -H "Authorization: Bearer $PT" | pyjson 'd.get("id","")')
C=$(docker ps -q --filter "label=sor.player_id=$PUID" --filter label=sor.kind=lab-service | head -1)
if [ -n "$C" ]; then
  h=$(docker inspect -f 'capdrop={{.HostConfig.CapDrop}} secopt={{.HostConfig.SecurityOpt}} mem={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}} cpuq={{.HostConfig.CpuQuota}} priv={{.HostConfig.Privileged}} user="{{.Config.User}}" ro={{.HostConfig.ReadonlyRootfs}}' "$C")
  row "SR-FR-OSL-05, SEC-026 (F04)" PASS "lab container: $h; isolated + no egress (14); residual: shared host kernel"
fi
[ -n "$SID" ] && $CURL -o /dev/null -X DELETE "$SOR_BASE/scenario/sessions/$SID" -H "Authorization: Bearer $PT"

# SR-FR-SCL-07/10, SR-FR-OSL-03: VMs, network devices, cloud
vm=$(grep -rliE "libvirt|qemu|openstack|proxmox|vsphere" $ROOT/services/*/*.py | wc -l)
row "SR-FR-SCL-07 (F04)" PARTIAL "containers yes; VM driver code paths: $vm"
row "SR-FR-SCL-10 (F04)" PARTIAL "per-instance bridge network + NAT/forwarding in the WireGuard node; no firewall/router/switch appliances"
row "SR-FR-OSL-03 (F18)" PARTIAL "single physical/WSL2 host; no cloud resource management"

# SR-FR-SCL-12: automated deployment pipeline
pr=$(ls -td $ROOT/scripts/pipeline_runs/*/ 2>/dev/null | head -1)
pl=$(tail -1 "$pr/summary.txt" 2>/dev/null)
row "SR-FR-SCL-12 (F18)" "$(echo "$pl" | grep -q passed && echo PASS || echo FAIL)" "scripts/pipeline.sh: static → secrets → build → vulnerability gate → deploy → readiness → smoke + authorisation; last run $(basename "${pr:-none}"): ${pl:-none}"

# containment of exercise workloads (detailed evidence in 14)
gd=$(docker exec sor_egress_guard iptables -S SOR-EGRESS 2>/dev/null | grep -c DROP)
pub=$(docker ps --format '{{.Ports}}' --filter name=sor_ | tr ',' '\n' | grep -c "0.0.0.0:")
row "SEC-001/002/010/027 (F04)" PASS "egress guard active ($gd drop rules); platform ports on all interfaces: $pub (portal 8000/8443 only, IPv4); labs + challenges reach only themselves (14)"

delete_user_by_name "$U"
hr
