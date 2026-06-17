# Argus - Design Document

> **Status:** Draft / design plan (pre-Phase-1)
> **Date:** 2026-06-16
> **Authors:** Kyle Greiner (kyle@example.com)
> **License:** Apache-2.0 (intended)
> **Relationship:** the **observability arm of [gojira](https://github.com/shaz/gojira)**.
> **Home (for now):** personal GitHub repo `argus`; migrates into the shared org alongside
> gojira / techne / limenal / cabal once the org exists.

---

## What is Argus

Argus is the piece that takes a **gojira deployment from zero to a full, end-to-end open-source
observability platform** - automatically, as a continuation of the same deployment. gojira gets your
app *running* on GKE (identity, CI/CD, manifests, sizing). Argus makes that running app, and the
cluster it lives on, *observable*: it stands up the open-source metrics/logs/traces backends, deploys
the OTel collection tier, and wires every gojira-managed app into it with default dashboards, SLOs,
and alerts. No hand-rolled OTel YAML, no separate observability project to babysit, no vendor backend.

This is **not** "the gojira of observability" - a parallel platform you operate on its own. Argus is a
**branch off of gojira**: a gojira deployment is the *input*, and a fully-observed workload on a
100%-open-source stack is the *output*. If you can `gojira` an app, you get Argus observability for
free.

The opinion: a workload's **gojira workload class already tells us how it should be observed**. A
`web` service wants HTTP RED metrics, latency SLOs, and request dashboards; a `worker` wants
throughput and queue depth; a `cron` wants run-success and duration. Argus reads that signal and
provisions the right observability without anyone authoring a dashboard or a scrape config.

Argus is driven by a **self-contained agent + Agent Skills**, not a web UI.

### Relationship to gojira

gojira already leans toward this: its generated apps expose `/metrics` and `/health` on port 8080,
and it emits PrometheusRules and scrape annotations. gojira stops at *"telemetry is exposed."* Argus
starts there and completes the loop: *"telemetry is collected, stored, visualized, alerted, and
attributed back to the workload class gojira deployed."*

```
gojira:  repo ──▶ identity ──▶ CI/CD ──▶ manifests ──▶ app running, /metrics exposed
                                                              │
                                                              ▼  (Argus continues)
argus:   cluster platform (collectors + backends + Perses) ──▶ auto-wire every gojira app ──▶ dashboards/SLOs/alerts
```

Argus runs as a **continuation of the gojira lifecycle** (a hand-off / extension phase), reading
gojira's contract and state rather than inventing its own parallel source of truth.

### Lineage

- **gojira** - the parent. Argus extends its lifecycle and consumes its contract, workload classes,
  and per-app metadata.
- **The OTel Collector Enablement Guide** (`otel-collector-enablement-guide.md`) - the collection
  patterns, processor opinions, and pipeline assembly Argus encodes as defaults.
- **The platform-evolution deck** (`observability-platform-design.md`) - the Node-Agent/Cluster-Agent
  topology and the "integrations that just work" north star (the de-branded `argus-integrations`).
- **OpenTelemetry + Mimir/Loki/Tempo (storage) + Perses (visualization)** - the open-source substrate
  (details below).
- **cabal / limenal / techne** - the agent runtime, tools, and memory Argus can later plug into.

---

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Positioning | **Observability extension of gojira**, not a standalone platform | Input is a gojira deployment; output is that deployment, observed |
| Telemetry substrate | **OpenTelemetry Collector** (custom distro via `ocb`) | Vendor-neutral, CNCF-graduated, the convergence point both enablement docs land on |
| Metrics backend | **Mimir** (Prometheus-compatible, object-storage) | Horizontally scalable, OSS, PromQL; outgrows single Prometheus cleanly |
| Logs backend | **Loki** | Label-based, object-storage, queried via Perses |
| Traces backend | **Tempo** | Object-storage, cheap, OTLP-native, exemplar links to metrics |
| Visualization | **Perses** | CNCF, Apache-2.0, dashboards-as-code (native CRD/GitOps); one pane over Mimir/Loki/Tempo (and Pyroscope later). Fits Argus's agent-first, declarative, "no hand-authored dashboards" model far better than Grafana's JSON blobs |
| Alerting | **Alertmanager** (+ `PrometheusRule` CRDs) | Standard Prometheus alerting path; rules are declarative CRDs, not UI-authored |
| Cluster scrape sources | **kube-state-metrics + node-exporter + cAdvisor/kubelet** | The "big singletons" + per-node host/pod telemetry from the deck |
| Collection topology | **Agent (DaemonSet) + Gateway (Deployment) + TargetAllocator (StatefulSet)** | The recommended architecture from the enablement guide |
| **Apply model** | **Direct apply via cloud CLI / SDK / Helm** through an idempotent step engine | **No third-party GitOps (no ArgoCD/Flux).** Argus provisions directly, like gojira shells to gcloud/gh/kubectl |
| Cloud-side infra | **Terraform or cloud SDK** | Object-storage buckets, IAM/WIF - stateful, declarative |
| In-cluster components | **Helm** (charts + pinned values) | The OSS standard; versioned, idempotent |
| Collector/scrape config | **Operator CRDs** (`OpenTelemetryCollector`, `ServiceMonitor`, `PodMonitor`) | Declarative, hot-reloaded, no manual config files |
| The contract | **Derived from `gojira.yaml` + workload class**, with an optional `observability:` override block | Zero new config for the common case; class drives the defaults |
| Backend hosting | **Per-cluster** self-hosted backends (Mimir/Loki/Tempo + Perses) | Strong isolation, contained blast radius; no shared failure domain |
| Ledger / state | **SQLite now**, techne-backed later | Proven in gojira; migrate the agent's deploy history into techne when ready |
| Agent home | **Self-contained in the `argus` repo** | Ships its own minimal runtime; portable, can register into cabal later |
| First cloud target | **GKE / GCP** | Matches gojira and existing infra; cloud-agnostic is a later phase |
| Object storage | **GCS** (S3/Azure later) | Backs Mimir/Loki/Tempo; one bucket-set per cluster |
| Interface | **Agent + Skills**, no web UI | Agent-first; the repo is the substrate |
| Language | **Python** (CPython 3.14, `uv`, `ruff`) | All sibling repos (gojira/techne/limenal/cabal) are Python; first-class Anthropic SDK; Jinja2 rendering + stdlib SQLite + shell-out fit Argus's "render YAML, apply via CLI" shape |

Rejected / deferred: a third-party GitOps controller (ArgoCD/Flux) - Argus applies directly. Hosted
SaaS backends (Datadog, Grafana Cloud, and similar) - Argus is the open-source answer. A standalone
identity separate from gojira. Multi-cloud on day one. A web UI.

---

## How it plugs into gojira (the contract & trigger)

Argus does not introduce a competing source of truth. It reads what gojira already knows:

- **Per-app:** the app name, namespace, workload class, and cluster from `gojira.yaml` and gojira's
  provisioning state. Workload class is the key signal - it selects the observability profile.
- **Per-cluster:** the registered cluster, GCP project, and region from gojira's cluster registry.

The common case needs **no new config**. For the rare override, `gojira.yaml` gains an optional block:

```yaml
# gojira.yaml (existing gojira contract) - Argus reads this; the observability block is optional
app: clips-api
class: web                       # gojira workload class -> Argus observability profile
cluster: dev-us-east4
namespace: clips
environments: [dev, qa, prod]

observability:                   # OPTIONAL - omit entirely for class defaults
  profile: web                   # override the class->profile mapping if needed
  traces: true                   # opt a web service into tracing
  retention: { metrics: 30d }    # override per-signal retention
  slos:
    - { name: latency-p99, objective: "99% < 500ms" }
  overrides:
    cardinality_budget: 1500000
    justification: "growth RUM expansion, approved 2026-06-14"
```

**Trigger:** Argus runs as a continuation of the gojira lifecycle - either invoked at the end of a
gojira deployment (hand-off) or run against an already-gojira'd cluster to bring it up to full
observability. Either way the entry point is "here is a gojira-managed thing; make it observed."

---

## Phase 1 scope - "every gojira app, automatically observed"

**Definition of done:** take a gojira-managed cluster with at least one deployed app and, in one
guided agent run, (1) stand up the per-cluster platform (Mimir/Loki/Tempo + Perses + the OTel collector
tier), (2) auto-wire every gojira app into it based on its workload class, and (3) prove a real metric
and trace from a deployed app are queryable in Perses with a default dashboard and golden-signal SLO
present - no hand-written collector YAML, applied directly via CLI/SDK/Helm.

### The opinionated open-source stack (what we target)

```
                 ┌──────────────── Perses (single pane) ─────────────────┐
                 │   metrics → Mimir   logs → Loki   traces → Tempo       │
                 └───────────▲──────────────▲───────────────▲────────────┘
                             │              │               │ (remote_write / OTLP / push)
                   ┌─────────┴──────────────┴───────────────┴─────────┐
                   │        OTel Gateway Collector (Deployment)        │  enrich, batch, redact, route
                   └─────────▲───────────────────────────▲────────────┘
                             │ OTLP                       │ OTLP
        ┌────────────────────┴───────┐        ┌───────────┴───────────────────┐
        │ OTel Agent (DaemonSet)      │        │ OTel TargetAllocator + Scraper │
        │ host+pod telemetry, logs,   │        │ (StatefulSet) - sharded        │
        │ OTLP/StatsD ingest          │        │ Prometheus scraping            │
        └────────────────────▲────────┘        └───────────▲────────────────────┘
                             │ scrape/receive               │ ServiceMonitor/PodMonitor
        gojira apps ─────────┘            kube-state-metrics, node-exporter, app /metrics:8080
```

Default versions are pinned in the distro manifest and Helm chart pins.

### Observability profiles (mapped from gojira workload class)

The opinion is a **class to profile mapping** - gojira's workload class selects what "observed" means,
so a developer never authors a dashboard or scrape config:

| gojira class | Argus profile | Signals + defaults |
|---|---|---|
| `web` | HTTP RED | request rate/errors/latency, p99 latency SLO, HTTP dashboard; traces opt-in |
| `worker` | Queue/throughput | processing rate, queue depth/lag, failure rate, saturation dashboard |
| `cron` | Job health | run success/failure, duration, last-success-age alert |
| `heavy` | USE + resource | CPU/mem/IO saturation, throttling, capacity dashboard |
| `gpu` | USE + accelerator | GPU utilization/memory, plus `heavy` defaults |

Every profile inherits the cluster baseline (node/pod health, kube-state-metrics, logs). Retention and
cardinality budgets default per environment (dev/qa/prod) and are overridable in the `observability:`
block. Overrides need justification - free-text, in git, visible at review (social enforcement first,
policy enforcement later).

### Order of operations (direct apply)

The agent runs an **idempotent, resumable step engine** (`check() → apply() → record`). Tooling is
chosen per layer; everything is applied directly - no GitOps controller in the path:

| # | Step (scope) | Tooling | Ledger key |
|---|---|---|---|
| 1 | Object-storage buckets + lifecycle | Terraform / gcloud SDK | `storage@<cluster>` |
| 2 | IAM / Workload Identity Federation for backends + collectors | Terraform / gcloud SDK | `iam@<project>` |
| 3 | Namespaces, cert-manager, OTel Operator, Prometheus Operator CRDs | Helm + kubectl | `bootstrap@<cluster>` |
| 4 | Backends: Mimir, Loki, Tempo, Perses, Alertmanager | Helm (pinned values) | `backend_<name>@<cluster>` |
| 5 | Collection: Agent DaemonSet, Gateway, TargetAllocator StatefulSet | Operator CRDs | `collector_<role>@<cluster>` |
| 6 | Scrape sources: kube-state-metrics, node-exporter | Helm | `scrape_<name>@<cluster>` |
| 7 | Per-app wiring: read gojira apps → profile → ServiceMonitor/PodMonitor + pipeline | CRDs | `wire@<app>` |
| 8 | Opinionated content: per-profile dashboards, recording rules, SLOs, alerts | CRDs / ConfigMaps | `content@<app>` |
| 9 | Validation: health checks, self-observability scrape, synthetic round-trip | CLI probes | `validate@<cluster>` |

Steps 1-6 are per-cluster (run once); steps 7-8 run per gojira app and re-run as gojira deploys new
apps. **Division of responsibility:** Terraform/SDK owns cloud/stateful resources, Helm owns in-cluster
components, Operator CRDs own runtime config. The agent edits the profile/contract and regenerates
CRDs; it never hand-edits collector YAML.

---

## Architecture

### Components

```
┌──────────────────────────── argus (repo) ────────────────────────────┐
│  profiles/ (class→observability opinions)   distro/ (ocb manifest)     │
│  terraform/ (cloud)   helm/ (pinned values)  crds/ (templates)         │
│  content/ (dashboards, recording rules, SLO/alert packs per profile)   │
│                                                                         │
│  agent/                                                                 │
│    persona + skills/ (Anthropic-format SKILL.md set)                    │
│    step-engine (check→apply→record, SQLite ledger)                      │
│    gojira-reader (reads gojira.yaml + gojira state → app/cluster model) │
│    renderers (profile → CRDs; cluster → terraform/helm values)          │
└─────────────────────────────────────────────────────────────────────┘
        │ reads                         │ applies directly       │ records
        ▼                               ▼                        ▼
  gojira contract + state    terraform · helm · kubectl · gcloud   SQLite ledger
                                                                   (→ techne later)
```

### Data model (first cut)

```
clusters            gojira-managed clusters Argus has made observable
  id, name, gcp_project, location, env, platform_status, created_at

apps                gojira apps Argus is observing (mirrors gojira's app list)
  id, cluster_id (fk), name, namespace, workload_class, profile, status

backends            deployed backend instances per cluster
  id, cluster_id (fk), kind (mimir|loki|tempo|perses), version, bucket, status

provision_steps     audit + idempotency ledger, one row per step per run
  id, target_ref, step_name, tooling (tf|helm|crd|cli),
  status (pending|done|failed|skipped), resource_ref, error, started_at, finished_at

events              append-only log: agent runs, applies, validations, drift
  id, target_ref, type, payload_json, created_at
```

`provision_steps` is load-bearing: re-runs skip `done`, failures resume from the failed step, and a
future teardown knows exactly what to remove.

### The custom collector distribution (`distro/`)

Argus ships its own pinned OTel Collector via **`ocb` (OpenTelemetry Collector Builder)** - core plus
the specific contrib components the profiles need (`otlp`, `prometheus`, `kubeletstats`,
`hostmetrics`, `filelog`, `k8sattributes`, `resourcedetection`, `transform`, `groupbyattrs`, `batch`,
`memory_limiter`, `spanmetrics` connector, `loadbalancing` exporter, OTLP/`prometheusremotewrite`
exporters). Rebuilding to add a component is a CI release event. This is the seam where the
**`argus-integrations`** catalog (the de-branded "integrations that just work") eventually lives:
declarative `integration.yaml` + autodiscovery via `receiver_creator` + `k8s_observer` and annotation
hints - so that as gojira deploys new kinds of workloads, their integrations light up automatically.

---

## Collector configuration defaults (per the enablement guide)

Argus does not let the agent improvise collector config. It ships the **exact opinions from
`otel-collector-enablement-guide.md`** as the rendered defaults, so every cluster gets the same
battle-tested pipeline. These are non-negotiable defaults (overridable only with justification):

**Pipeline order (the critical rule):** `memory_limiter` is always **first**, `batch` is always
**last**. The standard gateway chain Argus renders:

```
memory_limiter → k8sattributes → resource/default → batch → otlp/argus
```

**Recommended pipeline assembly (gateway)** - exactly the guide's structure:

```yaml
service:
  pipelines:
    metrics:        # receivers: [otlp, forward/self-scrape]
                    # processors: [memory_limiter, k8sattributes, resource/default, batch]
                    # exporters:  [otlp/argus]
    traces:         # processors: [memory_limiter, k8sattributes, resource/default, batch]
                    # exporters:  [otlp/argus, spanmetrics]      # spanmetrics connector
    metrics/spanmetrics:   # receivers: [spanmetrics]
                           # processors: [memory_limiter, resource/spanmetrics,
                           #   groupbyattrs/spanmetrics, transform/spanmetrics,
                           #   metricstransform/spanmetrics, batch]
    metrics/self-scrape:   # receivers: [prometheus/self-scrape]
                           # processors: [memory_limiter, resource/self-scrape]
                           # exporters:  [forward/self-scrape]
```

**Per-processor opinions Argus encodes:**

- **`memory_limiter`** - `limit_percentage: 90`, `spike_limit_percentage: 10`, relative to the
  container memory limit (the limit must be set, or it silently falls back to Node total). Collector
  HPA `targetMemoryUtilization` defaults to **70%** so autoscaling kicks in *before* the limiter starts
  rejecting traffic.
- **`k8sattributes`** - on the Agent in an Agent+Gateway split, run in **passthrough mode** (preserve
  source Pod IP) and scope to the node via `filter.node_from_env_var: KUBE_NODE_NAME` to limit API
  load; the Gateway does the actual enrichment via the `connection` source.
- **`resourcedetection`** - Agent-only (it is scoped to the running host).
- **`resource`** (not `attributes`) for environment enrichment (`environment`, `cluster`, `region`) -
  resource attributes describe the entity emitting telemetry; data attributes describe the request.
- **`groupbyattrs`** - elevate `namespace`/`pod`/`container` from datapoint to resource scope for
  cAdvisor and kube-state-metrics, so `k8sattributes` can use them as association sources.
- **`transform`** (OTTL) - KSM special-casing (strip the `k8s.*` attrs that refer to the KSM pod
  itself, not the pods it reports on) and PII redaction (e.g. `replace_pattern` on sensitive span
  attributes). Used by the `compliance` profile.
- **`batch`** - `send_batch_size: 1000`, `send_batch_max_size: 2000`, `timeout: 1s`.

**Exporter to backends:** OTLP **gRPC** with `compression: zstd`, `sending_queue.num_consumers: 50`,
`timeout: 30s`, `retry_on_failure.enabled: true`. **Logs** use the **OTLPHTTP** exporter (the guide's
preferred split). Watch `otelcol_exporter_queue_size` to tune `num_consumers`.

**Collection of cluster sources** follows the StatefulSet + TargetAllocator model: ServiceMonitors /
PodMonitors define *what* to scrape, the TargetAllocator shards targets across scraper replicas, no
duplication.

## Collector self-monitoring (the guide's "Monitoring Collector Health")

Argus treats the collector as mission-critical and self-observes it by default:

- **`health_check` extension** on `:13133` wired to Kubernetes liveness/readiness probes.
- **Self-observability metrics** on `:8888` scraped into Mimir, with a default **Collector Health**
  dashboard and alerts on the guide's key metrics: `otelcol_process_memory_rss` (OOM risk),
  `otelcol_receiver_refused_*` (dropping at the door), `otelcol_exporter_send_failed_*` (backend
  issues), `otelcol_processor_batch_batch_send_size` (batch tuning).
- **`zpages`** and **`pprof`** extensions enabled for live debugging and CPU/memory profiling.

This is wired in the order of operations: backend bring-up (step 4) includes the collector-health
dashboard (step 8), and validation (step 9) asserts the collector is scraping itself and a synthetic
metric/trace completes a full round-trip.

---

## The Agent & Skills

Argus ships a **self-contained agent** in-repo (its own minimal runtime, step engine, SQLite ledger),
so it runs without a cabal dependency. It is built to register into cabal later (as a persona pulling
skills from this repo) and to migrate its ledger into techne when ready, but neither is required.

Competence is packaged as **Agent Skills** in the Anthropic format (lowercase-hyphenated dir = `name`;
`SKILL.md` < 500 lines with `name` + `description` frontmatter; progressive disclosure to
`references/`, `scripts/`, `assets/`; imperative voice, explain the *why*; `license: Apache-2.0`).

| Skill | Does | Trigger phrasing | Phase |
|---|---|---|---|
| `argus-platform` | Stand up the per-cluster platform (collectors + Mimir/Loki/Tempo + Perses) | "make this cluster observable", "stand up the observability stack" | 1 |
| `argus-observe-app` | Wire a gojira app into observability by its class | "observe the clips app", "add dashboards/SLOs for this service" | 1 |
| `argus-pipeline` | Author/modify OTel collector pipelines & processors | "add a redaction step", "tail-sample traces" | 1 |
| `argus-slo` | Define SLOs → recording rules + alerts | "add an SLO for checkout latency" | 2 |
| `argus-dashboard` | Generate Perses dashboards-as-code (RED/USE per profile) | "dashboard for the growth service" | 2 |
| `argus-cardinality` | Find/curb high-cardinality metrics against budget | "what's blowing up our series count" | 2 |
| `argus-triage` | Investigate an incident using live telemetry | "why is p99 spiking", "debug this" | 4 |

We mirror Anthropic's `skill-creator` workflow to author and eval skills (write SKILL.md → 2-3 eval
prompts → with-skill vs baseline runs → grade → optimize description for triggering → package). We may
vendor `skill-creator` itself.

