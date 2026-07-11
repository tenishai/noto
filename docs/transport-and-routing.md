# ADR: Telemetry Transport, Filtering, Routing, and Cloud Seams

> **Status:** Accepted 2026-07-08
> **Amends:** [design.md](design.md) decisions "Backend hosting" (was: per-cluster backends) and the
> collector export path (was: gateway exports OTLP straight to backends).
> **Context:** the pipeline sketch (Prometheus/collectors -> queue -> ingestor -> multiple stores),
> the cost-consciousness notes (get telemetry off the workload node fast, mind ingress/egress), and
> the requirement that Argus work on any cloud, starting with GCP.

## Decision, in one picture

```
per cluster                                per region                          destinations
┌─────────────────────────────┐   ┌──────────────────────────────────┐
│ OTel Agent (DaemonSet)       │   │  Redpanda/Kafka (durable queue)   │      ┌ Mimir   (metrics)
│   filter: profile drop rules │   │    topics: metrics/logs/traces    │      ├ Loki    (logs)
│        │                     │──▶│        │                          │      ├ Tempo   (traces)
│ OTel Gateway (Deployment)    │   │  Ingest tier: OTel Collector      │─────▶├ warehouse sink
│   filter + enrich + batch    │   │  consumers - filter, route, fan   │      │  (ClickHouse default)
│   kafka exporter ────────────┘   │  out via routing connector        │      └ (optional streams
└─────────────────────────────┘   └──────────────────────────────────┘         for agents)
        kubectl logs -f keeps working: kubelet reads node files, none of this touches it
```

Collection stays **per cluster** (agents and gateway local to the workloads). Storage and the queue
centralize **per region** (one Redpanda/Kafka + Mimir/Loki/Tempo + Perses set per region). All
clusters in a region ship to that region's endpoint; nothing crosses a region boundary by default,
because cross-region/cross-cloud egress is the dominant telemetry cost on every provider.

## Why a queue at all

- **Backpressure isolation.** Without it, a slow or down backend fills the collector's in-memory
  `sending_queue` and then data drops. With it, agents and gateways hand off fast and locally; the
  workload node's IO is released immediately (the original "the quicker you get rid of it, the more
  room for real work" requirement).
- **Replay and fan-out.** Multiple consumer groups read the same stream independently: the storage
  ingest tier, the warehouse export, and (Phase 4) the real-time observer agent that learns "normal"
  and flags deviations. None of them can slow another down.
- **Not built from scratch.** The OTel Collector contrib has a Kafka exporter and Kafka receiver;
  the "ingestor" is just a collector Deployment consuming from the queue. No custom ingest service.

**Choice: Redpanda (Kafka-protocol) preferred, Kafka acceptable.** Redpanda is a single binary with
no ZooKeeper/KRaft controller fleet, and dev/qa can run one node. ZeroMQ was rejected (no durability,
no replay); RabbitMQ was rejected (wrong shape for high-throughput ordered streams). NATS JetStream
was considered (CNCF, even lighter than Redpanda) and set aside: the OTel contrib Kafka
exporter/receiver is far more battle-tested than any NATS path, and Kafka-protocol compatibility
keeps the consumer ecosystem open; revisit only if Redpanda's footprint becomes a problem. Pub/Sub
and kin are rejected in the default path (vendor backend, per-volume pricing; violates the OSS rule) -
at most an optional exporter, like any other vendor egress.

**When the queue enters (entry criteria).** The queue earns its keep at multi-cluster fan-in, replay,
and the Phase 4 agent tap - not at a single dev cluster. So: the **solo topology ships queueless**,
with the gateway exporting straight to in-cluster backends through a **file-backed persistent sending
queue** (`file_storage` extension) sized to ride out a backend restart. The regional queue lands when
the second cluster joins a region, or when the first queue consumer (warehouse export, observer
agent) is built - whichever comes first. Before standing it up, measure real gateway egress volume in
solo mode and size the topic retention from measurements, not guesses.

Exact versions (Redpanda chart, collector kafka components in the ocb manifest) are pinned in
[versions.md](versions.md) when the components are introduced, per the latest-stable convention.

## Ingestion protocols: OTLP first, scrape second, statsd legacy-only

