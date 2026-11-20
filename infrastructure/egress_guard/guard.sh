#!/bin/sh
set -u
LAB_POOL="${LAB_POOL:-10.10.0.0/16}"
CHALLENGE_SUBNET="${CHALLENGE_SUBNET:-172.20.0.0/16}"
INTERVAL="${INTERVAL:-30}"
IPT="${IPT:-iptables}"

chain() {  # chain <name>: create or flush
  $IPT -N "$1" 2>/dev/null || $IPT -F "$1"
}
jump_first() {  # jump_first <from> <to>
  $IPT -C "$1" -j "$2" 2>/dev/null || $IPT -I "$1" 1 -j "$2"
}

apply() {
  $IPT -S DOCKER-USER >/dev/null 2>&1 || $IPT -N DOCKER-USER
  chain SOR-EGRESS
  $IPT -A SOR-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  $IPT -A SOR-EGRESS -s "$LAB_POOL" -d "$LAB_POOL" -j RETURN
  $IPT -A SOR-EGRESS -s "$LAB_POOL" -m comment --comment "sor:lab-egress" -j DROP
  $IPT -A SOR-EGRESS -s "$CHALLENGE_SUBNET" -d "$CHALLENGE_SUBNET" -j RETURN
  $IPT -A SOR-EGRESS -s "$CHALLENGE_SUBNET" -m comment --comment "sor:challenge-egress" -j DROP
  jump_first DOCKER-USER SOR-EGRESS

  chain SOR-HOSTGUARD
  $IPT -A SOR-HOSTGUARD -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  $IPT -A SOR-HOSTGUARD -s "$LAB_POOL" -m comment --comment "sor:lab-to-host" -j DROP
  $IPT -A SOR-HOSTGUARD -s "$CHALLENGE_SUBNET" -m comment --comment "sor:challenge-to-host" -j DROP
  jump_first INPUT SOR-HOSTGUARD
}

healthy() {  # rules present and jumped to → nothing to do (no flush window)
  [ "$($IPT -S SOR-EGRESS 2>/dev/null | wc -l)" = 6 ] &&
  [ "$($IPT -S SOR-HOSTGUARD 2>/dev/null | wc -l)" = 4 ] &&
  $IPT -C DOCKER-USER -j SOR-EGRESS 2>/dev/null &&
  $IPT -C INPUT -j SOR-HOSTGUARD 2>/dev/null
}

remove() {
  $IPT -D DOCKER-USER -j SOR-EGRESS 2>/dev/null
  $IPT -D INPUT -j SOR-HOSTGUARD 2>/dev/null
  for c in SOR-EGRESS SOR-HOSTGUARD; do $IPT -F "$c" 2>/dev/null; $IPT -X "$c" 2>/dev/null; done
  echo "egress guard rules removed"
}

case "${1:-run}" in
  --remove) remove; exit 0 ;;
  --once)   apply; exit $? ;;
esac

echo "egress guard: lab pool $LAB_POOL, challenge net $CHALLENGE_SUBNET, every ${INTERVAL}s"
first=1
while true; do
  if healthy; then
    :
  elif apply; then
    [ "$first" = 1 ] && { echo "rules applied"; $IPT -S SOR-EGRESS; $IPT -S SOR-HOSTGUARD; first=0; }
  else
    echo "WARNING: could not apply rules" >&2
  fi
  sleep "$INTERVAL" & wait $!
done
