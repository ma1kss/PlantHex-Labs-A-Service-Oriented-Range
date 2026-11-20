#!/usr/bin/env bash
set -u
. "$(dirname "$0")/_lib.sh"
RABBIT_MGMT="${RABBIT_MGMT:-http://localhost:15672}"
RABBIT_AUTH="${RABBIT_AUTH:-${RMQ_AUTH:-}}"

echo "=== RabbitMQ event bus ($(date -Is)) ==="
curl -s -u "$RABBIT_AUTH" "$RABBIT_MGMT/api/queues" | python3 -c "
import sys, json
qs = json.load(sys.stdin)
print('QUEUE'.ljust(38), 'READY'.rjust(6), 'UNACK'.rjust(6),
      'CONSUMERS'.rjust(9), 'IN msg/s'.rjust(9), 'OUT msg/s'.rjust(9))
for q in sorted(qs, key=lambda x: x.get('name', '')):
    nm    = q.get('name', '?')[:38]
    stats = q.get('message_stats', {})
    inr   = stats.get('publish_details', {}).get('rate', 0.0)
    outr  = stats.get('deliver_get_details', {}).get('rate', 0.0)
    print(nm.ljust(38), str(q.get('messages_ready', 0)).rjust(6),
          str(q.get('messages_unacknowledged', 0)).rjust(6),
          str(q.get('consumers', 0)).rjust(9),
          ('%.2f' % inr).rjust(9), ('%.2f' % outr).rjust(9))
warn = [q['name'] for q in qs if q.get('messages_ready', 0) > 100]
print()
print('WARNING queue backlog >100:', ', '.join(warn) if warn else '(none)')
no_cons = [q['name'] for q in qs if q.get('consumers', 0) == 0]
print('queues without consumers  :', ', '.join(no_cons) if no_cons else '(none)')
"