The push path is **OTLP** (the OTel SDKs), full stop: typed metrics, traces, logs, exemplars, and
resource attributes in one protocol. The pull path is the Prometheus `/metrics` scrape every gojira
app already exposes on 8080. **StatsD is demoted to a legacy-ingest affordance**: the `statsd`
receiver stays in the distro manifest so un-instrumented third-party workloads can be caught, but it
is documented as "supported, never recommended" - it is a 2011-era untyped protocol that survives
mostly as a Datadog-migration path, and nothing Argus or gojira generates emits it. New
instrumentation questions have a one-word answer: OTLP.

## Filtering: drop the noise as early as possible

Filtering runs at three points, earliest first, because a dropped datapoint costs nothing downstream:

1. **Agent (DaemonSet)** - drop rules that need no cross-pod context: debug-level logs outside dev,
   health-check endpoint spans/logs (`/health`, `/healthz`, `/ready`), scrape metadata series.
2. **Gateway** - rules that need enrichment first: per-namespace exclusions, high-churn
   kube-state-metrics series, unused histogram buckets, unbounded labels (pod UID, request ID)
   stripped or aggregated away.
3. **Ingest tier (post-queue)** - destination-specific reduction: the warehouse sink gets a curated
   subset, not the firehose; retention-driven downsampling hooks live here later. This is also where
   the third cost lever beyond drop and compress lives: **aggregate-then-discard-raw** - for known
   high-cardinality metrics, emit the rollup (recording-rule or `metricstransform` aggregation
   rendered from the profile) and drop the raw series before storage. Telemetry shaping of this kind
   is the core of commercial cost-control platforms (Chronosphere's control plane, for one); ours is
   profiles-in-git. Phase 2/3 scope, but the ingest tier is its home.

The rules are **profile-owned, never hand-authored**: each observability profile carries a
`drop:`/`keep:` section that renders into OTel `filter` processor (OTTL) config, exactly like every
other pipeline opinion. The Phase 2/3 cardinality budget is the enforcement backstop behind them.
This also closes a known gap in the [enablement guide](otel-collector-enablement-guide.md), which
names `filter` and routing in prose but never demonstrates them; the reference assembly gains both.

## Tenancy: stamped at ingest

The regional backends are shared, so they run multi-tenant: **tenant per cluster**, carried as
`X-Scope-OrgID` on writes to Mimir/Loki/Tempo. The ingest tier stamps it from the telemetry's
`cluster` resource attribute (in the `solo` topology, the gateway stamps it directly). Cluster is
the finest useful isolation and cost-attribution unit; Perses datasources set the tenant header per
view, and per-team cost attribution falls out of tenant + namespace labels.

## Routing: send each signal to its logical place

The ingest tier uses the OTel `routing` connector plus per-destination exporters:

| Stream | Destination | Notes |
|---|---|---|
| Metrics | Mimir (regional) | `prometheusremotewrite`/OTLP; the central endpoint. Thanos rejected: Mimir already is the multi-tenant central write path, no sidecar fleet needed |
| Logs | Loki (regional) | GCP Cloud Logging bypassed for app logs (below) |
| Traces | Tempo (regional) | |
| Analytics subset | Warehouse sink - **default: self-hosted ClickHouse** (seam; managed cloud warehouses optional) | Filtered, curated stream for the Phase 4 forecasting agent's historical training data, cost attribution, long-range aggregates. See "The warehouse sink" below |
| Live streams | Queue consumer groups | The Phase 4 observer agent taps the queue directly; no extra export |

ClickHouse is **not** added as a fourth observability store, and it does not replace Loki/Tempo
(reaffirmed with revisit triggers in [design.md](design.md), rejected alternatives). It enters the
stack in exactly one role, below.

## The warehouse sink: ClickHouse by default (decided 2026-07-09)

The warehouse-sink seam was originally "BigQuery on GCP, Athena/Redshift on AWS, Synapse on Azure."
That had two problems: a vendor dependency in an otherwise 100%-OSS stack, and three per-cloud
implementations to build and maintain for the multi-cloud goal.

**Default implementation: self-hosted ClickHouse**, one per region alongside the other backends:

- **OSS and cloud-portable** - the same implementation on GCP/AWS/Azure, deleting two future
  per-provider builds. The seam remains: a team that wants BigQuery (or Athena, or Synapse) can
  select it; the managed warehouses become the optional per-cloud alternatives instead of the
  defaults.
- **Fed by the ingest tier** via the existing OTel contrib ClickHouse exporter on the routed,
  curated subset - never the firehose. Its size follows the subset's measured volume per deployment
  (a single shard where that is small, sharded where it is not); the operational weight is bounded
  by the routing rules, not by an assumed deployment size.
- **Queryable where it matters**: Perses ships a native ClickHouse datasource (LogQuery +
  TimeSeriesQuery, verified in the capability spike), and agents get arbitrary SQL - which is what
  the forecaster actually wants (LogQL/TraceQL are for humans debugging; analytics wants SQL).
- **A hedge that costs nothing extra**: operating a small ClickHouse builds the in-house competence
  that the Loki/Tempo revisit triggers (design.md) would require, so if that conversation ever
  reopens it happens from experience, not speculation.

**Rejected for this role** (2026-07-09): **Postgres / CloudSQL / AlloyDB / TimescaleDB.** The
workload is append-only wide events with analytical scans (group-by/aggregate over billions of
rows), which is columnar-OLAP shaped; Postgres is a row store (poor compression, index/vacuum
pressure under append load, full-row scans for column queries). CloudSQL is managed Postgres - same
engine plus a vendor-managed, GCP-only dependency. AlloyDB adds a columnar engine but is
GCP-proprietary and single-cloud - fails both the OSS rule and the portability seam. TimescaleDB's
compressed/advanced features are under the non-OSS Timescale License and still trail purpose-built
columnar stores at scale. The ecosystem tiebreaker: the OTel contrib exporter writes optimized
ClickHouse schemas out of the box; no maintained OTel-to-Postgres analytics path exists. Postgres
remains the family's right answer for transactional state (ladon's datastore, gojira's DB) - engine
per workload, not one engine everywhere.

Pin the ClickHouse image/chart and the collector's `clickhouse` exporter component in
[versions.md](versions.md) when introduced, per the latest-stable convention.

## The four cloud seams (multi-cloud goal, GCP first)

Everything above - OTel, the queue, Mimir/Loki/Tempo, Perses, Helm, Operator CRDs - runs identically
on GKE/EKS/AKS. Cloud-specificity is confined to four seams, each a provider interface in the step
engine from day one, with **only the GCP implementation built now**:

| Seam | GCP (now) | AWS (later) | Azure (later) |
|---|---|---|---|
| Object storage (backend blocks) | GCS | S3 | Azure Blob |
| Workload identity (pods -> buckets) | Workload Identity Federation | IRSA | Azure Workload Identity |
| Warehouse sink (analytics subset) | **ClickHouse (default, cloud-portable, self-hosted)**; BigQuery optional | ClickHouse (same); Athena/Redshift optional | ClickHouse (same); Synapse optional |
| Managed-logging bypass | Disable GKE Cloud Logging for app logs (keep system logs) | Disable CloudWatch agent | Disable AMA |

Terraform modules are provider-namespaced (`terraform/modules/<seam>/gcp/...`), the pattern ladon
already uses. The `argus adopt` path (Phase 3, pulled forward conceptually) is the multi-cloud entry
point: gojira is GKE-only, so on AWS/Azure a conformant cluster enters via adoption, not deployment.

**On the logging bypass:** `kubectl logs -f` is unaffected on every provider - it streams from the
kubelet reading container log files on the node and never touches the managed logging service. The
recorded caveat: keep *system* (control-plane/node) logging on, or accept losing those from the
cloud console.

## Consequences

- The queue is the largest new operational commitment in Argus. It is sized per environment (dev/qa
  run single-node Redpanda) and stood up by the step engine like everything else (new steps:
  `queue@<region>`, `ingest@<region>`; regional steps join the per-cluster steps 1-6).
- The enablement guide's reference assembly must be updated: filter examples, routing connector,
  kafka exporter/receiver, statsd receiver on the agent, and a decision on the unexplained
  logs-over-OTLPHTTP rule.
- Perses/alerting remain regional alongside the backends; per-cluster dashboards query the regional
  stores filtered by cluster label.
- Contract tests (testing.md layer 3) gain assertions: profile drop rules render into every agent
  and gateway pipeline; the routing table above matches rendered ingest-tier config.
