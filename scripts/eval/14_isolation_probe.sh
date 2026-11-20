#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
PROBE_IMG="${PROBE_IMG:-python:3.11-slim}"
SUF=$RANDOM
WGC="sor_eval_wgc_$SUF"
OUT_JSON="${ISO_JSON:-$(mktemp)}"

admin_token
docker image inspect "$PROBE_IMG" >/dev/null 2>&1 || docker pull -q "$PROBE_IMG" >/dev/null

read -r LAB_ID < <($CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "next((c['id'] for c in sorted(d, key=lambda c: c.get('lab_services') or 9) if c.get('challenge_type')=='lab'), '')")
read -r SINGLE_ID < <($CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "next((c['id'] for c in d if c.get('challenge_type')=='docker'), '')")
[ -z "$LAB_ID" ] && { echo "No lab challenge exists — create one first."; exit 1; }

PA="evalisoA$SUF"; PB="evalisoB$SUF"
TOK_A=$(make_temp_player "$PA"); TOK_B=$(make_temp_player "$PB")

cleanup() {
  docker rm -f "$WGC" >/dev/null 2>&1
  for pair in "$TOK_A ${SID_A:-}" "$TOK_B ${SID_B:-}" "$TOK_B ${SID_C:-}"; do
    set -- $pair; [ -n "${2:-}" ] && $CURL -o /dev/null -X DELETE "$SOR_BASE/scenario/sessions/$2" -H "Authorization: Bearer $1"
  done
  delete_user_by_name "$PA"; delete_user_by_name "$PB"
}
trap cleanup EXIT

launch() { $CURL -X POST "$SOR_BASE/scenario/challenges/$1/session" -H "Authorization: Bearer $2"; }
J_A=$(launch "$LAB_ID" "$TOK_A"); SID_A=$(echo "$J_A" | pyjson 'd.get("id","")')
J_B=$(launch "$LAB_ID" "$TOK_B"); SID_B=$(echo "$J_B" | pyjson 'd.get("id","")')
J_C='{}'; SID_C=""
if [ -n "$SINGLE_ID" ]; then
  J_C=$(launch "$SINGLE_ID" "$TOK_B"); SID_C=$(echo "$J_C" | pyjson 'd.get("id","")')
fi
[ -z "$SID_A" ] || [ -z "$SID_B" ] && { echo "FATAL: lab launch failed: $J_A $J_B"; exit 1; }

A_UID=$(echo "$J_A" | pyjson 'd["user_id"]')
A_SUBNET=$(echo "$J_A" | pyjson 'd["subnet"]'); B_SUBNET=$(echo "$J_B" | pyjson 'd["subnet"]')
A_GW="${A_SUBNET%.0/24}.1"
A_WGPORT=$(echo "$J_A" | pyjson 'd["ports"].get("51820/udp","")')
A_SVC=$(docker ps -q --filter "label=sor.player_id=$A_UID" --filter label=sor.kind=lab-service | head -1)
A_WG=$(docker ps -q --filter "label=sor.player_id=$A_UID" --filter label=sor.kind=lab-wireguard | head -1)
HOST_LAN=$(ip -4 -o addr show scope global | awk '$2 !~ /^(lo|docker|br-|veth|wg)/ {split($4,a,"/"); print a[1]; exit}')
DOCKER0=$(docker network inspect bridge -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}')
SORNET=$(docker network inspect sor_net -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}')
CHALNET=$(docker network inspect sor_chal_net -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}')
C_IP=""; C_PORT=""
if [ -n "$SID_C" ]; then
  C_CID=$(echo "$J_C" | pyjson 'd.get("container_id","")')
  C_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$C_CID" | awk '{print $1}')
  C_PORT=$(echo "$J_C" | pyjson 'next(iter(d.get("ports") or {"80/tcp":""})).split("/")[0]')
fi

