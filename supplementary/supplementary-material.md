# Supplementary material

**Building a Service-Oriented Cyber Range: From Requirements to a Traceable Containerised Prototype**

This document accompanies the manuscript and the open-source release of PlantHexLab, the
prototype described in the paper. References to tables and sections "of the paper" point to
the manuscript. Probe numbers refer to the scripts in [`scripts/eval/`](../scripts/eval/), and
the raw outputs of the reported run are in [`results/`](../results/). The traceability matrix
of Tables A.1–A.2 is also available as [`requirements-traceability.csv`](requirements-traceability.csv).

**Contents:** A.1 Requirement baseline, allocation and satisfaction · A.2 Deployed components ·
A.3 Service-boundary decisions and alternatives · A.4 Requirement-to-test chains ·
A.5 Evaluation probes · A.6 Software versions · References

This appendix provides the inputs and outputs of the derivation and the evidence of the evaluation, so that each step can be inspected: the requirement baseline with its allocation and satisfaction (A.1), the deployed components (A.2), the service-boundary decisions and the alternatives considered (A.3), four requirement-to-test chains (A.4), the evaluation probes (A.5) and the software versions (A.6).

## A.1 Requirement baseline, allocation and satisfaction

The baseline consists of the 49 functional requirements of [1] and the 27 security requirements of [2], used unchanged; the 16 non-functional requirements of [1] are quality constraints and are not allocated to functions. Tables A.1 and A.2 give the text of every requirement, the functions it is allocated to (Table 1 of the paper), its satisfaction in the prototype and the probe or inspection that supports the verdict.

**Table A.1.** Functional requirements of the SOR [1], their allocation to functions, and their satisfaction in the prototype (S satisfied, P partly, N not satisfied). Evidence numbers refer to the probes of Table A.7.

