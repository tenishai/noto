# Argus - Design Document

> **Status:** Draft / design plan (pre-Phase-1)
> **Date:** 2026-06-16 (revised 2026-07-08: family alignment - license, real gojira contract,
> transport/routing, daedalus, rightsizing feedback)
> **Authors:** Kyle Greiner (kyle@example.com)
> **License:** AGPL-3.0-or-later (the tenishai family license)
> **Relationship:** the **observability arm of [gojira](https://github.com/tenishai/gojira)**.
> **Home:** [tenishai/argus](https://github.com/tenishai/argus) (transferred into the org
> 2026-07-11), alongside gojira / techne / limenal / cabal / daedalus / ladon.

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
- **The platform-evolution notes** (`observability-platform-design.md`) - the Node-Agent/Cluster-Agent
  topology and the "integrations that just work" north star (`argus-integrations`).
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
| SLO generation | **Sloth** (decided 2026-07-11 with the #17 design) - profiles carry a tool-neutral `slos:` block, the renderer emits Sloth specs, Sloth emits the multiwindow-multiburn recording rules and alerts | Pure generator fits the renderer pipeline (spec in, `PrometheusRule` out, nothing to operate); burn-rate math is not something we own. Pyrra (operator + UI) is the recorded alternative |
| Cluster scrape sources | **kube-state-metrics + node-exporter + cAdvisor/kubelet** | The "big singletons" + per-node host/pod telemetry |
| Span metrics ownership | **Tempo metrics-generator** (`service-graphs` + `span-metrics` processors, spike-verified) owns span-metrics and the service graph; the spanmetrics connector ships in the distro but is unwired by default | One owner avoids double-counted RED metrics; Tempo-side needs zero extra collector pipelines. The connector is the fallback for a deployment that disables the generator |
| Collection topology | **Agent (DaemonSet) + Gateway (Deployment) + TargetAllocator (StatefulSet)** | The recommended architecture from the enablement guide |
| **Apply model** | **Direct apply via cloud CLI / SDK / Helm** through an idempotent step engine | **No third-party GitOps (no ArgoCD/Flux).** Argus provisions directly, like gojira shells to gcloud/gh/kubectl |
| Cloud-side infra | **OpenTofu or cloud SDK** | Object-storage buckets, IAM/WIF - stateful, declarative. OpenTofu, not Terraform: Terraform is BUSL (not open source) since 1.6, which violates the 100% OSS rule |
| In-cluster components | **Helm** (charts + pinned values) | The OSS standard; versioned, idempotent |
| Collector/scrape config | **Operator CRDs** (`OpenTelemetryCollector`, `ServiceMonitor`, `PodMonitor`) | Declarative, hot-reloaded, no manual config files |
| The contract | **Derived from `gojira.yaml` + workload class**, with an optional `observability:` override block | Zero new config for the common case; class drives the defaults |
| Backend hosting | **Per-cluster collection, per-region storage** (Mimir/Loki/Tempo + Perses per region) | Revised 2026-07-08: collection stays local and cheap; storage centralizes per region so all clusters ship to one endpoint without cross-region egress. See [transport-and-routing.md](transport-and-routing.md) |
| Transport | **Durable queue (Redpanda/Kafka) between gateway and storage** | Decouples collection from backends, absorbs backend outages, gives agents a real-time tap. See [transport-and-routing.md](transport-and-routing.md) |
| Filtering / routing | **Profile-owned `filter` drop rules as early as possible; `routing` connector fan-out in the ingest tier** | Drop noise before it costs egress or storage; route each signal to its logical destination. See [transport-and-routing.md](transport-and-routing.md) |
| Cloud portability | **Multi-cloud is the goal, GCP first.** Four seams behind provider interfaces from day one: object storage, workload identity, warehouse sink, managed-logging bypass | Everything above the seams (OTel, backends, Helm, CRDs) is already cloud-neutral; only the seams get per-provider implementations |
| Topology sizes | **`solo` and `regional`** - same rendered components, different placement. `solo`: everything in one cluster, queueless (file-backed gateway sending queue), single-node backends; `regional`: shared queue + ingest tier + backends per region | Phase 1's "one cluster, one guided run, < 30 min" DoD and the kind smoke test are `solo`; production estates are `regional`. Mirrors gojira's laptop mode vs production sandbox |
| Log shipper | **A seam: OTel `filelog` default; Fluent Bit supported alternative** (FB tails -> OTLP -> same local gateway; downstream identical). Both rendered from the profile schema's log-collection section, both contract-tested. Chosen per deployment by benchmark on real node profiles, not by opinion. FB implementation is post-Phase-1 | Scale-flexible without giving up the one-config-guarantee: at very high per-node log rates FB's footprint/throughput wins; at typical rates one substrate wins. The bypassed GKE logging agent is itself Fluent Bit, so the pattern is native to the platform |
| Scale posture | **Design for arbitrary scale; size each deployment from its measurements.** Every component is horizontally scalable (Mimir/Loki/Tempo shard on object storage, the queue partitions, collectors HPA, the ingest tier is a stateless consumer group); scale numbers are *inputs* (profile budgets, Helm sizing tiers, topic retention), never constants baked into rendered config | Argus targets many teams and many companies, small and massive. One deployment's volume ([target-fleet.md](target-fleet.md)) is an instance parameter, not the design envelope. Decisions justified "because our volume is small" are reasoning bugs - flexibility across scales is the requirement |
| Tenancy | **Tenant per cluster**, stamped as `X-Scope-OrgID` by the ingest tier (`solo`: by the gateway) | Mimir/Loki/Tempo are natively multi-tenant; cluster is the finest useful isolation and cost-attribution unit and aggregates upward in Perses; per-team usage attribution falls out of tenant + namespace labels |
| License | **AGPL-3.0-or-later** | Moves with the family (gojira/techne/limenal/cabal/daedalus relicensed 2026-07-08); permits library-level embedding of siblings |
| Agent shell | **daedalus bundle** (persona + agent card + wiring), with a thin Argus CLI fallback | daedalus owns "an agent is a directory" plus transports, cabal peering, and limenal wiring; Argus keeps only what it owns (step engine, renderers, profiles, skills) as the tools the bundle wires in |
| Ledger / state | **SQLite now**, techne-backed later | Proven in gojira; migrate the agent's deploy history into techne when ready |
| Agent home | **Self-contained in the `argus` repo** | Ships its own minimal runtime; portable, can register into cabal later |
| First cloud target | **GKE / GCP** | Matches gojira and existing infra; cloud-agnostic is a later phase |
| Object storage | **GCS** (S3/Azure later) | Backs Mimir/Loki/Tempo; one bucket-set per cluster |
| Interface | **Agent + Skills**, no web UI | Agent-first; the repo is the substrate |
| Language | **Python** (CPython 3.14, `uv`, `ruff`) | All sibling repos (gojira/techne/limenal/cabal) are Python; first-class Anthropic SDK; Jinja2 rendering + stdlib SQLite + shell-out fit Argus's "render YAML, apply via CLI" shape |

Rejected / deferred: a third-party GitOps controller (ArgoCD/Flux) - Argus applies directly. Hosted
SaaS backends (Datadog, Grafana Cloud, and similar) - Argus is the open-source answer. A standalone
identity separate from gojira. Multi-cloud on day one. A web UI. **Terraform** - BUSL since 1.6, not
open source; OpenTofu is the drop-in replacement. **VictoriaMetrics/VictoriaLogs** as the storage
tier - credible on ops simplicity and resource footprint (and Apache-2.0), but Mimir's native
multi-tenancy is load-bearing for the per-region storage design, MetricsQL deviates subtly from
PromQL, and there is no Victoria equivalent for traces, so we would still run Tempo and split the
operational model; revisit only if Mimir's operational weight proves unaffordable.
**ClickHouse replacing Loki/Tempo** (reaffirmed 2026-07-09) - for traces it is blocked outright:
Perses has no ClickHouse TraceQuery plugin (verified in the capability spike), so traces stored
there would be invisible in our pane, while Tempo passed every check and ships span-metrics/service
graph for free. For logs the question is workload *shape*, not volume - Loki shards horizontally on object storage
to very large scale, is OTLP-verified, and alerts through the ruler, while ClickHouse means schema
engineering, MergeTree/TTL operations, and disks. Revisit triggers, any of: (1) a deployment's
query workload outgrows Loki's label model (needle-in-haystack full-text or high-cardinality
group-bys become the norm, at any volume), (2) Perses ships a ClickHouse TraceQuery plugin, (3) the
observer/forecaster agents need arbitrary SQL over wide events beyond the warehouse subset. Note
ClickHouse does enter the stack earlier as the default warehouse-sink implementation - see
[transport-and-routing.md](transport-and-routing.md). **Hand-rolled SLO
rules** - see the SLO generation decision. **Fluent Bit as a hard-wired log shipper** - superseded by the log-shipper *seam* decision (see
the decisions table): filelog is the default, Fluent Bit is the supported per-deployment
alternative chosen by benchmark, both rendered from the same profile schema. What stays rejected is
FB as a second always-on substrate with hand-authored config outside the contract-test guarantee.
**Telegraf** - a metrics agent overlapping the Prometheus-scrape and OTLP paths we already have,
Influx-centric plugin ecosystem, nothing for logs/traces; no gap it fills here.

---

## How it plugs into gojira (the contract & trigger)

Argus does not introduce a competing source of truth. It reads what gojira already knows:

- **Per-app:** the app name, workload class, and per-environment cluster/namespace bindings from
  `gojira.yaml` and gojira's provisioning state. Workload class is the key signal - it selects the
  observability profile.
- **Per-cluster:** the registered cluster, GCP project, and location from gojira's cluster registry
  (a DB table: `name`, `gcp_project`, `location`, `gke_cluster`, `ar_repo`).

The real contract (verified against gojira's `gojira.yaml.j2` template, 2026-07-08): `app`, `class`,
an `environments` list (each entry carries its **own** `cluster` and `namespace`), an optional
`schedule` for cron, and an optional `overrides` block (`requests`, `limits`, `justification`).
There is no top-level cluster/namespace, and no observability block exists in gojira today.

The common case needs **no new config**. For the rare override, we will **propose to gojira** (as a
gojira PR, since gojira owns the contract) an optional block:

```yaml
# gojira.yaml (real gojira contract shape) - Argus reads this; the observability block is proposed
app: clips-api
class: web                       # gojira workload class -> Argus observability profile
environments:
  - name: dev
    cluster: dev-us-east4
    namespace: clips
  - name: prod
    cluster: prod-us-east4
    namespace: clips

observability:                   # PROPOSED, OPTIONAL - omit entirely for class defaults
  profile: web                   # override the class->profile mapping if needed
  traces: true                   # opt a web service into tracing
  retention: { metrics: 30d }    # override per-signal retention
  slos:
    - { name: latency-p99, objective: "99% < 500ms" }
  overrides:
    cardinality_budget: 1500000
    justification: "growth RUM expansion, approved 2026-06-14"
```

Until gojira accepts the block, Argus treats everything in it as Argus-side profile defaults keyed by
class; the block is purely additive when it lands.

**Trigger:** gojira has no outbound hook or event bus today (verified 2026-07-08), so Argus runs as a
**standalone pass over a gojira-managed cluster**: discover apps via the cluster registry and each
repo's `gojira.yaml`, then make them observed. A tight end-of-deploy hand-off is a future gojira PR.

**Superseding gojira's built-in wiring:** gojira already generates Google Managed Prometheus CRDs per
app (`monitoring.googleapis.com/v1` `PodMonitoring` + a scrape-down `Rules` alert). GMP is
GKE-proprietary and the least portable piece of the family. When Argus manages a cluster it must
**replace** that wiring with its own portable equivalents (ServiceMonitor / OTel scrape via the
TargetAllocator, plus the profile's alert pack), or every app is double-scraped and double-billed.

The mechanics matter: **deleting the CRD in-cluster is not enough**, because gojira's drift capture
diffs live state against the app repo's manifests and would PR the deletion right back (or flag
drift forever). The manifest source has to change. Interim mechanism (until the upstream skip flag
exists): the `wire@<app>` step opens a small PR against the app repo removing
`k8s/base/podmonitoring.yaml` and `k8s/base/rules.yaml` from the overlay and noting the Argus
equivalents in the PR body. This means **Phase 1 Argus requires `gh` write access to app repos** -
the same access gojira itself uses to open setup PRs, but stated here explicitly as a scope. The
in-cluster CRD is removed only after that PR merges (or in the same reconcile, since gojira treats
git as catching up to the cluster). Long-term we propose gojira gain an
`observability: argus`-style flag that skips emitting GMP CRDs at generation time, which retires
this whole dance.

**Feeding gojira back (the rightsizing loop):** gojira now accepts distilled usage evidence at
`POST /api/v1/usage` (cluster-scoped token) and turns it into reviewed rightsizing PRs. Argus sits on
Mimir with every app's real usage history, making it the natural evidence producer: query per-app
p95/p99 CPU and memory, distill, POST. Scheduled for Phase 2; this closes the loop between the two
platforms (gojira deploys, Argus observes, Argus feeds measurements back, gojira resizes).

---

## Phase 1 scope - "every gojira app, automatically observed"

**Definition of done:** take a gojira-managed cluster with at least one deployed app and, in one
guided agent run, (1) stand up the platform in the **`solo` topology** (Mimir/Loki/Tempo + Perses +
the OTel collector tier, all in-cluster, queueless), (2) auto-wire every gojira app into it based on
its workload class, and (3) prove a real metric and trace from a deployed app are queryable in
Perses with a default dashboard and golden-signal SLO present - no hand-written collector YAML,
applied directly via CLI/SDK/Helm. The `regional` topology (shared queue + ingest tier + backends)
is the production shape and lands per the entry criteria in
[transport-and-routing.md](transport-and-routing.md).

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
| 1 | Object-storage buckets + lifecycle | OpenTofu / gcloud SDK | `storage@<cluster>` |
| 2 | IAM / Workload Identity Federation for backends + collectors | OpenTofu / gcloud SDK | `iam@<project>` |
| 3 | Namespaces, cert-manager, OTel Operator, Prometheus Operator CRDs | Helm + kubectl | `bootstrap@<cluster>` |
| 4 | Backends: Mimir, Loki, Tempo, Perses, Alertmanager | Helm (pinned values) | `backend_<name>@<cluster>` |
| 5 | Collection: Agent DaemonSet, Gateway, TargetAllocator StatefulSet | Operator CRDs | `collector_<role>@<cluster>` |
| 6 | Scrape sources: kube-state-metrics, node-exporter | Helm | `scrape_<name>@<cluster>` |
| 7 | Per-app wiring: read gojira apps → profile → ServiceMonitor/PodMonitor + pipeline; **supersede gojira's GMP CRDs** (remove/neutralize the generated `PodMonitoring`/`Rules` so nothing is double-scraped) | CRDs | `wire@<app>` |
| 8 | Opinionated content: per-profile dashboards, recording rules, SLOs, alerts | CRDs / ConfigMaps | `content@<app>` |
| 9 | Validation: health checks, self-observability scrape, synthetic round-trip | CLI probes | `validate@<cluster>` |

Steps 1-6 are per-cluster (run once); steps 7-8 run per gojira app and re-run as gojira deploys new
apps. **Division of responsibility:** OpenTofu/SDK owns cloud/stateful resources, Helm owns in-cluster
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
**`argus-integrations`** catalog (the "integrations that just work") eventually lives:
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

The Argus agent's **shell is a daedalus bundle** (decision, 2026-07-08): a directory with
`agent-card.json`, `persona.md`, and `agent.yaml`, run by the daedalus runtime, which already provides
the transports (CLI, A2A, Slack), the cabal peer surface, and limenal tool wiring the original design
planned to hand-build. What Argus keeps in this repo is what it genuinely owns: the idempotent step
engine and SQLite ledger, the gojira-reader, the renderers, the profiles, and the Agent Skills - all
exposed as the tools the bundle wires in. Because daedalus is v0.1-in-progress, Argus also keeps a
thin CLI entry point of its own so Phase 1 never blocks on daedalus maturity; the bundle is the
target shape, the CLI is the escape hatch.

Sibling integration specifics (verified against the repos, 2026-07-08):

- **limenal** - register a native `Handler` via the `limenal.handlers` Python entry-point group
  (in-process), or run an Argus MCP server behind limenal's `mcp_proxy` / declarative targets.
- **techne** - embed the stable Python API; map step-engine run IDs onto techne sessions with
  `init_session_for_external_id`. Ledger migration is a one-time script through the public API
  (there is no generic foreign-SQLite importer).
- **cabal** - registering a persona currently requires a cabal PR (`PERSONA_REGISTRY` is a hardcoded
  dict, no plugin loading); the daedalus bundle's cabal peer surface is the nearer-term path.
- **ladon** - Argus onboards as a ladon "team"; the External Secrets Operator syncs OpenBao values
  into plain Kubernetes Secrets that collectors/backends mount. Argus never calls OpenBao at runtime.
- Now that Argus is AGPL like the family, library-level embedding (techne, limenal) carries no
  license boundary.

Competence is packaged as **Agent Skills** in the Anthropic format (lowercase-hyphenated dir = `name`;
`SKILL.md` < 500 lines with `name` + `description` frontmatter; progressive disclosure to
`references/`, `scripts/`, `assets/`; imperative voice, explain the *why*; `license: AGPL-3.0-or-later`).

| Skill | Does | Trigger phrasing | Phase |
|---|---|---|---|
| `argus-platform` | Stand up the per-cluster platform (collectors + Mimir/Loki/Tempo + Perses) | "make this cluster observable", "stand up the observability stack" | 1 |
| `argus-observe-app` | Wire a gojira app into observability by its class | "observe the clips app", "add dashboards/SLOs for this service" | 1 |
| `argus-pipeline` | Author/modify OTel collector pipelines & processors | "add a redaction step", "tail-sample traces" | 1 |
| `argus-slo` | Define SLOs → SLO spec → generated rules + alerts (via Sloth/Pyrra, never hand-rolled) | "add an SLO for checkout latency" | 2 |
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
| **1 - Observe** | `ocb` distro, profiles, OpenTofu/Helm/CRD step engine, gojira-reader, `argus-platform`/`argus-observe-app`/`argus-pipeline` skills | A gojira'd cluster → full Mimir/Loki/Tempo + Perses + OTel platform + every gojira app auto-observed; a real metric & trace queryable in Perses, < 30 min, no hand-written YAML |
| **2 - Curate** | `argus-integrations` declarative catalog + autodiscovery; `argus-slo`/`argus-dashboard`/`argus-cardinality` skills; **gojira rightsizing feedback** (distill per-app p95/p99 from Mimir → `POST /api/v1/usage`) | A new gojira workload type auto-wires its integration; cardinality budget enforced (soft); a gojira app gets a rightsizing PR from Argus-produced evidence |
| **3 - Guard** | Policy enforcement (cardinality/retention/PII), drift detection on the observability layer, `argus adopt` for clusters not deployed by gojira | Rogue high-cardinality metric rejected; a non-gojira cluster adopted |
| **4 - Reason** | `argus-triage` over live telemetry; **runtime-context flight recorder** for dev (below); trace↔log↔metric correlation wired by default | Agent answers "why did this break" from runtime data, not guesses |
| **Later** | Multi-cloud, Pyroscope profiling as a fifth pillar (Perses-native), predictive cardinality, eBPF auto-instrumentation | - |

Phases are ordered; Phase 1 is the MVP. Argus deploys directly throughout - no GitOps controller is
ever required.

---

## North star: the observer agent (generic agent, per-team config)

Phase 4's live-telemetry reasoning takes a specific shape: **one generic observer agent, made
team-specific by a config file** - no team ever forks or writes agent code. It lands only after the
OTel pipeline and infra work are complete; it is recorded now so nothing designs it out.

- **The generic agent is a daedalus bundle** (persona + agent card + wiring): how it reasons about
  anomalies is the persona; what it may touch is the wiring.
- **The team config is an Argus-rendered artifact**, the same family as profiles: which streams to
  consume (scoped by the team's tenant + namespace labels - this is why the tenancy decision
  matters), which SLOs and golden signals anchor "normal", sensitivity and quiet hours, and where to
  escalate (Slack channel, on-call). Custom per team, zero code per team.
- **Its feed is a consumer group on the regional queue** - the replayable-queue decision exists
  partly for this. The first observer is itself a valid trigger for standing up the regional queue
  (see the entry criteria in [transport-and-routing.md](transport-and-routing.md)).
- **Observer vs forecaster stays sharp:** the observer consumes the live stream and learns what
  normal looks like, flagging deviations (minutes of context). The forecaster reads history (Mimir
  long-range, the warehouse subset) and predicts capacity and cost - it feeds gojira's rightsizing
  loop. Two configs (or two bundles) of the same generic machinery, different inputs and cadences.

Market note: this problem space - getting off Datadog-scale bills via telemetry shaping,
cardinality control, and per-team cost attribution - is validated commercially (Chronosphere built
a company on it, closed-SaaS, M3 lineage). Argus is the open-source, agent-first answer; where they
shape telemetry with a proprietary control plane, we shape it with profiles, budgets, and rendered
filter/aggregation rules in git.

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

- **AGPL-3.0-or-later** (the family license), all components OSS, no phone-home, no vendor backend
  required to run.
- Pin everything: `ocb` manifest pins each component gomod + version; Helm charts pinned; OpenTofu
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

## Resolved questions (2026-07-08, family alignment)

9. **License** → **AGPL-3.0-or-later.** The whole family (gojira, techne, limenal, cabal, daedalus,
   agents) relicensed from Apache-2.0/MIT to AGPL on 2026-07-08; Argus moves with it. This keeps
   library-level embedding of the siblings clean; the alternative (stay Apache, integrate only over
   protocols) was rejected as a permanent seam through the ecosystem. Only ladon remains Apache-2.0.
10. **Trigger mechanism** → **standalone pass.** gojira has no outbound hook or event bus, so Argus
    discovers and observes gojira-managed clusters on its own schedule; a tight end-of-deploy
    hand-off is a future gojira PR.
11. **Agent shell** → **daedalus bundle** (with a thin Argus CLI fallback while daedalus is v0.1).
    See "The Agent & Skills."
12. **Transport, filtering, routing, and cloud seams** → see
    [transport-and-routing.md](transport-and-routing.md): durable queue between gateway and storage,
    profile-owned drop rules, routing-connector fan-out, per-cluster collection with per-region
    storage, and four provider-interface seams (object storage, workload identity, warehouse sink,
    managed-logging bypass) with GCP as the first implementation.

## Resolved questions (2026-07-09)

13. **Perses capability spike** → **PASS, Perses stands; fallback not invoked.** Verified hands-on
    against Mimir 3.1.1 / Loki 3.7.2 / Tempo 2.10.7 with synthetic OTLP load: all three signals
    queryable through the Perses proxy, dashboards-as-code via API with mixed-signal panels, tenant
    headers on datasources, span-metrics/service-graph via Tempo's metrics-generator, UI render
    confirmed by screenshot. One item carried forward (exemplar click-through at the UI level,
    verify during Phase 1 content work). Full results, reproduction manifests, and operational
    gotchas: [spikes/perses/RESULTS.md](spikes/perses/RESULTS.md).

## Still open (not blocking Phase 1)

- **Exemplar / cross-signal click-through in Perses UI** - carried from the spike; verify with a
  real SDK app emitting exemplars during Phase 1 content work. Mitigation already proven:
  co-located correlated panels per dashboard.
- **Repo name:** `argus` - confirm before scaffolding (note: name collision with the historical
  argus network-monitoring project; acceptable within the org namespace).
- **CI host + WIF/OIDC path** to GCP (reuse gojira's).
- **gojira contract PR timing:** when to propose the `observability:` block and the
  "skip GMP CRDs when Argus-managed" flag upstream.
