#!/bin/sh
set -eu
API="http://sor_rabbitmq:15672/api"
ADMIN_PW="$(cat /run/secrets/rmq_admin)"
EX='cyber_range\\.events'

# The broker's health check (diagnostics ping) passes before the management
# API listens; wait for the API (any HTTP answer) for up to 120 s.
for i in $(seq 120); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$API/overview" || true)
  [ "$code" != "000" ] && break
  sleep 1
done

if curl -fsS -u "sor_admin:$ADMIN_PW" "$API/whoami" >/dev/null 2>&1; then AUTH="sor_admin:$ADMIN_PW"
else AUTH="guest:guest"; fi
put() { curl -fsS -u "$AUTH" -H 'content-type: application/json' -X PUT "$API/$1" -d "$2" >/dev/null; }

put "users/sor_admin" "{\"password\":\"$ADMIN_PW\",\"tags\":\"administrator\"}"
put "permissions/%2F/sor_admin" '{"configure":".*","write":".*","read":".*"}'
AUTH="sor_admin:$ADMIN_PW"

user() {  # user <secret-name> <queue-or-empty>
  name="svc_$1"; pw="$(cat /run/secrets/rmq_$1)"; q="$2"
  if [ -n "$q" ]; then
    re="^($EX|$(echo "$q" | sed 's/\./\\\\./g'))\$"; rd="$re"
  else
    re="^$EX\$"; rd="^\$"
  fi
  put "users/$name" "{\"password\":\"$pw\",\"tags\":\"\"}"
  put "permissions/%2F/$name" "{\"configure\":\"$re\",\"write\":\"$re\",\"read\":\"$rd\"}"
  echo "broker user $name: queue=${q:-<publish only>}"
}
user iam           ""
user scenario      ""
user orchestration ""
user events        events-service.log
user data          data-logging.capture
user learning      learning-service.scoring
user monitoring    monitoring.security-alerts
user portal        portal.sessions

curl -fsS -u "$AUTH" -X DELETE "$API/users/guest" >/dev/null 2>&1 && echo "default guest account removed" || true
echo "rabbit-init: done"