| **ID** | **Requirement** | **Func.** | **Sat.** | **Evidence** |
|---|---|---|---|---|
| SR-FR-OSL-01 | Provide runtime infrastructure for hosting services | F18 | S | all 17 platform containers running and healthy (01, 16) |
| SR-FR-OSL-02 | Support virtualization platforms | F18 | P | container virtualisation (Docker) only; no hypervisor support (17) |
| SR-FR-OSL-03 | Manage physical and cloud-based infrastructure resources | F18 | P | single physical/WSL2 host; no cloud resource management (17) |
| SR-FR-OSL-04 | Provide network infrastructure (physical/virtual networks) | F18 | S | control-plane bridge + per-instance bridges created/removed (05; 14) |
| SR-FR-OSL-05 | Ensure resource isolation and sandboxing | F04 | S | no cross-instance, control-plane, host or internet reachability from laboratories, VPN or challenges (14); capabilities dropped, no privilege escalation, limits, minimal init (17) |
| SR-FR-OSL-06 | Support legacy system integration and databases | F18 | P | PostgreSQL + OpenSearch stores; no legacy-system integration |
| SR-FR-SCL-07 | Deploy and manage virtual machines and containers | F04 | P | containers only; no VM driver (17) |
| SR-FR-SCL-08 | Support configuration management tools | F18 | P | declarative Compose specification; no configuration-management tool (Ansible etc.) |
| SR-FR-SCL-09 | Provide middleware components for service execution | F18 | S | RabbitMQ + Consul + shared FastAPI library running (01; 08) |
| SR-FR-SCL-10 | Manage network components (firewalls, routers, switches) | F04 | P | per-instance bridge + NAT in WireGuard node; no firewall/router/switch appliances (17) |
| SR-FR-SCL-11 | Enable dynamic provisioning/de-provisioning of assets | F04 | S | 30/30 launch and teardown cycles; no leaked containers or networks (05) |
| SR-FR-SCL-12 | Enable automated deployment pipelines | F18 | S | deployment pipeline: static checks, secrets, build, vulnerability gate, deployment, readiness gate, functional and authorisation probes; passed end to end (P) |
| SR-FR-SL-13 | Provide resource provisioning service via standardized APIs | F04 | S | REST + OpenAPI 3 on 7/7 services; provisioning via POST /scenario/challenges/{id}/session (17) |
| SR-FR-SL-14 | Expose scenario deployment and management service | F03, F05 | S | Scenario Management CRUD + session lifecycle (12; 05) |
| SR-FR-SL-15 | Provide identity and access management service (IAM) | F01 | S | IAM service; functional suite and authorisation probe (12, 13) |
| SR-FR-SL-16 | Offer monitoring and analytics service | F08 | S | Monitoring service + Prometheus/Grafana (10; 17) |
| SR-FR-SL-17 | Provide data collection and logging service | F11 | S | Data & Logging consumer archives events (08; 17) |
| SR-FR-SL-18 | Implement service registry and discovery mechanism | F15 | S | Consul catalogue lists 8 services + registry; portal resolves via registry (01; 16) |
| SR-FR-SL-19 | Provide orchestration service for component coordination | F06 | S | Orchestration launches/teardown compose IAM/Scenario/Monitoring (12) |
| SR-FR-SL-20 | Support event handling and notification service | F13 | S | topic exchange + durable queues + announcements archived (08; 17) |
| SR-FR-BPL-21 | Orchestrate end-to-end cyber exercise workflows | F06 | P | per-instance launch/teardown workflow; no exercise-level workflow (cohort scheduling/enrolment/debrief) |
| SR-FR-BPL-22 | Support scenario lifecycle management (design, deploy, execute, evaluate) | F03, F05, F10 | S | define (upload) → deploy → execute → evaluate (flag scoring) (05; 12; 04) |
| SR-FR-BPL-23 | Manage participant registration and team assignment | F07, F10 | S | registration, team assignment and per-exercise enrolment with team and red/blue/white role (17; 18 R) |
| SR-FR-CL-24 | Provide unified web-based user interface for all actors | F16 | S | single SPA behind the TLS edge (02) |
| SR-FR-CL-25 | Support role-based dashboards (trainee, instructor, administrator) | F16 | S | role-dependent views for trainee/instructor/admin (17) |
| SR-FR-CL-26 | Support multi-device access (desktop, tablet, mobile) | F16 | P | viewport meta + media queries; not tested on devices (17) |
| SR-FR-CL-27 | Provision of API gateways to expose unified endpoints specifically for human interfaces and external user-operated tools. | F14 | S | Consumer & Portal edge proxies all 7 services with edge auth (02; 13) |
| SR-FR-CL-28 | Offer self-service scenario creation and management interface | F03 | S | instructors create/edit challenges and lab compose files in the UI (12; 13) |
| SR-FR-CL-29 | Enable collaboration tools | F16 | P | announcements + teams only; no chat/shared workspace |
| SR-FR-CL-30 | Provide real-time exercise monitoring interface | F16 | P | live WebSocket event feed + monitoring page; no in-instance telemetry (17) |
| SR-FR-IL-31 | Implementation of a middleware solution to facilitate communication between services, external systems and protocol mediation | F14 | P | gateway + broker between services; no external-system mediation |
| SR-FR-IL-32 | Support multiple communication patterns (pub-sub, request-response, message queue) | F13 | S | request-response via edge; topic exchange; durable queues (02; 08; 17) |
| SR-FR-IL-33 | Provide protocol conversion and mediation through ESB | F04 | N | no ESB / protocol conversion component (17) |
| SR-FR-IL-34 | Implement message-level encryption and digital signatures | F13 | S | every event AES-256-GCM encrypted and Ed25519-signed by its publisher; forged unsigned event injected into the broker dropped by all consumers (18 H) |
| SR-FR-IL-35 | Enable integration with external systems and federated ranges | F19 | N | F19 not implemented |
| SR-FR-QoS-36 | Monitor service performance and availability | F08 | S | Prometheus scrapes all services; monitoring summary (10; 17) |
| SR-FR-QoS-37 | Enforce security policies (authentication, authorization, encryption) | F02 | P | authentication and authorisation enforced on all 81 routes (13); TLS at the edge and encrypted events (09, 18 H); service-to-service HTTP without TLS |
| SR-FR-QoS-38 | Support multi-factor authentication (MFA) | F01 | N | multi-factor authentication not provided (17) |
| SR-FR-QoS-39 | Support intrusion detection and prevention | F08 | P | application-level detection/prevention: login lockout after 5 failures (13 G), alerts on 401/403 bursts and forged bus messages; no network IDS |
| SR-FR-QoS-40 | Provide service health dashboards and metrics | F09 | S | provisioned Grafana dashboard + /monitoring/summary (17) |
| SR-FR-QoS-41 | Provide real-time alerting for performance degradation | F09 | S | 6 Prometheus alert rules; outage alert firing within 60 s and resolved after restart, published to staff feed and archive (18 J) |
| SR-FR-IFL-42 | Provide centralized data warehouse for logs, metrics, and analytics | F11, F17 | S | logs, events, security alerts and per-service metric snapshots stored in one archive (18 K); Prometheus keeps raw series |
| SR-FR-IFL-43 | Ensure data quality and consistency | F11 | S | three integrity queries return 0 rows (07); deleting a challenge removes its solves and points (18 Q) |
| SR-FR-IFL-44 | Support data export and interoperability (CSV, JSON, XML) | F12 | S | JSON, NDJSON, CSV and well-formed XML export (17; 18 L) |
| SR-FR-IFL-45 | Provide unified data access layer | F12 | S | filtered/full-text/time-bounded query + stats API (17) |
| SR-FR-GL-46 | Define and enforce system-wide policies and standards | F17 | P | role checks in every service (13); no policy engine |
| SR-FR-GL-47 | Implement role-based access control (RBAC) policies | F02, F17 | S | 486/486 role checks conform, through the edge and directly at each service (13) |
| SR-FR-GL-48 | Support compliance monitoring (GDPR, NIS2, ISO 27001) | F17 | N | no compliance monitoring |
| SR-FR-GL-49 | Support risk assessment and management frameworks | F17 | N | no risk-assessment support |

