# Changelog

All notable changes to Argus are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/) (see [docs/versioning.md](docs/versioning.md)).

## [Unreleased]

### Changed

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
