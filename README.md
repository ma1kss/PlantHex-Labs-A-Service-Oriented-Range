# Service-Oriented Cyber Range (SOR) prototype

This repository contains a prototype of a Service-Oriented Cyber Range (SOR): a
cybersecurity training platform built as a set of independent services that run as
containers on a single host. It provides user and role management, CTF-style challenges,
isolated multi-container lab exercises reached over a per-trainee WireGuard VPN, scoring,
monitoring and an audit log.

**PlantHex-Labs** is only the name of this prototype. It is not a product; it is one
example of how a SOR can be built from its requirements. The repository shows that path
step by step, so that others can follow it to build their own SOR prototypes (see
[Building a SOR prototype](#building-a-sor-prototype)).

The repository also contains the evaluation scripts, the measurements and the
supplementary material that accompany the research paper describing the prototype.

## Quick start

Requirements: a Linux host (or WSL2) with Docker Engine and Docker Compose v2,
`openssl`, `curl` and `python3`. Lab exercises need the WireGuard kernel module
(built in to modern Linux kernels and WSL2).

```bash
./start.sh              # generate keys/certificate, build images, start everything
./stop.sh               # stop (data kept)
./stop.sh --wipe        # stop and delete all data volumes
./start.sh --rebuild    # rebuild all images from scratch
```

The first start builds the images and takes a few minutes. Then open
**https://localhost:8000/** (self-signed certificate — accept the browser warning).

Default administrator: `charon` / `charon`. Change it before exposing the platform
to anyone else (set `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` in
`infrastructure/docker-compose.yml` before the first start). Trainees register with
the enrolment secret, which the administrator can change in the web interface.

To add the example exercises (two labs and a single-container challenge):

```bash
./scripts/seed_examples.sh
```

Operator interfaces are bound to `127.0.0.1` only:

| Interface  | URL                         |
|------------|-----------------------------|
| Consul     | http://localhost:8500/ui    |
| RabbitMQ   | http://localhost:15672 (`sor_admin`, password in `infrastructure/secrets/rmq_admin.pw`) |
| Prometheus | http://localhost:9090       |
| Grafana    | http://localhost:3000       |
| Portainer  | http://localhost:9000       |

Keys, passwords and the TLS certificate are generated on the first start into
`infrastructure/secrets/` and `infrastructure/certs/` (both git-ignored). Rotate them
with `scripts/security/gen_secrets.sh --rotate`, then
`docker compose -f infrastructure/docker-compose.yml up -d --force-recreate`.

## Building a SOR prototype

The prototype was derived from requirements in the steps below. Each step leaves an
artefact in this repository or in the supplementary material, so the path from a
requirement to the code and the test that checks it can be followed.

1. **Requirements.** 49 functional and 27 security requirements of a SOR
   (supplementary Tables A.1–A.2).
2. **Functions.** The requirements are grouped into 19 abstract functions (F01–F19);
   `supplementary/requirements-traceability.csv` records the function(s) of every requirement.
3. **Logical views.** Functional, behavioural and temporal views of the functions decide
   which couplings are synchronous (REST) and which are asynchronous (event bus).
4. **Services.** The functions are grouped into eight services plus a service registry
   (Table A.4 gives the reasons for each boundary, Table A.5 the alternatives rejected).
5. **Zones and deployment.** The services and the infrastructure are placed in four zones
   (underlying infrastructure, virtualisation & execution, services, policy & governance)
   and declared in `infrastructure/docker-compose.yml` (Table A.3 maps every container).
6. **Verification.** Every requirement is checked by a probe in `scripts/eval/`
   (Table A.7) and receives a verdict: satisfied, partly or not satisfied.

The technologies used here (FastAPI, Consul, RabbitMQ, PostgreSQL, OpenSearch,
Prometheus, Docker) are one possible realisation; the method does not depend on them.
To build a different prototype, start again from step 1 with your own requirements.

To add a service to this prototype, use an existing service as a template (e.g.
`services/events_and_notification_service/`). A service built on
`cyber_range_core.create_app()` registers itself with the registry, exposes health,
readiness and metrics endpoints, verifies tokens and can publish and consume events.
Give it a Dockerfile and an entry in `infrastructure/docker-compose.yml`, and add its
credentials to `scripts/security/gen_secrets.sh` (and to `infrastructure/db_init/` or
`infrastructure/rabbit_init/` if it uses the database or the event bus). Prometheus
discovers it through the registry.

## Repository layout

```
start.sh, stop.sh          bring the platform up / down
infrastructure/            docker-compose.yml (deployment specification) and configuration
                           of Consul, RabbitMQ, PostgreSQL, Prometheus, Grafana, egress guard
cyber_range_core/          shared library: registry, auth, events, health, logging
services/                  the eight platform services
  iam_service                  identity, authentication, roles
  scenario_service             challenges, lab exercises, WireGuard instances
  orchestration_service        exercise workflow
  learning_service             scoring and progress
  monitoring_service           metrics and alerts
  data_logging_service         audit archive (OpenSearch)
  events_and_notification_service  event bus fan-out, announcements
  consumer_portal_service      web UI and API gateway (TLS edge)
examples/                  example lab definitions and a single-container challenge
scripts/eval/              evaluation probes (00_run_all.sh runs the suite)
scripts/pipeline.sh        build → scan → deploy → verify pipeline
scripts/security/          secret generation and image vulnerability scan
results/                   raw outputs of the evaluation run reported in the paper
supplementary/             supplementary material and requirement traceability matrix
docs/                      lab exercises, evaluation guidelines
```

## Reproducing the evaluation

With the platform running and the examples seeded:

```bash
cd scripts/eval
./00_run_all.sh                     # full suite → scripts/eval/reports/<timestamp>/
./16_redeploy_repeatability.sh      # restarts the platform; run separately
cd ../.. && ./scripts/pipeline.sh   # end-to-end deployment pipeline
```

Probes create only throw-away `eval*` accounts and remove them afterwards. Sample
sizes and options are described in `docs/evaluation-guidelines.md`. The outputs of the
run reported in the paper are in `results/evaluation/`, the pipeline run in
`results/pipeline/` and the image scan summary in `results/image-scan/`.

The software versions of the evaluated deployment are listed in supplementary
Tables A.8–A.9. Rebuilding the images installs the newest Python packages allowed by
`pyproject.toml`, so a later build may differ in patch versions.

## Supplementary material

[`supplementary/supplementary-material.md`](supplementary/supplementary-material.md)
contains the full requirement baseline (49 functional and 27 security requirements) with
the allocation, satisfaction and evidence of each requirement, the mapping of deployed
components to functions and requirements, the service-boundary decision table, the
alternatives considered, the requirement-to-test chains, the catalogue of evaluation
probes and the software versions. `supplementary/requirements-traceability.csv` is the
machine-readable traceability matrix (requirement → functions → verdict → evidence),
also used by `scripts/eval/00_allocation_check.py`.

## Note/Disclaimer

This is a research prototype designed for use with a single host. It runs untrusted exercise workloads in containers that share the host kernel, so a dedicated machine or VM should be used. It is provided as a proof of concept for a service-oriented cyber range and does not represent a final product. Further testing and adjustments are required. Claude (Anthropic) was mainly used for optimising the code of the services, evaluation scripts, and the frontend structure.
