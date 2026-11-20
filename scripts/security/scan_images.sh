#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
TRIVY="${TRIVY:-aquasec/trivy:0.58.1@sha256:ab70a02200597efa04748f210f793936eb647cbcdb0ea69cc30b226d6f5a22c7}"
SEVERITY="${SEVERITY:-HIGH,CRITICAL}"
OUT="scans/$(date -u +%Y%m%d_%H%M%S)"; mkdir -p "$OUT"
COMPOSE="docker compose -f ../../infrastructure/docker-compose.yml"
IMAGES=$($COMPOSE config --images | sort -u)

printf "%-62s %8s %8s %8s\n" IMAGE CRITICAL HIGH FIXABLE | tee "$OUT/summary.txt"
GATE=0
for img in $IMAGES; do
  safe=$(echo "$img" | tr '/:@' '___' | cut -c1-80)
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro -v trivy_cache:/root/.cache \
    "$TRIVY" image --quiet --scanners vuln --severity "$SEVERITY" --format json "$img" \
    > "$OUT/$safe.json" 2>/dev/null
  read -r crit high fix < <(python3 - "$OUT/$safe.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("? ? ?"); raise SystemExit
v = [x for r in d.get("Results", []) for x in (r.get("Vulnerabilities") or [])]
c = sum(1 for x in v if x["Severity"] == "CRITICAL"); h = sum(1 for x in v if x["Severity"] == "HIGH")
f = sum(1 for x in v if x.get("FixedVersion"))
print(c, h, f)
PY
)
  printf "%-62s %8s %8s %8s\n" "${img:0:62}" "$crit" "$high" "$fix" | tee -a "$OUT/summary.txt"
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(1 if any(x['Severity']=='CRITICAL' and x.get('FixedVersion') for r in d.get('Results',[]) for x in (r.get('Vulnerabilities') or [])) else 0)" "$OUT/$safe.json" 2>/dev/null || GATE=1
done
echo "reports: scripts/security/$OUT/" | tee -a "$OUT/summary.txt"
exit $GATE
