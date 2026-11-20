#!/usr/bin/env bash
set -u
PROM="${PROM:-http://localhost:9090}"

q() {  # q <label> <promql>
  local label="$1" expr="$2"
  echo "── $label ──"
  curl -s "$PROM/api/v1/query" --data-urlencode "query=$expr" | python3 -c '
import sys, json
r = json.load(sys.stdin)
res = r.get("data", {}).get("result", [])
if not res:
    print("  (no data)")
for row in sorted(res, key=lambda x: str(x["metric"])):
    m = row["metric"]
    name = m.get("service") or m.get("job") or m.get("instance") or "?"
    extra = m.get("handler", "")
    val = float(row["value"][1])
    print(f"  {name:28} {extra:34} {val:.4f}")'
  echo
}

echo "=== Prometheus snapshot ($(date -Is)) ==="
q "scrape targets up (1 = healthy)"          'up'
q "request rate by service (req/s, 5m)"       'sum by (service) (rate(http_requests_total[5m]))'
q "5xx error rate by service (req/s, 5m)"     'sum by (service) (rate(http_requests_total{status=~"5.."}[5m]))'
q "p95 request latency by service (s, 5m)"    'histogram_quantile(0.95, sum by (service, le) (rate(http_request_duration_seconds_bucket[5m])))'
q "in-progress requests by service"           'sum by (service) (http_requests_inprogress)'
