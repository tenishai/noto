# Argus Roadmap

Where Argus is going, at a glance. This is the short version; the
[design document](docs/design.md) is the source of truth for architecture, decisions, and the
detailed phase plan. Phases are ordered, and each builds on the last.

> **Status:** pre-Phase-1 (design complete, foundations in place). **Current focus: Phase 1 - Observe.**

## Phase 1 - Observe (current)

Take a gojira-managed cluster from zero to a full open-source observability platform, automatically.

- Pinned OpenTelemetry Collector distribution (`ocb`), the Agent + Gateway + TargetAllocator topology.
- Observability profiles mapped from gojira workload class (web -> HTTP RED, worker -> queue,
  cron -> job health, heavy -> USE, gpu -> accelerator).
- Idempotent step engine (`check -> apply -> record`) with a SQLite ledger; a gojira-reader; renderers.
- The `argus-platform`, `argus-observe-app`, and `argus-pipeline` skills.
- Default dashboards, recording rules, and SLOs; synthetic round-trip validation.

**Done when:** a gojira'd cluster reaches a full Mimir/Loki/Tempo + Perses + OTel platform, every app is
auto-observed by class, and a real metric and trace are queryable in Perses with a default dashboard and
golden-signal SLO - no hand-written YAML, applied directly via CLI/SDK/Helm.

## Phase 2 - Curate

Make new kinds of workloads light up automatically.

- The declarative `argus-integrations` catalog plus autodiscovery (`receiver_creator`, `k8s_observer`).
- The `argus-slo`, `argus-dashboard`, and `argus-cardinality` skills.

**Done when:** a new gojira workload type auto-wires its integration, and cardinality budgets are
enforced (soft).

## Phase 3 - Guard

Keep the observability layer honest over time.

- Policy enforcement (cardinality, retention, PII).
- Drift detection on the observability layer.
- `argus adopt` for clusters not deployed by gojira.

**Done when:** a rogue high-cardinality metric is rejected, and a non-gojira cluster can be adopted.

## Phase 4 - Reason

Make telemetry useful to agents, not just humans.

- `argus-triage` over live telemetry; trace <-> log <-> metric correlation wired by default.
- The runtime-context "flight recorder" for local dev (ring-buffer OTel exporter + a local MCP server
  feeding runtime snapshots into an agent's context).

**Done when:** the agent answers "why did this break" from runtime data, not guesses.

## Later

Multi-cloud; Pyroscope profiling as a fifth pillar (Perses-native); predictive cardinality; eBPF
auto-instrumentation.

---

Argus applies directly throughout - no third-party GitOps controller is ever required. Interested in a
phase or want to shape one? Open an issue or a discussion.
