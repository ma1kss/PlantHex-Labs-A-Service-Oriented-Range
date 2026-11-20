#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh
admin_token
IDS=$($CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" | pyjson 'json.dumps([c["id"] for c in d])')
$CURL -X POST "$SOR_BASE/learning/maintenance/reconcile" -H "Authorization: Bearer $ADMIN_TOK" \
  -H 'Content-Type: application/json' -d "{\"valid_challenge_ids\": $IDS}"
echo
