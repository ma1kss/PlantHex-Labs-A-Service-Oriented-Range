#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"; . ./_lib.sh

PASS=0; FAIL=0; INFO=0
ok()   { PASS=$((PASS+1)); printf "  PASS  %s\n" "$*"; }
bad()  { FAIL=$((FAIL+1)); printf "  FAIL  %s\n" "$*"; }
note() { INFO=$((INFO+1)); printf "  NOTE  %s\n" "$*"; }
code() { $CURL -o /dev/null -w '%{http_code}' "$@"; }

admin_token
SUF=$RANDOM
PA="evalazA$SUF"; PB="evalazB$SUF"; IN="evalazI$SUF"
TOK_PA=$(make_temp_player "$PA")
TOK_PB=$(make_temp_player "$PB")
$CURL -o /dev/null -X POST "$SOR_BASE/iam/auth/register" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ADMIN_TOK" \
  -d "{\"username\":\"$IN\",\"password\":\"evaltmp123\",\"roles\":[\"instructor\"]}"
TOK_IN=$(sor_token "$IN" evaltmp123)
[ -z "$TOK_PA" ] || [ -z "$TOK_PB" ] || [ -z "$TOK_IN" ] && { echo "FATAL: could not create probe accounts"; exit 1; }

uid_of() { $CURL "$SOR_BASE/iam/users" -H "Authorization: Bearer $ADMIN_TOK" \
             | pyjson "next((u['id'] for u in d if u['username']=='$1'), '')"; }
ADMIN_ID=$(uid_of "$SOR_ADMIN_USER"); PA_ID=$(uid_of "$PA"); IN_ID=$(uid_of "$IN")

echo "=== Authorisation probes ($(date -Is)) ==="
hr

# A + B + E: matrix runs inside the portal container (edge + sor_net)
echo "A/B/E. route × role and forged-token matrix (edge + direct-to-service)"
RAW=$(docker exec -i \
  -e TOK_PLAYER="$TOK_PA" -e TOK_INSTRUCTOR="$TOK_IN" -e TOK_ADMIN="$ADMIN_TOK" \
  -e JWT_PRIVATE_PEM="$(cat ../../infrastructure/secrets/jwt_private.pem 2>/dev/null)" \
  sor_portal python - < 13_authz_probe.py 2>/dev/null)
if [ -z "$RAW" ]; then
  bad "matrix probe did not run (docker exec sor_portal failed)"
else
  [ -n "${AUTHZ_JSON:-}" ] && echo "$RAW" > "$AUTHZ_JSON"
  echo "$RAW" | python3 13_authz_report.py
  VIOL=$(echo "$RAW" | pyjson 'len(d["violations"]) + int(d["compromised_service_forgery"]["accepted"]) + sum(1 for c in d["internal_paths"].values() if c != 404)')
  [ "$VIOL" = "0" ] && ok "role matrix and forged-token matrix: 0 violations" \
                    || bad "matrix: $VIOL violations (listed above)"
fi

# C. object-level and escalation checks
echo; echo "C. object-level / escalation"
LAB_ID=$($CURL "$SOR_BASE/scenario/challenges" -H "Authorization: Bearer $ADMIN_TOK" \
  | pyjson "next((c['id'] for c in d if c.get('challenge_type')=='lab'), '')")
SID=""
if [ -n "$LAB_ID" ]; then
  SID=$($CURL -X POST "$SOR_BASE/scenario/challenges/$LAB_ID/session" \
          -H "Authorization: Bearer $TOK_PA" | pyjson 'd.get("id","")')
fi
if [ -n "$SID" ]; then
  c=$(code "$SOR_BASE/scenario/sessions/$SID/wireguard.conf" -H "Authorization: Bearer $TOK_PB")
  [ "$c" = 403 ] || [ "$c" = 404 ] && ok "trainee B cannot fetch A's VPN profile (HTTP $c)" \
                                   || bad "trainee B fetched A's VPN profile (HTTP $c)"
  c=$(code -X DELETE "$SOR_BASE/scenario/sessions/$SID" -H "Authorization: Bearer $TOK_PB")
  [ "$c" = 403 ] && ok "trainee B cannot tear down A's lab instance (HTTP $c)" \
                 || bad "trainee B tear-down of A's instance → HTTP $c"
  n=$($CURL "$SOR_BASE/scenario/sessions" -H "Authorization: Bearer $TOK_PB" \
        | pyjson "sum(1 for s in (d if isinstance(d,list) else d.get('sessions',[])) if s.get('id')=='$SID')")
  [ "$n" = 0 ] && ok "A's instance is not listed in B's sessions" \
               || bad "A's instance visible in B's session list"
  c=$(code "$SOR_BASE/scenario/sessions/$SID/wireguard.conf" -H "Authorization: Bearer $TOK_PA")
  [ "$c" = 200 ] && ok "owner A can fetch own VPN profile (positive control, HTTP $c)" \
                 || bad "owner A cannot fetch own VPN profile (HTTP $c)"
  $CURL -o /dev/null -X DELETE "$SOR_BASE/scenario/sessions/$SID" -H "Authorization: Bearer $TOK_PA"
else
  note "no lab instance could be launched — object-level lab checks skipped"
fi
c=$(code "$SOR_BASE/iam/profiles/$ADMIN_ID" -H "Authorization: Bearer $TOK_PB")
[ "$c" = 403 ] && ok "trainee cannot read an administrator's profile (HTTP $c)" || bad "trainee read admin profile (HTTP $c)"
c=$(code -X PUT "$SOR_BASE/iam/users/$PA_ID/roles" -H "Authorization: Bearer $TOK_IN" \
      -H 'Content-Type: application/json' -d '{"roles":["admin"]}')
[ "$c" = 403 ] && ok "instructor cannot assign roles (HTTP $c)" || bad "instructor role change → HTTP $c"
c=$(code -X PUT "$SOR_BASE/iam/users/$PA_ID/roles" -H "Authorization: Bearer $TOK_PA" \
      -H 'Content-Type: application/json' -d '{"roles":["admin"]}')
[ "$c" = 403 ] && ok "trainee cannot promote itself (HTTP $c)" || bad "trainee self-promotion → HTTP $c"
SECRET=$($CURL "$SOR_BASE/iam/settings/registration-secret" -H "Authorization: Bearer $ADMIN_TOK" \
           | pyjson 'd.get("secret") or d.get("registration_secret") or d.get("value","")')
c=$(code -X POST "$SOR_BASE/iam/auth/register" -H 'Content-Type: application/json' \
      -d "{\"username\":\"evalazX$SUF\",\"password\":\"evaltmp123\",\"secret\":\"$SECRET\",\"roles\":[\"admin\"]}")
[ "$c" = 403 ] && ok "self-registration with the valid secret cannot request admin (HTTP $c)" \
               || { bad "self-registration as admin → HTTP $c"; delete_user_by_name "evalazX$SUF"; }
c=$(code -X POST "$SOR_BASE/iam/auth/register" -H 'Content-Type: application/json' \
      -d "{\"username\":\"evalazY$SUF\",\"password\":\"evaltmp123\",\"secret\":\"wrong-$SUF\"}")
[ "$c" = 403 ] && ok "self-registration with a wrong enrolment secret is refused (HTTP $c)" \
               || { bad "registration with wrong secret → HTTP $c"; delete_user_by_name "evalazY$SUF"; }
c=$(code -X POST "$SOR_BASE/iam/auth/login" -H 'Content-Type: application/json' \
      -d "{\"username\":\"$PA\",\"password\":\"wrong\"}")
[ "$c" = 401 ] && ok "login with a wrong password is refused (HTTP $c)" || bad "wrong-password login → HTTP $c"

# D. sessions: revocation and idle timeout at the edge (SEC-022)
echo; echo "D. sessions (SEC-022)"
edge() { code "$SOR_BASE/$1" -H "Authorization: Bearer $2"; }
sleep 1   # tokens issued in the same second as a revocation stay valid
$CURL -o /dev/null -X PUT "$SOR_BASE/iam/users/$IN_ID/roles" -H "Authorization: Bearer $ADMIN_TOK" \
  -H 'Content-Type: application/json' -d '{"roles":["player"]}'
sleep 1; c=$(edge learning/participants "$TOK_IN")
[ "$c" = 401 ] && ok "demoted instructor: old token refused at the edge (HTTP $c)" \
               || bad "demoted instructor's old token still accepted (HTTP $c)"
delete_user_by_name "$IN"; sleep 1; c=$(edge scenario/challenges "$TOK_IN")
[ "$c" = 401 ] && ok "deleted account: old token refused (HTTP $c)" || bad "deleted account's token accepted (HTTP $c)"
T2=$(sor_token "$PA" evaltmp123)
$CURL -o /dev/null -X POST "$SOR_BASE/iam/auth/logout" -H "Authorization: Bearer $T2"; sleep 1
c=$(edge learning/me "$T2")
[ "$c" = 401 ] && ok "logout: the token is refused afterwards (HTTP $c)" || bad "token still valid after logout (HTTP $c)"
c=$(edge learning/me "$TOK_PA")
[ "$c" = 200 ] && ok "logout revoked only that token; the user's other session works (HTTP $c)" \
               || bad "other session of the same user broken by logout (HTTP $c)"
c=$(code -X POST "$SOR_BASE/iam/users/$PA_ID/sessions/revoke" -H "Authorization: Bearer $ADMIN_TOK"); sleep 1
c2=$(edge learning/me "$TOK_PA")
[ "$c" = 200 ] && [ "$c2" = 401 ] && ok "administrator revoked all sessions of a user: token refused (HTTP $c2)" \
                                 || bad "admin revoke → HTTP $c, token afterwards HTTP $c2"
# idle timeout: shrink it to 5 s, idle 7 s, expect refusal; then restore.
OLD_IDLE=$($CURL "$SOR_BASE/iam/settings/security" -H "Authorization: Bearer $ADMIN_TOK" | pyjson 'd["session_idle_timeout_seconds"]')
$CURL -o /dev/null -X PUT "$SOR_BASE/iam/settings/security" -H "Authorization: Bearer $ADMIN_TOK" \
  -H 'Content-Type: application/json' -d '{"session_idle_timeout_seconds":5}'
T3=$(sor_token "$PB" evaltmp123); sleep 1
c1=$(edge learning/me "$T3"); sleep 7; c2=$(edge learning/me "$T3")
ADMIN_TOK=""; admin_token        # the old admin token went idle too
$CURL -o /dev/null -X PUT "$SOR_BASE/iam/settings/security" -H "Authorization: Bearer $ADMIN_TOK" \
  -H 'Content-Type: application/json' -d "{\"session_idle_timeout_seconds\":${OLD_IDLE:-1800}}"
[ "$c1" = 200 ] && [ "$c2" = 401 ] && ok "idle timeout (set to 5 s): active token works (HTTP $c1), after 7 s idle refused (HTTP $c2)" \
                                   || bad "idle timeout: before HTTP $c1, after idle HTTP $c2"

# G. login throttling + security alert (QoS-39, SEC-014)
echo; echo "G. login throttling"
codes=""; for i in 1 2 3 4 5 6; do
  codes="$codes $(code -X POST "$SOR_BASE/iam/auth/login" -H 'Content-Type: application/json' \
           -d "{\"username\":\"$PB\",\"password\":\"wrong-$i\"}")"
done
c=$(code -X POST "$SOR_BASE/iam/auth/login" -H 'Content-Type: application/json' \
      -d "{\"username\":\"$PB\",\"password\":\"evaltmp123\"}")
echo "$codes" | grep -q 429 && [ "$c" = 429 ] \
  && ok "6 wrong passwords → lockout (codes:$codes); correct password during lockout → HTTP $c" \
  || bad "lockout not enforced (codes:$codes; correct password → $c)"
sleep 3
n=$($CURL "$SOR_BASE/monitoring/alerts" -H "Authorization: Bearer $ADMIN_TOK" \
     | pyjson "sum(1 for a in d['security_alerts'] if a['alertname']=='security.alert.login_lockout' and a.get('details',{}).get('username')=='$PB')")
[ "${n:-0}" -ge 1 ] && ok "security alert login_lockout surfaced by the monitoring service" \
                    || bad "no lockout security alert in /monitoring/alerts"

delete_user_by_name "$PA"; delete_user_by_name "$PB"
hr
echo "SUMMARY: $PASS passed, $FAIL failed, $INFO notes"
[ "$FAIL" -eq 0 ]
