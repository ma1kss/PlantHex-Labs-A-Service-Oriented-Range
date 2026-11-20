#!/bin/sh
set -eu
export PGHOST="${PGHOST:-sor_postgres}" PGUSER="${PGUSER:-sor}" PGDATABASE="${PGDATABASE:-sor}"
export PGPASSWORD="${PGPASSWORD:-sor}"

until pg_isready -q; do sleep 1; done

own() {  # own <service> <table...>
  svc="$1"; shift
  role="svc_$svc"
  pw="$(cat "/run/secrets/db_$svc")"
  psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$role') THEN
    CREATE ROLE $role LOGIN;
  END IF;
END \$\$;
ALTER ROLE $role WITH LOGIN PASSWORD '$pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE $PGDATABASE TO $role;
GRANT USAGE, CREATE ON SCHEMA public TO $role;
SQL
  for t in "$@"; do
    psql -q -c "DO \$\$ BEGIN IF to_regclass('public.$t') IS NOT NULL THEN EXECUTE 'ALTER TABLE public.$t OWNER TO $role'; END IF; END \$\$;"
  done
  echo "role $role owns: $*"
}

psql -q -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC;"
own iam           users platform_settings access_grants session_revocations revoked_tokens
own scenario      challenges submissions ctf_events ctf_event_challenges docker_specs lab_specs docker_sessions
own events        announcements
own learning      participants teams solves exercise_enrolments
own orchestration launches
echo "db-init: done"