# Control-plane containers with their listening ports.
CP=""
for pair in sor_iam:8001 sor_scenario:8002 sor_events:8004 sor_monitoring:8005 sor_portal:8006 \
            sor_data:8007 sor_learning:8008 sor_orchestration:8009 sor_consul:8500 sor_rabbitmq:5672 \
            sor_postgres:5432 sor_opensearch:9200 sor_prometheus:9090 sor_grafana:3000 sor_portainer:9000; do
  n=${pair%%:*}; p=${pair##*:}
  ip=$(docker inspect -f '{{with index .NetworkSettings.Networks "sor_net"}}{{.IPAddress}}{{end}}' "$n" 2>/dev/null)
  [ -n "$ip" ] && CP="$CP $n:$ip:$p"
done

TARGETS=$(J_A="$J_A" J_B="$J_B" CP="$CP" C_IP="$C_IP" C_PORT="$C_PORT" A_GW="$A_GW" \
          DOCKER0="$DOCKER0" HOST_LAN="$HOST_LAN" python3 - <<'EOF'
import json, os, re
def eps(j):
    out = []
    for e in json.loads(j).get("lab_endpoints") or []:
        ports = [int(re.match(r"\d+", p).group()) for p in (e.get("ports") or []) if re.match(r"\d+", p)] or [8080, 80, 22]
        out += [(e["name"], e["ip"], p) for p in ports]
    return out
t = []
t += [["own-lab", f"A:{n}", ip, p] for n, ip, p in eps(os.environ["J_A"])]
t += [["other-lab", f"B:{n}", ip, p] for n, ip, p in eps(os.environ["J_B"])]
if os.environ["C_IP"]:
    t.append(["other-challenge", "B:single-container", os.environ["C_IP"], int(os.environ["C_PORT"] or 80)])
for item in os.environ["CP"].split():
    n, ip, p = item.split(":")
    t.append(["control-plane", n, ip, int(p)])
host_ports = {8000: "portal-edge", 8443: "portal-edge", 5432: "postgres", 5672: "rabbitmq",
              15672: "rabbitmq-ui", 8500: "consul", 9200: "opensearch", 9000: "portainer",
              9443: "portainer-tls", 9090: "prometheus", 3000: "grafana"}
for hname, hip in (("lab-gw", os.environ["A_GW"]), ("docker0", os.environ["DOCKER0"]),
                   ("host-lan", os.environ["HOST_LAN"])):
    for p, svc in host_ports.items():
        grp = "host-edge" if svc == "portal-edge" else "host-internal"
        t.append([grp, f"{hname}:{svc}/{p}", hip, p])
t += [["internet", "1.1.1.1:443", "1.1.1.1", 443], ["internet", "8.8.8.8:53", "8.8.8.8", 53]]
print(json.dumps(t))
EOF
)
NAMES='["sor_iam","sor_postgres","sor_consul","sor_rabbitmq","sor_opensearch","iam","postgres","consul"]'

run_probe() {  # run_probe <vantage-label> <container-id> [only-cidrs]
  docker run --rm --network "container:$2" -e TARGETS="$TARGETS" -e NAMES="$NAMES" -e ONLY_CIDRS="${3:-}" \
    -v "$PWD/14_netprobe.py:/p.py:ro" "$PROBE_IMG" python /p.py \
  | python3 -c "import sys,json; d=json.load(sys.stdin); d['vantage']='$1'; print(json.dumps(d))"
}

echo "=== Isolation probes ($(date -Is)) ==="
echo "instance A: $A_SUBNET (wg udp/$A_WGPORT)   instance B: $B_SUBNET"
echo "single-container challenge of B: ${C_IP:-none}:${C_PORT:-}   sor_net: $SORNET   chal_net: $CHALNET"
echo "host: lab-gw $A_GW · docker0 $DOCKER0 · LAN $HOST_LAN"
hr

: > "$OUT_JSON"
run_probe "V1 lab container" "$A_SVC" >> "$OUT_JSON"
run_probe "V2 lab WireGuard server" "$A_WG" >> "$OUT_JSON"
# V5: inside another trainee's single-container challenge (sor_chal_net).
[ -n "${C_CID:-}" ] && run_probe "V5 single-container challenge (B)" "$C_CID" >> "$OUT_JSON"

# V3/V4: a real WireGuard client in its own container
docker run -d --name "$WGC" --cap-add NET_ADMIN \
  --sysctl net.ipv4.conf.all.src_valid_mark=1 --entrypoint sh sor/wireguard:latest -c 'sleep 900' >/dev/null
CONF=$($CURL "$SOR_BASE/scenario/sessions/$SID_A/wireguard.conf" -H "Authorization: Bearer $TOK_A")
# The profile names the host as localhost; from a container the host is docker0.
CONF_V3=$(echo "$CONF" | sed "s#^Endpoint = .*:#Endpoint = $DOCKER0:#")
FIRST_WEB=$(echo "$J_A" | pyjson '"%s:%s" % next(((e["ip"], (e.get("ports") or ["8080"])[0].split("/")[0]) for e in d["lab_endpoints"]), ("",""))')

tunnel_up() {  # tunnel_up <conf>
  docker exec "$WGC" sh -c 'wg-quick down wgc >/dev/null 2>&1; true'
  echo "$1" | docker exec -i "$WGC" sh -c 'umask 077; cat > /etc/wireguard/wgc.conf && wg-quick up wgc >/dev/null 2>&1'
}
T0=$(now_ms); tunnel_up "$CONF_V3"; T1=$(now_ms)
FIRST_MS="n/a"
if docker exec "$WGC" wget -qO /dev/null -T 5 "http://$FIRST_WEB/" 2>/dev/null; then
  T2=$(now_ms); FIRST_MS="$((T2 - T1))"
fi
HS=$(docker exec "$WGC" wg show wgc latest-handshakes | awk '{print $2}')
echo "V3 tunnel: wg-quick up $((T1 - T0)) ms · first HTTP request through tunnel to $FIRST_WEB: ${FIRST_MS} ms · handshake ts ${HS:-none}"
# Only destinations the tunnel routes are meaningful from the client: anything
# else would leave via the client's own uplink, not the VPN.
run_probe "V3 VPN client (issued profile)" "$WGC" "$A_SUBNET" >> "$OUT_JSON"

# Read-only impact check through the tunnel (issued profile): what does the
# host-gateway exposure actually give a trainee? Nothing is modified.
echo "V3 impact (read-only, via tunnel → lab gateway $A_GW):"
idx=$(docker exec "$WGC" wget -qO- -T 5 "http://$A_GW:9200/_cat/indices?h=index,docs.count" 2>/dev/null | tr '\n' ' ')
echo "  OpenSearch audit archive (no auth): ${idx:-not readable}"
svcs=$(docker exec "$WGC" wget -qO- -T 5 "http://$A_GW:8500/v1/catalog/services" 2>/dev/null | head -c 300)
echo "  Consul catalogue (no ACL)         : ${svcs:-not readable}"
acl=$(docker exec "$WGC" wget -qO- -T 5 "http://$A_GW:8500/v1/acl/bootstrap" 2>&1 | head -c 120)
echo "  Consul ACL system                 : $(echo "$acl" | grep -qi 'ACL support disabled\|404\|400' && echo disabled || echo "${acl:-unknown}")"
pt=$(docker exec "$WGC" wget -qO- -T 5 "http://$A_GW:9090/api/v1/targets?state=active" 2>/dev/null | head -c 2000 | grep -o '"job":"[^"]*"' | sort -u | wc -l)
echo "  Prometheus API (no auth)          : $pt scrape jobs readable"
echo "$idx$svcs" > /dev/null

WIDE="10.10.0.0/16, $SORNET, $CHALNET, $HOST_LAN/32, 1.1.1.1/32, 8.8.8.8/32"
CONF_V4=$(echo "$CONF_V3" | sed "s#^AllowedIPs = .*#AllowedIPs = $WIDE#")
tunnel_up "$CONF_V4"
echo "V4 tunnel: AllowedIPs widened by the trainee to: $WIDE"
run_probe "V4 VPN client (widened AllowedIPs)" "$WGC" "$WIDE" >> "$OUT_JSON"
hr

python3 - "$OUT_JSON" <<'EOF'
import json, sys, collections
runs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
for r in runs:   # from B's challenge, A's lab is foreign and the challenge is "own"
    if r["vantage"].startswith("V5"):
        for x in r["results"]:
            x["group"] = {"own-lab": "other-lab", "other-challenge": "own-challenge"}.get(x["group"], x["group"])
groups = ["own-lab", "own-challenge", "other-lab", "other-challenge", "control-plane", "host-edge", "host-internal", "internet"]
fails = 0
for r in runs:
    print(f"\n{r['vantage']}")
    by = collections.defaultdict(list)
    for x in r["results"]:
        by[x["group"]].append(x)
    for g in groups:
        xs = by.get(g, [])
        if not xs: continue
        c = collections.Counter(x["result"] for x in xs)
        opened = [x["label"] for x in xs if x["result"] == "open"]
        print(f"  {g:16s} n={len(xs):2d}  open={c['open']:2d} refused={c['refused']:2d} filtered={c['filtered']:2d}"
              + (f"   open: {', '.join(opened)}" if opened and g not in ('own-lab',) else ""))
    leaked = [n for n, ip in r["dns"].items() if ip]
    print(f"  control-plane DNS names resolvable: {', '.join(leaked) if leaked else 'none'}")
    if any(x["result"] == "open" for x in by.get("other-lab", []) + by.get("other-challenge", []) + by.get("control-plane", [])) or leaked:
        fails += 1
pc = all(any(x["result"] == "open" for x in r["results"] if x["group"] in ("own-lab", "own-challenge")) for r in runs)
print("\nVERDICT")
print("  positive control (own instance reachable):", "PASS" if pc else "FAIL")
print("  cross-instance / control-plane isolation:", "PASS" if fails == 0 else f"FAIL ({fails} vantage points)")
hi = sorted({x["label"] for r in runs for x in r["results"] if x["group"] == "host-internal" and x["result"] == "open"})
print("  host-published internal ports reachable :", ", ".join(hi) if hi else "none")
eg = sorted({r["vantage"].split()[0] for r in runs for x in r["results"] if x["group"] == "internet" and x["result"] == "open"})
print("  internet egress open from              :", ", ".join(eg) if eg else "none")
he = sorted({x["label"] for r in runs for x in r["results"] if x["group"] == "host-edge" and x["result"] == "open"})
print("  host edge (portal) reachable from lab  :", ", ".join(he) if he else "none")
contained = not hi and not eg and not he
print("  containment (no host, no egress)       :", "PASS" if contained else "FAIL")
EOF
echo
echo "egress-guard drop counters (packets):"
docker exec sor_egress_guard iptables -L SOR-EGRESS -v -n -x 2>/dev/null \
  | awk '/DROP/ {match($0, /sor:[a-z-]+/); printf "  %-22s %s packets\n", substr($0, RSTART, RLENGTH), $1}'
docker exec sor_egress_guard iptables -L SOR-HOSTGUARD -v -n -x 2>/dev/null \
  | awk '/DROP/ {match($0, /sor:[a-z-]+/); printf "  %-22s %s packets\n", substr($0, RSTART, RLENGTH), $1}'
echo; echo "raw results: $OUT_JSON"