---

## Phase plan

| Phase | Ships | Definition of done |
|---|---|---|
| **1 - Observe** | `ocb` distro, profiles, Terraform/Helm/CRD step engine, gojira-reader, `argus-platform`/`argus-observe-app`/`argus-pipeline` skills | A gojira'd cluster → full Mimir/Loki/Tempo + Perses + OTel platform + every gojira app auto-observed; a real metric & trace queryable in Perses, < 30 min, no hand-written YAML |
| **2 - Curate** | `argus-integrations` declarative catalog + autodiscovery; `argus-slo`/`argus-dashboard`/`argus-cardinality` skills | A new gojira workload type auto-wires its integration; cardinality budget enforced (soft) |
| **3 - Guard** | Policy enforcement (cardinality/retention/PII), drift detection on the observability layer, `argus adopt` for clusters not deployed by gojira | Rogue high-cardinality metric rejected; a non-gojira cluster adopted |
| **4 - Reason** | `argus-triage` over live telemetry; **runtime-context flight recorder** for dev (below); trace↔log↔metric correlation wired by default | Agent answers "why did this break" from runtime data, not guesses |
| **Later** | Multi-cloud, Pyroscope profiling as a fifth pillar (Perses-native), predictive cardinality, eBPF auto-instrumentation | - |

