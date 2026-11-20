# Evaluation Guidelines — how and when to run the suite

The evaluation scripts live in `scripts/eval/`. They are self-contained bash
(curl + python3 stdlib + docker CLI), run from inside WSL against the live
stack, create only throw-away accounts (`eval*`), and clean up after
themselves. The results reported in the paper are in [results/](../results/).

## Quick reference

```bash
cd scripts/eval

./00_run_all.sh                  # everything → reports/<timestamp>/
SKIP_LOAD=1 ./00_run_all.sh      # skip the load test (safe during an exercise)

./01_service_health.sh           # is everything up and ready?
ITER=50 ./02_api_latency.sh      # endpoint latency percentiles
CONCURRENCY=50 REQUESTS=500 ./03_concurrency_load.sh
RUNS=5 ./04_scoring_pipeline_lag.sh
./05_lab_provision_time.sh       # launch+teardown a real lab instance
./06_resource_usage.sh           # CPU/mem/images/volumes snapshot
./07_db_stats.sh                 # row counts + integrity checks
./08_event_bus_stats.sh          # RabbitMQ queue depths/consumers
./09_tls_check.sh                # cert, protocol, handshake, edge auth
./10_prometheus_snapshot.sh      # the platform's own in-cluster metrics
./11_purge_orphan_participants.sh  # maintenance (normally finds nothing)
./12_functional_smoke.sh         # PASS/FAIL regression, exit code CI-able
```

Configuration via environment: `SOR_BASE` (default `https://localhost:8000`),
`SOR_ADMIN_USER`/`SOR_ADMIN_PASS` (default `charon`/`charon`).

## When to run what

| Situation | Run | Why |
|-----------|-----|-----|
| After any code change / rebuild | `12`, `01`, `08` | catch functional regressions + confirm bus **consumers re-attached** (0 consumers after a restart = scoring is silently dead) |
| Before an exercise (day −1) | `00_run_all.sh` | full baseline; fix anything red |
| Morning of an exercise | `01`, `05`, `09` | services ready, labs actually provision, TLS valid |
| During an exercise | `01`, `08`, `06`, `10` (read-only) | health, queue backlog, resource headroom — avoid `03`/`05` |
| After an exercise | `07`, `04`, `11` | data integrity, pipeline behaviour under real load |
| Weekly / idle | `00_run_all.sh` | trend the archived reports over time |
| After changing auth/TLS/secret | `09`, `12` | posture + full lifecycle still works |

## Reading the results

- **Latency (02/03/10).** Compare p95 against your own previous reports, not
  absolute numbers — WSL2 timings vary with host load. Sudden 2–3× regressions
  on one endpoint usually mean an N+1 query or a new blocking call. Login is
  intentionally ~hundreds of ms (bcrypt); don't "fix" it.
- **Scoring lag (04).** Sub-second is healthy (baseline mean ~206 ms). "NOT
  SCORED within 10 s" on *every* run is the signature of the consumer-liveness
  fault fixed in 2026-07 — check `08` first: if `learning-service.scoring` shows
  **0 consumers**, the consumer never re-attached after a restart. Since the
  `EventConsumer` now self-heals with backoff, a simple restart cures a
  transient case; if it persists, read `docker logs sor_learning` for the
  reconnect loop. A high lag with 1 consumer instead means RabbitMQ or the
  handler is slow, not detached.
- **Provisioning (05).** First run after a rebuild includes image pulls and
  the WireGuard image build — expect tens of seconds; warm runs should be a
  few seconds. Leaked containers must be 0; if not, look for teardown errors
  in `docker logs sor_scenario`.
- **Queues (08).** `messages_ready` should hover near 0. A queue with 0
  consumers means a service is down or lost its bus connection.
- **Integrity (07).** Every integrity query must return zero rows. Orphaned
  participants → run `11`. Score drift → a revoke/award bug; investigate
  before trusting the scoreboard.
- **Smoke (12).** Any FAIL is a real defect; the script exits non-zero so you
  can wire it into CI or a cron job.

## Rules of thumb

1. **Archive everything.** `00_run_all.sh` already writes
   `reports/<timestamp>/`; keep them in git or a backup — trends matter more
   than single runs.
2. **Measure idle vs. loaded.** Run `06` once idle, once while `03` is
   running; the delta is your per-user cost estimate.
3. **Don't load-test a live exercise.** `03` (and to a lesser degree `05`)
   compete with trainees for CPU; use `SKIP_LOAD=1`.
4. **Change one thing at a time.** Re-run the suite after each infra change so
   a regression maps to a single cause.
5. **Human metrics need humans.** Platform numbers say the range *works*;
   whether it *teaches* needs trainee-level data (attempt logs are already in
   Postgres/OpenSearch) and Kirkpatrick-style surveys — see the ideas doc §1.3–1.4.