**Table A.2.** Security requirements of the SOR [2], their allocation to functions, and their satisfaction in the prototype (S satisfied, P partly, N not satisfied, NV not verified). Evidence numbers refer to the probes of Table A.7.

| **ID** | **Requirement** | **Func.** | **Sat.** | **Evidence** |
|---|---|---|---|---|
| SEC-001 | *Containment.* The platform shall enforce network/system containment and egress controls to prevent exercise activity/traffic/tooling from impacting external environments and to support safe/legal operation (including provider safety assurances). | F04 | S | egress guard: lab and challenge networks reach only themselves — no internet, no host services, no other networks, from 5 vantage points (14); drop counters as assurance evidence |
| SEC-002 | *TenantIsolation.* The platform shall strictly isolate exercise execution contexts between exercises/teams to prevent cross-team interference, disclosure, or contamination; each exercise/team environment must be network-isolated from other exercises and from external systems. | F04 | S | 14: no instance reaches another instance, another trainee's challenge or external systems; per-trainee object checks (13 C) |
| SEC-003 | *Encryption.* The platform shall encrypt all service-to-service and user-to-service communications using mutually authenticated TLS with approved cipher suites to prevent interception and manipulation. | F13, F14 | P | TLS at the edge (09); events encrypted end to end (18 H); service-to-service HTTP without mutual TLS |
| SEC-004 | *Integrity.* The platform shall protect communications and platform-managed artifacts (configs, orchestration metadata, telemetry, logs, and policies) against unauthorized modification using cryptographic protections where appropriate. | F11 | P | edge TLS; events encrypted+signed (18 H); audit records hash-chained, edits detected (18 I); trainees cannot write the archive (18 L); internal HTTP and configuration files not cryptographically protected |
| SEC-005 | *Confidentiality.* The platform shall ensure confidentiality of platform-managed data (identities, configurations, policies, logs, learner performance, and telemetry) via encryption-at-rest and role-restricted access. | F11 | P | flags encrypted at rest (Fernet, 18 M); stores reachable only on the control-plane network/loopback via role-restricted APIs (13, 14); database and archive volumes not encrypted |
| SEC-006 | *Authentication.* The platform shall authenticate all users and services against a centrally managed identity provider before granting platform access; privileged roles should use multi-factor authentication. | F01 | P | every route rejects missing, forged and expired tokens (13); multi-factor authentication not provided |
| SEC-007 | *Authorization.* The platform shall enforce role-based access control aligned with stakeholder responsibilities and least-privilege across platform services and administrative functions (including simulated users and service identities). | F02 | S | 81 routes × 4 roles conform at the edge and at each service; object-level checks pass (13) |
| SEC-008 | *LeastPrivilege.* The platform shall ensure all user and service identities operate with least privilege permissions. | F02 | S | users: role checks on all 81 routes (13); services: RS256 private key only in IAM, own DB role (18 N) and broker account (18 S), Docker via filtering proxy (18 O) |
| SEC-009 | *IdentityFederation.* The platform shall support secure federation of identities with partner cyber ranges. | F19 | N | F19 not implemented |
| SEC-010 | *NetworkSegmentation.* The platform shall segment internal networks to separate infrastructure, management, and exercise traffic; the management plane must be segmented and isolated from exercise data planes. | F04 | S | management plane (service network; operator ports bound to loopback) unreachable from exercise networks and VPN (14) |
| SEC-011 | *Monitoring.* The platform shall continuously monitor security-relevant events and detect/surface anomalous or policy-violating behaviour affecting platform components and services, with traceability to support response (e.g., by green teams). | F08 | S | rule-based detection of auth-failure bursts, forged bus messages, lockouts and outages, surfaced to staff and archived with attribution (13 G; 18 H, J) |
| SEC-012 | *Logging.* The platform shall generate tamper-resistant (tamper-evident), timestamped, attributable logs of security-relevant events to support response, assurance, and auditing; logs should be immutable where feasible. | F11 | S | archive hash chain over all records; direct edit in the store detected, documented deletions verifiable (18 I); server timestamps and verified signer per record; not immutable |
| SEC-013 | *Traceability.* The platform shall maintain traceability between actions, identities, and platform events for auditing and accountability. | F17 | S | events carry verified signer, user id and correlation id; trainees cannot inject records; admin records attributed server-side (18 I, L) |
| SEC-014 | *Alerting.* The platform shall surface security alerts for suspicious or policy-violating behaviour. | F09 | S | security alerts for login lockouts (13 G) and forged bus messages (18 H) surfaced by the monitoring service and live feed |
| SEC-015 | *BoundaryProtection.* The platform shall enforce boundary controls on all federation and external connectivity interfaces; federation links must enforce mutual authentication and boundary policy inspection, and monitor/block suspicious or policy-violating behaviour from federation links. | F19 | N | F19 not implemented |
| SEC-016 | *PolicyEnforcement.* The platform shall enforce governance and security policies across all platform services and evaluate/enforce inter-range governance policies on federation partner interactions in real time. | F02, F17 | P | role policy enforced in every service (13); no policy engine; no inter-range policy |
| SEC-017 | *DataExportControl.* The platform shall restrict and audit export of platform-managed/exercise data to external destinations; exports require explicit role-authorized approval and produce immutable audit records. | F12 | P | export admin-only (13); every export recorded in the tamper-evident archive (18 L); no separate approval step |
| SEC-018 | *AdministrativeSecurity.* The platform shall secure privileged administrative interfaces against unauthorized access or misuse; privileged actions must be non-repudiably attributed to a verified identity and persisted in immutable audit records. | F01 | P | platform admin routes role-protected (13); operator UIs loopback-only (14); privileged actions attributed and recorded in the tamper-evident archive; no per-user signatures, not immutable |
| SEC-019 | *Compliance.* The platform shall enforce controls ensuring exercise operations comply with legal and provider requirements, and provide assurance evidence to providers that traffic/tooling use remains contained and compliant. | F17 | P | containment enforced and evidenced (egress-guard counters, isolation probe reports, 14); no legal/compliance controls |
| SEC-020 | *ServiceIdentity.* Each platform microservice must hold a cryptographically verified identity used for mutual authentication when invoking other platform services. | F01 | P | each service holds its own Ed25519 identity verified on every event it publishes (18 H); service-to-service HTTP calls not mutually authenticated (no mTLS) |
| SEC-021 | *SecretManagement.* All cryptographic keys and service credentials must be stored, distributed, and rotated using a dedicated secrets management service. | F01 | P | keys and passwords generated and rotated by gen_secrets.sh, distributed per service as file secrets (17); no dedicated secrets-management service |
| SEC-022 | *SessionManagement.* User sessions must enforce configurable idle timeouts and support immediate revocation by platform administrators. | F01 | S | 8 h tokens; configurable idle timeout and immediate revocation (demotion, deletion, logout, admin revoke) enforced at the edge (13 D) |
| SEC-023 | *Patching.* Platform software components must be continuously vulnerability-scanned and follow a defined, auditable patch management lifecycle. | F18 | P | all images digest-pinned; Trivy scans + pipeline gate; platform images free of fixable HIGH/CRITICAL, third-party images not yet patched |
| SEC-024 | *Fidelity.* The platform must support high-fidelity experimentation/training while maintaining strict containment and safety controls. | F05 | P | multi-container laboratory topologies over a per-instance VPN (05) with full containment (14); topologies limited to containers |
| SEC-025 | *Usability.* The platform must provide secure, role-appropriate usability for instructors/administrators without exposing sensitive learner data or platform internals. | F16 | NV | needs usability testing with instructors/administrators |
| SEC-026 | *RedTeamSafety.* The platform must ensure red-team and blue-team environments protect participants from collateral damage during exercises. | F05 | S | exercise containers hardened, isolated and without egress (14, 17); residual risk: shared host kernel |
| SEC-027 | *ExerciseControl.* The platform must allow the White Team to define/enforce least-privilege simulated user capabilities and prevent pivoting into range infrastructure. | F05, F06 | S | White Team sets user, read-only root and allow-listed capabilities; privileged, host mounts/namespaces and other capabilities refused (18 P); no pivoting into range infrastructure (14) |

