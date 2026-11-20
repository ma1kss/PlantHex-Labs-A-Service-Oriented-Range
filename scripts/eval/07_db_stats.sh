#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

PSQL() { docker exec sor_postgres psql -U sor -d sor -tA -F' | ' -c "$1"; }

echo "=== Database stats ($(date -Is)) ==="
echo "── row counts / table sizes ──"
# Use a real COUNT(*) per table (via a generated query) — pg_stat_user_tables
# .n_live_tup is only an estimate refreshed by ANALYZE and can read 0 right
# after bulk changes, which would make this report misleading.
COUNT_SQL=$(PSQL "
SELECT string_agg(
  format('SELECT %L AS tbl, count(*) AS rows, pg_size_pretty(pg_total_relation_size(%L)) AS size FROM %I',
         relname, relid::regclass, relname),
  ' UNION ALL ')
FROM pg_stat_user_tables;")
PSQL "SELECT tbl, rows, size FROM ($COUNT_SQL) q ORDER BY tbl;"

echo; echo "── integrity: participants without an IAM user (expect 0 rows) ──"
PSQL "
SELECT p.user_id, p.username FROM participants p
LEFT JOIN users u ON u.id = p.user_id WHERE u.id IS NULL;"

echo "── integrity: participant score vs sum of solves (expect 0 rows) ──"
PSQL "
SELECT p.user_id, p.username, p.total_score, COALESCE(s.pts,0) AS solve_pts
FROM participants p
LEFT JOIN (SELECT user_id, SUM(points) pts, COUNT(*) n FROM solves GROUP BY user_id) s
  ON s.user_id = p.user_id
WHERE p.total_score <> COALESCE(s.pts, 0);"

echo "── integrity: solves referencing missing challenges (expect 0 rows) ──"
PSQL "
SELECT DISTINCT s.challenge_id FROM solves s
LEFT JOIN challenges c ON c.id = s.challenge_id WHERE c.id IS NULL;"

echo "── activity: submissions in the last 24h ──"
PSQL "
SELECT COUNT(*) FILTER (WHERE correct)   AS correct,
       COUNT(*) FILTER (WHERE NOT correct) AS wrong
FROM submissions WHERE submitted_at > now() - interval '24 hours';"