Phases are ordered; Phase 1 is the MVP. Argus deploys directly throughout - no GitOps controller is
ever required.

---

## North star: runtime context for the agent (the "flight recorder")

Coding/SRE agents fail on non-trivial debugging because they see **static code, not runtime reality** -
they guess which branch ran and what a variable held. Argus already produces the cure: telemetry. The
Phase-4 idea (inspired by tools like Syncause) closes the loop for **local dev**:

- A custom **OTel exporter writes trace/span data to an in-memory ring buffer** - always-on, tiny
  footprint, overwrites old data.
- On an error/trigger, it **freezes the buffer** and snapshots the last few seconds of execution
  (stack traces, variable values, which branch ran).
- A **local server + an `argus-triage` Agent Skill** injects that runtime snapshot into the agent's
  context, so instead of guessing the agent says *"per the runtime trace, `x` was null at line 42,
  which caused the crash."*

Doubly aligned: a differentiating open-source product feature, and dogfooding - our own agents debug
from runtime reality, and snapshots can persist into techne as durable knowledge. It extends Argus from
"observe what gojira deploys" to "make telemetry *useful to agents*."

---

## Open-source hygiene

- **Apache-2.0**, all components OSS, no phone-home, no vendor backend required to run.
- Pin everything: `ocb` manifest pins each component gomod + version; Helm charts pinned; Terraform
  providers pinned. No floating `latest`.
