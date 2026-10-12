# Noto Roadmap

Where Noto is going, at a glance. This is the short version; the
[design document](docs/design.md) is the source of truth for architecture, decisions, and the
detailed phase plan. Phases are ordered, and each builds on the last.

> **Status:** Phase 1 is complete (v0.1.0, 2026-07-11). **Current focus: Phase 2 - Curate.**

## Phase 1 - Observe (complete, v0.1.0)

Take a gojira-managed cluster from zero to a full open-source observability platform, automatically.

- Pinned OpenTelemetry Collector distribution (`ocb`), the Agent + Gateway + TargetAllocator topology,
  plus the regional queue and ingest tier (see
  [docs/transport-and-routing.md](docs/transport-and-routing.md)).
- Observability profiles mapped from gojira workload class (web -> HTTP RED, worker -> queue,
  cron -> job health, heavy -> USE, gpu -> accelerator).
- Idempotent step engine (`check -> apply -> record`) with a SQLite ledger; a gojira-reader; renderers.
- The `noto-platform`, `noto-observe-app`, and `noto-pipeline` skills.
- Default dashboards, recording rules, and SLOs; synthetic round-trip validation.

**Done when:** a gojira'd cluster reaches a full Mimir/Loki/Tempo + Perses + OTel platform, every app is
auto-observed by class, and a real metric and trace are queryable in Perses with a default dashboard and
golden-signal SLO - no hand-written YAML, applied directly via CLI/SDK/Helm.

## Phase 2 - Curate (current)

Make new kinds of workloads light up automatically.

- The declarative `noto-integrations` catalog plus autodiscovery (`receiver_creator`, `k8s_observer`).
- The `noto-slo`, `noto-dashboard`, and `noto-cardinality` skills.
- The gojira rightsizing feedback loop: distill per-app p95/p99 usage from Mimir and POST it to
  gojira's `/api/v1/usage`, so gojira opens rightsizing PRs from Noto-produced evidence.
- **Per-app shaping in the shared gateway.** The collector tier is platform-owned (design.md,
  "Collector tier ownership"), so today only baseline drop rules reach it: a class-level or
  per-app `drop:` rule renders nowhere. Give per-app shaping a safe path into the shared tier
  (namespace-scoped rules composed from every observed app, or a per-app collector for apps that
  earn one) so one app's rule can never rewrite another app's pipelines.
**Done when:** a new gojira workload type auto-wires its integration, cardinality budgets are
enforced (soft), and a gojira app gets a rightsizing PR from Noto evidence.

## Phase 3 - Guard

Keep the observability layer honest over time.

- Policy enforcement (cardinality, retention, PII).
- Drift detection on the observability layer.
- **Label-selector pruning.** `observe` only ever applies, so a class change that drops the scrape
  family, or an app rename, leaves the previous ServiceMonitor/PodMonitor/PrometheusRule live until
  somebody deletes it by hand (README, "Running observe as a Job"). Pruning by
  `noto.dev/managed=true` needs the `delete` verb, which widens an identity whose whole argument
  is that it cannot delete anything anywhere - hence deferred to the phase that is about keeping
  the layer honest rather than standing it up.
- `noto adopt` for clusters not deployed by gojira.

**Done when:** a rogue high-cardinality metric is rejected, and a non-gojira cluster can be adopted.

## Phase 4 - Reason

Make telemetry useful to agents, not just humans.

- `noto-triage` over live telemetry; trace <-> log <-> metric correlation wired by default.
- The observer agent: one generic daedalus-bundled agent, made team-specific by an Noto-rendered
  config (streams, baselines, escalation) - a consumer group on the regional queue, zero code per
  team. The forecasting agent reads history instead (Mimir + warehouse) and feeds gojira rightsizing.
- The runtime-context "flight recorder" for local dev (ring-buffer OTel exporter + a local MCP server
  feeding runtime snapshots into an agent's context).

**Done when:** the agent answers "why did this break" from runtime data, not guesses.

## Later

Multi-cloud; Pyroscope profiling as a fifth pillar (Perses-native); predictive cardinality; eBPF
auto-instrumentation.

---

Noto applies directly throughout - no third-party GitOps controller is ever required. Interested in a
phase or want to shape one? Open an issue or a discussion.
