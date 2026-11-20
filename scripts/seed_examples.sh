#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
. scripts/eval/_lib.sh
admin_token

existing=$($CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "'\n'.join(c['title'] for c in d)")

create() {  # create <title> <type> <flag> <difficulty> <file>
  if grep -qxF "$1" <<<"$existing"; then echo "skip   $1 (exists)"; return; fi
  out=$($CURL -X POST "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
    --form-string "title=$1" --form-string "challenge_type=$2" --form-string "flag=$3" \
    --form-string "difficulty=$4" --form-string "category=example" \
    --form-string "description=Example exercise shipped with the repository." \
    -F "file=@$5")
  echo "$out" | pyjson "'created ' + d['title'] + ' (' + d['id'][:8] + ')' if 'id' in d else 'FAILED $1: ' + str(d)"
}

TAR=$(mktemp --suffix=.tar); trap 'rm -f "$TAR"' EXIT
tar -C "$ROOT/examples/challenges/web-flag" -cf "$TAR" .

create "Recon Lab"      lab    'FLAG{l4b_smoke_2026}'        easy   "$ROOT/examples/labs/recon-lab.yml"
create "Three-Tier Lab" lab    'FLAG{multi_endpoint_2026}'   medium "$ROOT/examples/labs/three-tier-lab.yml"
create "Web Flag"       docker 'FLAG{single_container_2026}' easy   "$TAR"