- Generated artifacts are human-readable and inspectable; the SQLite ledger is the audit trail.
- Secrets via the org's existing path (External Secrets / OpenBao), never plaintext in any contract.
- Sibling-checkout dev convention until the org + PyPI exist.

## Out of scope (explicitly)

- A third-party GitOps controller in the apply path (ArgoCD/Flux). Argus applies directly.
- A hosted SaaS / managed backend. Argus is the open-source alternative; vendor egress is at most an
  optional exporter.
- A web UI. The agent is the interface.
- A source of truth separate from gojira. Argus reads gojira's contract/state; it does not duplicate it.
- Re-implementing gojira's deploy concerns. Argus begins where gojira ends.
- Building our own TSDB/log store. We integrate Mimir/Loki/Tempo.
- Day-1 multi-cloud. GKE/GCP first; storage/IAM seams abstracted for later.

## Resolved questions (2026-06-16)

1. **Positioning** → Argus is the **observability extension of gojira**, not a standalone "gojira of
   observability." Input is a gojira deployment; output is that deployment, observed.
2. **Apply model** → **direct apply via cloud CLI / SDK / Helm**, idempotent step engine. **No
   third-party GitOps** (no ArgoCD/Flux) in the path.
3. **Contract** → derived from `gojira.yaml` + workload class, with an optional `observability:`
   override block. Zero new config for the common case.
