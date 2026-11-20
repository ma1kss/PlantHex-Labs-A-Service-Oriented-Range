#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh

admin_token
echo "=== Orphan participant purge ($(date -Is)) ==="
VALID=$($CURL "$SOR_BASE/iam/users" -H "Authorization: Bearer $ADMIN_TOK" | pyjson '",".join(u["id"] for u in d)')
ORPHANS=$($CURL "$SOR_BASE/learning/participants" -H "Authorization: Bearer $ADMIN_TOK" \
  | python3 -c "
import sys, json
valid = set('$VALID'.split(','))
print('\n'.join(r['user_id'] for r in json.load(sys.stdin) if r['user_id'] not in valid))")

if [ -z "$ORPHANS" ]; then
  echo "No orphaned participants — scoreboard is consistent with IAM."
  exit 0
fi
echo "$ORPHANS" | while read -r uid; do
  [ -n "$uid" ] && $CURL -o /dev/null -w "purged $uid: HTTP %{http_code}\n" \
    -X DELETE "$SOR_BASE/learning/participants/$uid" -H "Authorization: Bearer $ADMIN_TOK"
done
