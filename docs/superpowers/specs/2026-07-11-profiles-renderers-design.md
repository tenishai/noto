# Design: Profiles, Renderers, and Contract Tests (issue #17)

> **Status:** Approved 2026-07-11 (design approved in-session; decisions below chosen explicitly)
> **Scope:** Build 2 of the Phase 1 plan - the core of Argus. Pure logic, no cloud.
> **Upstream context:** [design.md](../../design.md), [transport-and-routing.md](../../transport-and-routing.md),
> [testing.md](../../testing.md), [spikes/perses/RESULTS.md](../../spikes/perses/RESULTS.md), the real
> gojira contract (design.md "How it plugs into gojira").

## Decisions made during design (with alternatives considered)

1. **Composition: layered** - baseline + class + per-app override, merged in order. (Rejected: flat
   self-contained profiles - baseline edits touch five files; `extends:` chains - YAGNI until
   argus-integrations.)
2. **Filter rules: structured schema + `raw_ottl` escape hatch** with required `justification:`.
   (Rejected: raw OTTL only - write-only for humans; structured-only - first weird rule blocks on a
   schema PR.)
3. **Rendered output: ephemeral** - in-memory dicts the step engine applies; the only persisted
   renders are golden files in `tests/contract/golden/`, which double as reviewable per-class
   examples. `.argus/` (gitignored) for local debugging. (Rejected: committed rendered/ dir -
   re-invents the GitOps artifact; ledger-attached renders - add later if forensics demand.)
4. **SLO render target: Sloth** - closes the open Sloth-vs-Pyrra question. Profiles carry a
   tool-neutral `slos:` block; the renderer emits Sloth specs; Sloth emits the multiwindow-multiburn
   `PrometheusRule`s. Pyrra remains the recorded alternative (operator + UI we do not want).
5. **Renderer mechanism: typed models -> `yaml.dump`** - pydantic models for schema, pure Python
   functions building typed dicts per artifact. No Jinja2: YAML-in-text-templates is the classic
   indentation-bug factory and collector configs are deeply nested. (gojira's Jinja shape noted and
   deliberately not copied for these artifacts.)

## Profile schema

Three layers, all YAML, all pydantic-validated at load:

- `profiles/baseline.yaml` - what every app/cluster gets: cluster scrape sources, log collection,
  default retention and cardinality budgets per environment, baseline drop rules (health-check
  endpoints, debug logs outside dev, scrape metadata).
- `profiles/classes/{web,worker,cron,heavy,gpu}.yaml` - per-class signals, golden-signal SLOs,
  dashboard set, class drop rules (mirrors the class table in design.md).
- The per-app override - the proposed `observability:` block shape (profile, traces, retention,
  slos, overrides.cardinality_budget + justification), supplied by the gojira-reader (#19); in #17
  it is a plain dict input with fixtures.

Validation posture: unknown fields are **errors** in baseline/class files (they are ours); unknown
fields in the override block **warn** (forward compatibility with gojira's contract evolution).

## Merge semantics

`resolve(baseline, class_profile, override) -> ResolvedProfile` - a pure function.

- Scalars: last layer wins.
- Named collections (SLOs, dashboards, drop rules): merge by `name`; a later layer may disable an
  inherited item with `enabled: false`.
- No other composition. `ResolvedProfile` is frozen; renderers cannot mutate it.

## Filter rules

Typed `drop:` rules, each tagged with `stage: agent | gateway | ingest` (the ADR's three-stage
gradient). Initial rule types: `log_level_below` (per environment), `path_matches` (endpoint
spans/logs), `metric_names`, `metric_labels_strip`, `namespace_exclude`. A pure-function compiler
maps each rule to exact OTTL for the `filter` processor; table-driven unit tests assert rule-in /
OTTL-out. `raw_ottl:` per stage passes through verbatim and requires `justification:`. The compiler
rejects anything it cannot express (that is what the hatch is for). The same schema is the future
surface for observer-agent team configs; `stage: ingest` leaves room for Phase 2/3
aggregate-then-discard-raw without schema changes.

## Renderers

One module per artifact family, every renderer `(ResolvedProfile, Context) -> dict` (or list of
dicts), serialized with `yaml.dump` at the edge only:

- **Collector CRDs**: `OpenTelemetryCollector` for the `agent` (DaemonSet), `gateway` (Deployment),
  and `ingest` (regional consumer) roles, plus the TargetAllocator. Topology-aware: in `solo`, the
  gateway absorbs ingest duties (filter stage 3, routing, tenancy stamping) and uses the
  file_storage-backed sending queue; in `regional`, gateway exports to Kafka and the ingest role
  consumes.
- **Scrape wiring**: ServiceMonitor/PodMonitor per app (the GMP-superseding equivalents).
- **Sloth specs** from the neutral `slos:` block.
- **Perses resources**: project, datasources (with tenancy headers), dashboards per profile -
  encoding the spike gotchas (never mount over `/etc/perses`; datasource proxy shapes verified in
  the spike).
- **Alert packs**: PrometheusRule for non-SLO alerts (collector health, scrape-down).

`Context` carries: topology size, tenant id, cluster/app/namespace identity, environment. It is the
seam #19 (gojira-reader) fills; #17 constructs it from fixtures.

## Contract tests (testing.md layer 3)

- **Golden files**: full rendered output per (class x topology) under `tests/contract/golden/`,
  compared exactly. Regeneration is explicit (`make regen-golden`) and reviewed as a PR diff, never
  automatic.
- **Invariant assertions** over every rendered pipeline, independent of golden coverage:
  `memory_limiter` first and `batch` last; k8sattributes passthrough on agent, enrichment on
  gateway; `resource` not `attributes` for env enrichment; groupbyattrs for KSM/cAdvisor; OTLP +
  zstd to backends via each backend's native OTLP ingest (gRPC to Tempo, HTTP paths for Mimir
  `/otlp/v1/metrics` and Loki `/otlp/v1/logs` per the spike - no prometheusremotewrite in the happy
  path); file_storage-backed sending queue on the solo gateway; tenancy stamped exactly once;
  every profile drop rule present at its declared stage and no other; routing table matches the ADR;
  statsd receiver present but never in a default app pipeline (legacy-only stance).

## Package layout and dependencies

```
agent/argus/profiles/   models.py (pydantic schema), loader.py, resolve.py
agent/argus/rendering/  context.py, collectors.py, scrape.py, slo.py, perses.py, alerts.py, ottl.py
profiles/               baseline.yaml, classes/*.yaml   (the YAML opinions themselves)
tests/unit/             resolver, loader, ottl compiler
tests/contract/         golden/ + invariant tests
```

New runtime deps, pinned exact and recorded in versions.md at introduction: `pydantic`, `pyyaml`.

## Scale posture (amended 2026-07-11)

Scale parameters are **inputs, never constants**: retention, cardinality budgets, batch/queue
sizing, and HPA bounds all flow from profile fields and `Context`, so the same renderers serve a
one-cluster dev deployment and a massive multi-region estate. The profile's log-collection section
is also the surface for the log-shipper seam (filelog default, Fluent Bit alternative - see the
decisions table in design.md); #17 renders the filelog default only, but the schema must not
assume it is the only possible shipper.

## Error handling

Load-time schema violations fail loud, naming file and field. A resolved profile referencing a
nonexistent dashboard/SLO is a validation error, not a warning. Renderers never silently skip a
section.

## Out of scope

Applying anything (step engine, #18); reading real `gojira.yaml` (#19); Helm values (#22); the ocb
manifest (#20); aggregate-then-discard-raw rule types (Phase 2/3 - the stage enum leaves room).
