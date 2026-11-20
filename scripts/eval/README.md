# Platform evaluation suite

Self-contained probes for the running SOR stack. Run on the Docker host:

```bash
./00_run_all.sh          # full suite → reports/<timestamp>/
```

Usage, thresholds and interpretation: [docs/evaluation-guidelines.md](../../docs/evaluation-guidelines.md)
Results of the run reported in the paper: [results/](../../results/)

All scripts only create throw-away `eval*` accounts and clean up after
themselves. Override defaults with `SOR_BASE`, `SOR_ADMIN_USER`, `SOR_ADMIN_PASS`.