## A.2 Deployed components

Table A.3 maps each deployed component to its zone, the functions it realises and, through Tables A.1 and A.2, to the requirements allocated to those functions. Every component traces to at least one requirement.

**Table A.3.** Deployed components of the prototype, their zone (1 Underlying Infrastructure, 2 Virtualisation & Execution, 3 Services, 4 Policy & Governance), the functions they realise and the requirements allocated to those functions.

| **Component** | **Zone** | **Role** | **Func.** | **Requirements** |
|---|---|---|---|---|
| sor_portal | 3 | Consumer & Portal: TLS edge, reverse proxy, SPA, session enforcement | F14, F16 | FR CL-24, CL-25, CL-26, CL-27, CL-29, CL-30, IL-31; SEC 003, 025 |
| sor_iam | 3 | Identity & Access Management (RS256 issuer, sessions, throttling) | F01, F02 | FR SL-15, QoS-37, QoS-38, GL-47; SEC 006, 007, 008, 016, 018, 020, 021, 022 |
| sor_scenario | 3 | Scenario Management (definitions, flags, instance lifecycle) | F03, F05 | FR SL-14, BPL-22, CL-28; SEC 024, 026, 027 |
| sor_orchestration | 3 | Orchestration (launch and teardown workflows) | F06 | FR SL-19, BPL-21; SEC 027 |
| sor_learning | 3 | Learning & Progress (enrolment, scoring, leaderboard) | F07, F10 | FR BPL-22, BPL-23 |
| sor_monitoring | 3 | Monitoring & Analytics (health summaries, alerts, snapshots) | F08, F09 | FR SL-16, QoS-36, QoS-39, QoS-40, QoS-41; SEC 011, 014 |
| sor_data | 3 | Data & Logging (capture, query, export, hash chain) | F11, F12 | FR SL-17, IFL-42, IFL-43, IFL-44, IFL-45; SEC 004, 005, 012, 017 |
| sor_events | 3 | Event & Notification (announcements, live feed) | F13 | FR SL-20, IL-32, IL-34; SEC 003 |
| sor_consul | 3 | Service registry (dual role) | F15 | FR SL-18 |
| sor_rabbitmq | 3 | Message broker (topic exchange, per-service accounts) | F13 | FR SL-20, IL-32, IL-34; SEC 003 |
| sor_prometheus | 3 | Metric collection and alert-rule evaluation | F08, F09 | FR SL-16, QoS-36, QoS-39, QoS-40, QoS-41; SEC 011, 014 |
| sor_grafana | 3 | Dashboards | F09, F16 | FR CL-24, CL-25, CL-26, CL-29, CL-30, QoS-40, QoS-41; SEC 014, 025 |
| sor_opensearch | 4 | Audit and event archive | F11, F17 | FR SL-17, IFL-42, IFL-43, GL-46, GL-47, GL-48, GL-49; SEC 004, 005, 012, 013, 016, 019 |
| sor_postgres | 1 | Relational store (per-service roles) | F18 | FR OSL-01, OSL-02, OSL-03, OSL-04, OSL-06, SCL-08, SCL-09, SCL-12; SEC 023 |
| sor_db_init, sor_rabbit_init | 1 | One-shot initialisers of per-service database and broker accounts | F18, F02 | FR OSL-01, OSL-02, OSL-03, OSL-04, OSL-06, SCL-08, SCL-09, SCL-12, QoS-37, GL-47; SEC 007, 008, 016, 023 |
| sor_egress_guard | 1/2 | Host-level confinement of exercise networks | F04, F18 | FR OSL-01, OSL-02, OSL-03, OSL-04, OSL-05, OSL-06, SCL-07, SCL-08, SCL-09, SCL-10, SCL-11, SCL-12, SL-13, IL-33; SEC 001, 002, 010, 023 |
| sor_docker_proxy | 2 | Filtering container-engine API proxy | F04 | FR OSL-05, SCL-07, SCL-10, SCL-11, SL-13, IL-33; SEC 001, 002, 010 |
| sor_portainer | 2 | Operator view of containers (loopback only) | F18 | FR OSL-01, OSL-02, OSL-03, OSL-04, OSL-06, SCL-08, SCL-09, SCL-12; SEC 023 |
| Docker engine, bridge networks | 1/2 | Host runtime, control-plane network, per-instance networks | F04, F18 | FR OSL-01, OSL-02, OSL-03, OSL-04, OSL-05, OSL-06, SCL-07, SCL-08, SCL-09, SCL-10, SCL-11, SCL-12, SL-13, IL-33; SEC 001, 002, 010, 023 |
| Laboratory containers (per instance) | 2 | Target infrastructure of a laboratory exercise | F04, F05 | FR OSL-05, SCL-07, SCL-10, SCL-11, SL-13, SL-14, BPL-22, IL-33; SEC 001, 002, 010, 024, 026, 027 |
| WireGuard server (per instance) | 2 | Per-instance VPN endpoint, key pair and UDP port | F04 | FR OSL-05, SCL-07, SCL-10, SCL-11, SL-13, IL-33; SEC 001, 002, 010 |
| Challenge containers (per trainee) | 2 | Single-container exercises | F04, F05 | FR OSL-05, SCL-07, SCL-10, SCL-11, SL-13, SL-14, BPL-22, IL-33; SEC 001, 002, 010, 024, 026, 027 |

