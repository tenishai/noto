# Changelog

All notable changes to Argus are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/) (see [docs/versioning.md](docs/versioning.md)).

## [Unreleased]

### Added

- **Design spec for profiles + renderers + contract tests** (issue #17), approved and committed at
  [docs/superpowers/specs/2026-07-11-profiles-renderers-design.md](docs/superpowers/specs/2026-07-11-profiles-renderers-design.md):
  layered composition (baseline + class + override), structured filter rules with a justified
  `raw_ottl` escape hatch, ephemeral renders with golden files as the only persisted output, typed
  models -> `yaml.dump` renderers (no Jinja2 for these artifacts), and the invariant list the
  contract tests enforce.

### Changed

- **SLO generator decided: Sloth** (with the #17 design) - pure spec-in/rules-out generator fits the
  renderer pipeline; Pyrra (operator + UI) recorded as the alternative. Closes the open question.
- **Fluent Bit and Telegraf recorded as rejected alternatives**: one telemetry substrate per node
  stands (OTel filelog has ample headroom at fleet volume; the bypassed GKE logging agent is itself
  Fluent Bit). Revisit trigger recorded: measured filelog underperformance, with
  Fluent-Bit-to-OTLP-gateway as the clean fallback. Telegraf overlaps existing scrape/OTLP paths.

- **Python scaffold** (issue #16, the first code PR): `pyproject.toml` (package `argus` under
  `agent/`, hatchling, CPython 3.14.6 via `.python-version`), uv-managed env with committed
  lockfile, ruff lint+format, pytest with the unit and contract layers wired, `make test` running
  layers 1-3 per docs/testing.md, and CI's unit+contract job activated via `astral-sh/setup-uv`.
  Pins verified at introduction and recorded: uv 0.11.28 (bumped from 0.11.21), ruff 0.15.20,
  pytest 9.1.1, setup-uv v8.

### Changed

- **Warehouse sink default is now self-hosted ClickHouse** (per region, fed the curated subset via
  the OTel ClickHouse exporter), replacing "BigQuery with per-cloud equivalents later." One OSS,
  cloud-portable implementation instead of three managed-warehouse builds; BigQuery/Athena/Synapse
  remain optional per-cloud alternatives behind the same seam. Perses ClickHouse datasource
  coverage was verified in the capability spike; agents get SQL for analytics.
  Postgres/CloudSQL/AlloyDB/TimescaleDB recorded as rejected for this role (row-store vs
  columnar-OLAP workload shape; CloudSQL/AlloyDB additionally vendor/GCP-only; Timescale License
  not OSS) - Postgres stays the family's transactional-state engine.
- **ClickHouse replacing Loki/Tempo reaffirmed as rejected**, now with recorded revisit triggers:
  Loki label-model outgrown, a Perses ClickHouse TraceQuery plugin appearing (today traces in
  ClickHouse would be invisible in Perses), or agents needing arbitrary SQL beyond the warehouse
  subset.

### Added

- **Perses capability spike: PASS** ([docs/spikes/perses/RESULTS.md](docs/spikes/perses/RESULTS.md),
  closes issue #11). Perses 0.53.1 verified hands-on against pinned Mimir/Loki/Tempo on kind: full
  Loki/Tempo/Prometheus plugin coverage, dashboards-as-code via API, all three query languages
  through the Perses proxy, tenancy headers, Tempo metrics-generator span-metrics into Mimir, UI
  render screenshot. Grafana fallback not invoked; exemplar click-through carried as a Phase 1
  content-work check. Reproduction manifests committed; operational gotchas recorded (config mount
  must not mask /etc/perses plugins; Loki OTLP structured-metadata config; native OTLP ingest paths).

- [docs/target-fleet.md](docs/target-fleet.md): the first concrete deployment target - 11 production
  GKE clusters, ~2,200 logs/sec fleet-wide, 1 week raw retention then aggregation, Go/Python. Adapted
  from a teammate's earlier fleet roadmap; preserves its Datadog parallel-run migration strategy
  (instrument once with OTel, dual-ship during the window, deprecate per signal per service), its
  correlation acceptance checklist (trace_id in logs, exemplars, metric -> trace -> log
  click-through, folded into the Phase 1/2 DoD and the Perses spike), and its remediation guardrails
  (risk-tiered actions, blast-radius limits, cooldowns, pause switch) as recorded requirements for
  the future observer agent - remediation itself stays out of Argus scope.

### Changed

- **Topology sizes named as a first-class concept**: `solo` (one cluster, queueless, file-backed
  gateway sending queue, single-node backends - the Phase 1 DoD and kind smoke-test shape) and
  `regional` (shared queue + ingest tier + backends - the production shape). Same rendered
  components, different placement.
- **Tenancy decided**: tenant per cluster, stamped as `X-Scope-OrgID` by the ingest tier (gateway in
  `solo`), giving isolation and per-team cost attribution via tenant + namespace labels.
- **GMP supersession mechanics specified**: deleting gojira's generated CRDs in-cluster is not
  enough (drift capture would PR them back); the `wire@<app>` step opens a PR against the app repo
  removing the GMP manifests, which makes Argus's `gh` write access to app repos an explicit Phase 1
  scope. Long-term fix remains the upstream gojira skip flag.
- **Observer-agent north star recorded** in design.md and ROADMAP Phase 4: one generic
  daedalus-bundled agent made team-specific by an Argus-rendered config file, fed by a queue
  consumer group; kept sharp against the history-reading forecaster. Market context noted
  (Chronosphere validates the cost-control thesis commercially; Argus is the open-source,
  profiles-in-git answer), and aggregate-then-discard-raw added to the ADR as the third cost lever.

- **OpenTofu replaces Terraform** (CLI pin 1.12.3, verified 2026-07-08): Terraform has been BUSL
  (not open source) since 1.6, violating the 100% OSS hard convention. Providers are MPL-2.0 and
  work unchanged; sources re-pointed at the OpenTofu registry. The `terraform/` directory name stays
  (it is just HCL).
- **SLOs are generated, never hand-rolled**: profiles render an SLO spec consumed by Sloth or Pyrra
  (evaluation tracked as an open question, leaning Sloth), which emit the multiwindow-multiburn
  `PrometheusRule` output. Burn-rate math is not something we own.
- **StatsD demoted to legacy-only** in the transport ADR: OTLP is the push path, Prometheus scrape
  the pull path; the statsd receiver remains in the distro as a "supported, never recommended"
  affordance for un-instrumented third-party workloads.
- **Queue entry criteria added** to the transport ADR: the solo topology ships queueless with a
  file-backed persistent gateway sending queue; the regional Redpanda queue lands with the second
  cluster in a region or the first queue consumer. NATS JetStream recorded as considered-and-set-aside.
- **VictoriaMetrics/VictoriaLogs recorded as a rejected alternative** for the storage tier (Mimir
  multi-tenancy is load-bearing; MetricsQL dialect drift; no traces story).
- **Perses capability spike tracked as blocking the Phase 1 DoD** (metrics + logs + traces verified
  hands-on against Mimir/Loki/Tempo), with Grafana OSS via the Grafana Operator as the named
  fallback now that AGPL is license-clean for us.

- **Relicensed Apache-2.0 -> AGPL-3.0-or-later**, moving with the tenishai family (gojira, techne,
  limenal, cabal, daedalus all relicensed 2026-07-08). Permits library-level embedding of the
  siblings without a license boundary. LICENSE, README, CLAUDE.md, design.md, and the skills-format
  convention updated.
- **Backend hosting revised: per-cluster collection, per-region storage**, with a durable
  Redpanda/Kafka transport and an OTel-collector ingest tier doing profile-owned filtering and
  routing-connector fan-out. New ADR: [docs/transport-and-routing.md](docs/transport-and-routing.md).
  Multi-cloud is the stated goal (GCP first) via four provider-interface seams: object storage,
  workload identity, warehouse sink, managed-logging bypass.
- **design.md now documents the real gojira contract** (verified against gojira's template,
  2026-07-08): `app`, `class`, an `environments` list each carrying its own cluster/namespace,
  optional `schedule` and `overrides`. The `observability:` block is re-labeled as a proposal to
  gojira, not an existing field. The trigger question is resolved to the standalone pass (gojira has
  no outbound hook today).
- **The Argus agent's shell is a daedalus bundle** (agent card + persona + wiring), keeping the step
  engine, renderers, profiles, and skills as Argus-owned tools, with a thin CLI fallback while
  daedalus is v0.1.
- Sibling links updated from personal repos to the [tenishai](https://github.com/tenishai) org;
  ecosystem tables gained daedalus and ladon with concrete integration paths (limenal entry-point
  handler, techne `init_session_for_external_id`, cabal registry PR, ladon team onboarding via ESO).
- Step 7 of the order of operations now **supersedes gojira's generated Google Managed Prometheus
  CRDs** (`PodMonitoring`/`Rules`) so Argus-managed clusters are not double-scraped; proposing a
  skip flag upstream is tracked as an open question.
- Phase 2 gains the **gojira rightsizing feedback loop**: Argus distills per-app p95/p99 usage from
  Mimir and POSTs it to gojira's `/api/v1/usage`, closing the deploy-observe-resize loop.
- Visualization is now **Perses**, not Grafana. Perses is CNCF, Apache-2.0, and dashboards-as-code
  (native CRDs), and covers metrics (Mimir), traces (Tempo), and logs (Loki) - satisfying the Phase-1
  trace requirement with no Grafana in the stack. Retires the "LGTM" label in favor of naming the
  substrate explicitly (Mimir/Loki/Tempo + Perses). See decision 7 in
  [docs/design.md](docs/design.md).

### Added

- README "Part of a larger platform" section naming the sibling projects (gojira, techne, limenal,
  cabal) and Argus's relationship to each.
- Top-level [ROADMAP.md](ROADMAP.md): the phased plan (Observe -> Curate -> Guard -> Reason -> Later)
  at a glance, linked from the README, with design.md as the source of truth.
- Implementation language locked to **Python** (CPython 3.14, `uv`, `ruff`) for consistency with the
  sibling repos and first-class Anthropic SDK support. See decision 8 in [docs/design.md](docs/design.md).
- Canonical pinned-versions reference at [docs/versions.md](docs/versions.md) capturing the latest
  stable version of every tool we pin, with verify-at-pin-time caveats.
- CLAUDE.md convention: any newly introduced dependency must be verified to its current latest stable
  version and recorded in docs/versions.md before pinning.
- Phase 0 foundations: contributor guide with branching strategy and commit conventions
  ([CONTRIBUTING.md](CONTRIBUTING.md)), versioning and release policy
  ([docs/versioning.md](docs/versioning.md)), testing philosophy and layers
  ([docs/testing.md](docs/testing.md)), a conventions lint, and a CI skeleton.
- Baseline repo scaffold: OTel-style README, project conventions in CLAUDE.md, Apache-2.0 license,
  design and source-capture docs, and the project-local web-design-guidelines skill.