4. **Backend hosting** → per-cluster self-hosted backends (Mimir/Loki/Tempo + Perses).
5. **Ledger** → SQLite now, migrate into techne later.
6. **Agent home** → self-contained in the `argus` repo; register into cabal later.
7. **Visualization** → **Perses, not Grafana.** Perses is CNCF, Apache-2.0, and dashboards-as-code
   (native CRDs), which fits Argus's agent-first / declarative / "no hand-authored dashboards" model
   far better than Grafana's JSON dashboards. It covers all four pillars over our backends today -
   Prometheus/Mimir metrics, Tempo traces (scatter/Gantt/table), Loki logs (search/filter), and
   Pyroscope profiling (flame graphs) - so it satisfies the Phase-1 trace DoD with no Grafana in the
   stack. Tradeoff accepted: Perses is a CNCF *sandbox* project (v0.53.x as of 2026-06), younger and
   less battle-tested than Grafana; we pin its chart/version like everything else and revisit if a
   capability gap blocks a profile. This retires the "LGTM" label (the G was Grafana); we name the
   substrate explicitly as Mimir/Loki/Tempo + Perses.
8. **Language** → **Python.** All four sibling repos (gojira, techne, limenal, cabal) are Python, and
   Argus is meant to merge into the same org and compose with them; a different language would put a
   seam through the ecosystem. On the merits, gojira is the same shape of program (Python + Jinja +
   shelling to gcloud/kubectl), the Anthropic SDK is first-class in Python, SQLite is stdlib, and
   FastMCP fits the Phase-4 flight recorder. Go was considered (static binary, typed CRDs) but loses:
   Argus renders YAML and shells out rather than embedding a controller, so typed client-go buys
   little, and the one Go artifact (the ocb-built distro) is generated from a manifest, not code we
   write. Toolchain: CPython 3.14, `uv`, `ruff`. Pinned versions live in [versions.md](versions.md).

## Still open (not blocking Phase 1)

- **Trigger mechanism:** does gojira *call* Argus at the end of its lifecycle (tight hand-off), or does
  Argus run as a separate pass over a gojira-managed cluster (looser coupling)? Leaning: support both,
  start with the standalone pass.
- **Repo name:** `argus` - confirm before scaffolding.
- **CI host + WIF/OIDC path** to GCP (reuse gojira's).