## A.3 Service-boundary decisions and alternatives

Table A.4 shows how each service boundary meets the four service-identification criteria and which competing consideration was resolved there; Table A.5 lists the alternatives considered during the derivation and the reasons for the choice made.

**Table A.4.** Service-boundary decision table: how each service meets the four criteria of Section 4.3 of the paper and which competing consideration was resolved at its boundary.

| **Service** | **Concern (entity)** | **Cohesion** | **Shared mutable state** | **Single ownership** | **Independent deployability** | **Consideration resolved** |
|---|---|---|---|---|---|---|
| IAM (F01, F02) | Principal: identity, roles, sessions | Verification and authorisation act on the same principal and change together | Users, roles, revocations, access grants | Only IAM issues tokens and records revocations | Token contract (claims, RS256 key) stable for all services | F02 kept here, not at the edge: both read the role records; the edge only authenticates |
| Scenario Management (F03, F05) | Scenario, from definition to teardown | Definition feeds every lifecycle step | Definitions, flags, instance records, artefacts | Only service that drives the container engine | REST contract for challenges and sessions | Isolation over cohesion: engine access confined to this service and to a filtering proxy |
| Orchestration (F06) | Exercise workflow | Composition of steps of other services | Thin ledger of launches and grants | Owns no capability; peers authorise each step | Workflows change most often, so deployed separately | Kept separate from Scenario Management despite frequent calls |
| Learning & Progress (F07, F10) | Learner record across exercises | Enrolment and scoring update the same participant record | Participants, teams, enrolments, solves | Exercise roles here, platform roles in IAM | Consumes events; no synchronous dependants | Asynchronous scoring so submission never waits on scoring |
| Monitoring & Analytics (F08, F09) | Interpretation of platform state | Observation and its indicators change together | Observations, alert state | Reads metrics, owns no event data | Facade over Prometheus and Grafana | Separate from Data & Logging: sampled metrics vs pushed events (temporal view) |
| Data & Logging (F11, F12) | Unified record of activity | Write and read share one data model | Archive and its hash chain | Single writer of the archive | Query and export contract | Ingestion reserved to the consumer and administrators |
| Event & Notification (F13) | Asynchronous communication | Fan-out and announcements | Announcements, exchange configuration | Owner of notification surface | Topic and envelope contract (signed, encrypted) | A service, not only a broker: notifications need an owner |
| Consumer & Portal (F14, F16) | Platform interface | One ingress for UI and API | None (session cache only) | Only public entry point | SPA and gateway deployable alone | Gateway and portal merged to keep a single ingress |
| Service Registry (F15) | Service locations | Registration and resolution | Registrations, health | Single source of locations | Standard registry API | Registry instead of static addresses; static fallback kept for resilience |

