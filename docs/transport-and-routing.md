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
│   filter + enrich + batch    │   │  consumers - filter, route, fan   │      │  (BigQuery on GCP)
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
no replay); RabbitMQ was rejected (wrong shape for high-throughput ordered streams). Pub/Sub and
kin are rejected in the default path (vendor backend, per-volume pricing; violates the OSS rule) -
at most an optional exporter, like any other vendor egress.

Exact versions (Redpanda chart, collector kafka components in the ocb manifest) are pinned in
[versions.md](versions.md) when the components are introduced, per the latest-stable convention.

## Filtering: drop the noise as early as possible

Filtering runs at three points, earliest first, because a dropped datapoint costs nothing downstream:

1. **Agent (DaemonSet)** - drop rules that need no cross-pod context: debug-level logs outside dev,
   health-check endpoint spans/logs (`/health`, `/healthz`, `/ready`), scrape metadata series.
2. **Gateway** - rules that need enrichment first: per-namespace exclusions, high-churn
   kube-state-metrics series, unused histogram buckets, unbounded labels (pod UID, request ID)
   stripped or aggregated away.
3. **Ingest tier (post-queue)** - destination-specific reduction: the warehouse sink gets a curated
   subset, not the firehose; retention-driven downsampling hooks live here later.

The rules are **profile-owned, never hand-authored**: each observability profile carries a
`drop:`/`keep:` section that renders into OTel `filter` processor (OTTL) config, exactly like every
other pipeline opinion. The Phase 2/3 cardinality budget is the enforcement backstop behind them.
This also closes a known gap in the [enablement guide](otel-collector-enablement-guide.md), which
names `filter` and routing in prose but never demonstrates them; the reference assembly gains both.

## Routing: send each signal to its logical place

The ingest tier uses the OTel `routing` connector plus per-destination exporters:

| Stream | Destination | Notes |
|---|---|---|
| Metrics | Mimir (regional) | `prometheusremotewrite`/OTLP; the central endpoint. Thanos rejected: Mimir already is the multi-tenant central write path, no sidecar fleet needed |
| Logs | Loki (regional) | GCP Cloud Logging bypassed for app logs (below) |
| Traces | Tempo (regional) | |
| Analytics subset | Warehouse sink (seam) | Filtered, curated stream for the Phase 4 forecasting agent's historical training data |
| Live streams | Queue consumer groups | The Phase 4 observer agent taps the queue directly; no extra export |

ClickHouse is **not** added as a fourth store; it is noted as a candidate *replacement* for
Loki/Tempo to evaluate if a capability gap appears (one substrate per concern, not two).

## The four cloud seams (multi-cloud goal, GCP first)

Everything above - OTel, the queue, Mimir/Loki/Tempo, Perses, Helm, Operator CRDs - runs identically
on GKE/EKS/AKS. Cloud-specificity is confined to four seams, each a provider interface in the step
engine from day one, with **only the GCP implementation built now**:

| Seam | GCP (now) | AWS (later) | Azure (later) |
|---|---|---|---|
| Object storage (backend blocks) | GCS | S3 | Azure Blob |
| Workload identity (pods -> buckets) | Workload Identity Federation | IRSA | Azure Workload Identity |
| Warehouse sink (analytics subset) | BigQuery (via queue subscription/loader) | Athena/Redshift | Synapse |
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