**Table A.5.** Alternatives considered during the derivation and the reasons for the choice made.

| **Alternative** | **Reason not chosen** | **Choice made** |
|---|---|---|
| Provisioning (F04) as a service holding the container engine | Would proxy the engine's API without a platform-owned contract and add a hop to every launch | F04 realised by the container runtime (Zone 2); Scenario Management reaches it through a filtering proxy |
| Separate API gateway and web portal | Two ingress points with no state of their own | Merged into Consumer & Portal |
| Authorisation (F02) at the edge or as a policy-decision service | F01 and F02 read the same role records; separating them duplicates state | F02 in IAM; every service enforces the roles its routes need |
| Scenario definition (F03) and lifecycle (F05) as two services | Lifecycle needs the definition at every step; shared records | One Scenario Management service |
| Orchestration inside Scenario Management | Workflows change most often and span four services | Separate Orchestration service with a thin ledger |
| Monitoring merged with Data & Logging | Sampled metrics and pushed events differ in rate and store | Two services; metric snapshots archived by Data & Logging |
| One database per service | Footprint on a single host | One PostgreSQL instance; each service has its own role owning its tables |
| Event choreography for the launch workflow | Each launch step needs the result of the previous one; teardown must reverse the sequence | Synchronous orchestration; events for notification and scoring |
| Synchronous scoring (Scenario calls Learning) | Submission would block on scoring; more consumers need the same fact | Scoring consumes the signed submission event |
| Broker without an owning service | Announcements and the live feed need an owner | Event & Notification service |
| Static service addresses | Locations change at runtime; SR-FR-SL-18 | Consul registry with a static fallback |
| Governance (F17) as a service in the Services Zone | Oversight must not depend on the services it oversees | Policy & Governance Zone (archive outside the services) |
| Virtual machines for exercise instances | Provisioning time and footprint on a single host | Containers with confinement by the egress guard; VMs as future work |
| Shared symmetric token key | Any service holding it could issue tokens | RS256 with the private key held by IAM only |
| Confinement inside each laboratory network | Would not cover single-container challenges or tunnel traffic | Host-level egress guard for all exercise networks |

## A.4 Requirement-to-test chains

Table A.6 follows four groups of requirements through the complete chain, from requirement to function, service or non-service realisation, zone, deployed component, probe and verdict.

**Table A.6.** Four requirement-to-test chains. Chain A is the example of Section 4.3 of the paper; B ends outside the service layer, C crosses an asynchronous coupling and D is not implemented.

| **Step** | **A: identity** | **B: outside the service layer** | **C: asynchronous coupling** | **D: not implemented** |
|---|---|---|---|---|
| Requirements | SR-FR-SL-15, SEC-006 | SR-FR-OSL-05, SEC-002 | SR-FR-BPL-22, SR-FR-IL-34 | SR-FR-IL-35, SEC-009, SEC-015 |
| Transformation | Claimed identity → verified principal | Request → isolated runtime resources of an instance | Exercise outcome → score | Request → controlled connection to a partner range |
| Entity / output class | Principal / D | Runtime resources / S | Participant record / O | External connection / S |
| Function | F01 | F04 | F10 | F19 |
| Service-layer test | Passes: invoked as a step, platform-owned contract | Fails criterion 2: contract defined by the container runtime | Passes | Contract set by the federation |
| Grouping / realisation | Shares identity state with F02 → IAM | Container runtime (Zone 2), driven by Scenario Management via the proxy; egress guard | Shares the participant record with F07 → Learning & Progress; coupled to F05 via F13 | Composition of IAM, Event & Notification and Data & Logging |
| Zone | Services | Virtualisation & Execution (+ Underlying Infrastructure) | Services | Boundary / external |
| Deployed component | sor_iam; session checks in sor_portal | Docker engine, sor_docker_proxy, lab networks, sor_egress_guard | sor_learning consuming signed events of sor_scenario via sor_rabbitmq | – |
| Probes | 12, 13 | 05, 14, 17 | 04, 12, 18 | – |
| Satisfaction | SL-15: S; SEC-006: P | OSL-05: S; SEC-002: S | BPL-22: S; IL-34: S | N, N, N |

## A.5 Evaluation probes

Table A.7 lists the probes of the evaluation. Each probe is a script that runs against the deployed platform, creates only disposable accounts and objects, and removes them afterwards.

**Table A.7.** Evaluation probes (scripts in the repository, `scripts/eval`), their pass criteria and the requirements or research questions they evidence.

| **No.** | **Probe** | **What is checked / pass criterion** | **Evidence for** |
|---|---|---|---|
| 00 | Allocation check | All 76 requirements allocated; every function carries a requirement | Allocation invariants |
| 01 | Service health | Liveness and readiness of every service; registry catalogue | SL-18, QoS-36, OSL-01 |
| 02 | API latency | p50/p95 of 100 sequential requests per endpoint through the edge | RQ3, CL-27 |
| 03 | Concurrency load | 200 requests by 20 parallel clients; 20 parallel logins; zero errors | RQ3, SCL-11 |
| 04 | Scoring pipeline | Correct flag scored within 10 s for each of 100 runs | BPL-22, SL-20, IL-32 |
| 05 | Laboratory lifecycle | 30 launch/teardown cycles; first request through the VPN; no leaked containers or networks | SCL-11, SL-14, OSL-05 |
| 06 | Resources | Memory of all containers; image and volume sizes | RQ3 |
| 07 | Data integrity | Three referential-integrity queries return zero rows | IFL-43 |
| 08 | Event bus | Every queue has one consumer and no backlog | SL-20, IL-32 |
| 09 | TLS edge | TLS handshake; plaintext refused; 401 without token | SEC-003, QoS-37 |
| 10 | Monitoring | All scrape targets up; no server errors | QoS-36, QoS-40 |
| 12 | Functional suite | 12 end-to-end checks of accounts, roles, flags, scoring, revocation | SL-14, SL-15, BPL-22, BPL-23 |
| 13 | Authorisation | 81 routes × 4 roles, edge and direct; 9 forged-token kinds; object-level checks; session end; login throttling | SEC-006/007/008/022, GL-47, QoS-37/39 |
| 14 | Isolation | TCP reachability from 5 vantage points to other instances, control plane, host and internet; positive controls | SEC-001/002/010/024/026/027, OSL-05 |
| 15 | Teardown profile | Stop time per container kind | RQ3 |
| 16 | Redeployment | 3 down/up cycles: all healthy, catalogue complete, targets up, functional suite passes | SCL-12 |
| 17 | Requirement checks | Scripted inspection of remaining requirements (exports, alert rules, secrets, hardening) | various |
| 18 | Security controls | Bus-message injection, archive tampering, outage alerting, cross-service access, lab-definition limits, deletion consistency, enrolment | IL-34, SEC-004/005/008/011–014/017/027, QoS-41, IFL-42/43/44, BPL-23 |
| P | Deployment pipeline | Static checks, build, vulnerability gate, deployment, readiness, probes 12 and 13 | SCL-12, SEC-023 |

## A.6 Software versions

**Table A.8.** Software versions of the evaluated deployment.

| **Layer** | **Versions** |
|---|---|
| Host | Windows 11 Pro; WSL2 kernel 6.18.33.2; Ubuntu 24.04.4 LTS; Docker Engine 29.1.3; Docker Compose 5.1.2 |
| Service runtime | Python 3.11.15 on Debian 13; FastAPI 0.162.5; Pydantic 2.14.3; Uvicorn 0.54.0; SQLAlchemy 2.1.8; asyncpg 0.32.0; aio-pika 10.2.3; httpx 0.28.1; python-jose 3.5.0; cryptography 50.0.2; passlib 1.7.4 / bcrypt 3.2.2; structlog 26.1.0; prometheus-fastapi-instrumentator 8.1.0; opensearch-py 3.2.0; docker SDK 7.2.0; PyYAML 6.0.3 (complete list in Table A.9) |
| Infrastructure | Consul 1.19.2; RabbitMQ 3.13.7; PostgreSQL 16.13; OpenSearch 2.17.1; Prometheus 2.54.1; Grafana 11.2.0; Portainer CE 2.39.1; docker-socket-proxy 0.5.0 (HAProxy 3.4.2); iptables 1.8.10 (nf_tables) |
| Exercise access | WireGuard (kernel module) with wireguard-tools 1.0.20210914 on Alpine 3.19 |
| Tooling | Trivy 0.58.1 (vulnerability scanning); all images pinned by digest in the deployment specification |

**Table A.9.** Python packages installed in the service images of the evaluated deployment (`pip freeze` of the service image; all eight services share this set). Rebuilding the images installs the newest versions allowed by `pyproject.toml`, so a later build may differ in patch versions.

| Package | Version |
|---|---|
| aio-pika | 10.2.3 |
| aiofiles | 25.1.0 |
| aiohappyeyeballs | 2.7.1 |
| aiohttp | 3.14.5 |
| aiormq | 7.2.5 |
| aiosignal | 1.4.0 |
| annotated-doc | 0.0.6 |
| annotated-types | 0.8.0 |
| anyio | 4.16.0 |
| asyncpg | 0.32.0 |
| attrs | 26.1.0 |
| bcrypt | 3.2.2 |
| certifi | 2026.10.23 |
| cffi | 2.1.1 |
| charset-normalizer | 3.5.2 |
| click | 8.5.1 |
| cryptography | 50.0.2 |
| docker | 7.2.0 |
| ecdsa | 0.19.2 |
| Events | 0.5 |
| fastapi | 0.162.5 |
| frozenlist | 1.8.0 |
| greenlet | 3.5.6 |
| grpcio | 1.85.1 |
| h11 | 0.16.0 |
| httpcore | 1.0.9 |
| httptools | 0.9.0 |
| httpx | 0.28.1 |
| idna | 3.20 |
| multidict | 7.2.1 |
| opensearch-protobufs | 1.2.0 |
| opensearch-py | 3.2.0 |
| opentelemetry-api | 1.45.2 |
| packaging | 26.2 |
| pamqp | 5.0.0 |
| passlib | 1.7.4 |
| prometheus-fastapi-instrumentator | 8.1.0 |
| prometheus_client | 0.26.0 |
| propcache | 0.5.4 |
| protobuf | 7.36.2 |
| pyasn1 | 0.6.5 |
| pycparser | 3.12 |
| pydantic | 2.14.3 |
| pydantic-settings | 2.16.3 |
| pydantic_core | 2.50.4 |
| python-dateutil | 4.0.0 |
| python-dotenv | 1.2.4 |
| python-jose | 3.5.0 |
| python-multipart | 0.0.33 |
| PyYAML | 6.0.3 |
| requests | 2.34.2 |
| rsa | 4.9.1 |
| six | 1.17.0 |
| SQLAlchemy | 2.1.8 |
| starlette | 1.10.0 |
| structlog | 26.1.0 |
| typing-inspection | 0.4.5 |
| typing_extensions | 4.16.0 |
| tzdata | 2026.6 |
| urllib3 | 2.8.0 |
| uvicorn | 0.54.0 |
| uvloop | 0.23.0 |
| watchfiles | 1.3.0 |
| websockets | 17.4 |
| yarl | 1.25.1 |

## References

[1] M. Takaronis, G. Kavallieratos, V. Gkioulos, System Requirements for Service-Oriented Cyber Ranges in CPS/OT Environments, under review at the International Journal of Information Security. Preprint: https://doi.org/10.21203/rs.3.rs-8885747/v1

[2] M. Takaronis, A. Kollarou, G. Kavallieratos, V. Gkioulos, S. Katsikas, Using LLMs to Elicit Security Requirements for Service-Oriented Cyber Ranges, in: 2026 IEEE International Conference on Cyber Security and Resilience (CSR), 2026, pp. 589–595. https://doi.org/10.1109/CSR69374.2026.11760077
