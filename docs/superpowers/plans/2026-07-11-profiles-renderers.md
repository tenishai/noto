# Profiles, Renderers, and Contract Tests - Implementation Plan (issue #17)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core of Argus - layered observability profiles, the OTTL filter-rule compiler,
typed-dict renderers for every artifact family, and the contract tests that lock every pipeline
opinion behind golden files and invariants.

**Architecture:** Pure logic, no cloud. YAML profiles (baseline + class + override) validated by
pydantic and merged by a pure resolver into a frozen `ResolvedProfile`; renderers are pure
functions `(ResolvedProfile, Context) -> dict` serialized with `yaml.dump` only at the edges;
contract tests diff golden files per (class x topology) and assert invariants over every rendered
pipeline. Spec: `docs/superpowers/specs/2026-07-11-profiles-renderers-design.md`.

**Tech Stack:** Python 3.14.6, pydantic 2.13.4, PyYAML 6.0.3, pytest 9.1.1 (all pinned exact).
Render targets: OpenTelemetryCollector CRDs (opentelemetry.io/v1beta1), ServiceMonitor/PodMonitor
(monitoring.coreos.com/v1), Sloth v1 `PrometheusServiceLevel` (sloth.slok.dev/v1), Perses API
resources, PrometheusRule.

## Global Constraints

- No em dashes in prose or docstrings; spaced hyphen instead (repo hard convention, linted).
- Every new dependency pinned exact and recorded in `docs/versions.md` (pydantic 2.13.4,
  pyyaml 6.0.3, verified 2026-07-11).
- `make test` (lint + unit + contract) must be green at every commit; TDD - failing test first.
- Scale numbers are inputs, never constants (design.md scale-posture row): retention, budgets,
  batch sizes, HPA bounds all flow from profile fields or `Context`.
- Pipeline opinions that contract tests enforce (design.md "Collector configuration defaults" +
  ADR): memory_limiter first, batch last; k8sattributes passthrough on agent / enrich on gateway;
  `resource` not `attributes`; groupbyattrs for KSM/cAdvisor; OTLP+zstd via native OTLP ingest
  (gRPC Tempo :4317, HTTP Mimir `/otlp/v1/metrics`, HTTP Loki `/otlp/v1/logs`); file_storage-backed
  sending queue on the solo gateway; tenancy (`X-Scope-OrgID` from `Context.tenant`) stamped exactly
  once; drop rules present at exactly their declared stage; statsd receiver never in a default app
  pipeline.
- Golden regeneration only via `make regen-golden`, reviewed as a PR diff.
- CHANGELOG entry with the final task.

## File Structure

```
agent/argus/profiles/__init__.py      re-exports: load_baseline, load_class, resolve, ResolvedProfile
agent/argus/profiles/models.py        pydantic models: DropRule, SLO, Dashboard, BaselineProfile,
                                      ClassProfile, Override, ResolvedProfile
agent/argus/profiles/loader.py        load_baseline(path), load_class(path), load_override(dict)
agent/argus/profiles/resolve.py       resolve(baseline, cls, override) -> ResolvedProfile
agent/argus/rendering/__init__.py     re-exports render_all + per-family renderers
agent/argus/rendering/context.py      Context model (topology, tenant, cluster, app, namespace, env)
agent/argus/rendering/ottl.py         compile_rules(rules, stage) -> list[str] (OTTL statements)
agent/argus/rendering/collectors.py   render_agent / render_gateway / render_ingest / render_targetallocator
agent/argus/rendering/scrape.py       render_servicemonitor / render_podmonitor
agent/argus/rendering/slo.py          render_sloth(profile, ctx) -> dict (PrometheusServiceLevel)
agent/argus/rendering/perses.py       render_perses(profile, ctx) -> list[dict]
agent/argus/rendering/alerts.py       render_alert_rules(profile, ctx) -> dict (PrometheusRule)
profiles/baseline.yaml                the baseline opinions
profiles/classes/{web,worker,cron,heavy,gpu}.yaml
tests/unit/test_models.py, test_loader.py, test_resolve.py, test_ottl.py
tests/unit/rendering/test_collectors.py, test_scrape.py, test_slo.py, test_perses.py, test_alerts.py
tests/contract/test_invariants.py
tests/contract/test_golden.py
tests/contract/golden/<class>-<topology>/*.yaml
scripts/regen-golden.py               regenerates golden dir from real profiles
Makefile                              + regen-golden target
```

---

### Task 1: Runtime deps + skeleton packages

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `docs/versions.md` (agent runtime table)
- Create: `agent/argus/profiles/__init__.py`, `agent/argus/rendering/__init__.py` (empty)

**Interfaces:** Produces installed `pydantic==2.13.4`, `pyyaml==6.0.3` for all later tasks.

- [ ] **Step 1:** Add to `pyproject.toml` `[project] dependencies`: `"pydantic==2.13.4", "pyyaml==6.0.3"`.
- [ ] **Step 2:** `uv sync` then `uv run python -c "import pydantic, yaml; print(pydantic.VERSION, yaml.__version__)"` - expect `2.13.4 6.0.3`.
- [ ] **Step 3:** Add both to versions.md agent-runtime table (verified 2026-07-11); create the two empty `__init__.py` files.
- [ ] **Step 4:** `make test` green. Commit: `feat(profiles): runtime deps - pydantic 2.13.4, pyyaml 6.0.3 (verified, pinned)`.

### Task 2: Profile models

**Files:**
- Create: `agent/argus/profiles/models.py`
- Test: `tests/unit/test_models.py`

**Interfaces (produces - exact, later tasks depend on these):**

```python
Stage = Literal["agent", "gateway", "ingest"]

class DropRule(BaseModel):          # extra="forbid", all models frozen=True
    name: str
    stage: Stage
    enabled: bool = True
    log_level_below: str | None = None          # "info" -> drop debug/trace logs
    path_matches: list[str] | None = None        # endpoint substrings for span/log drop
    metric_names: list[str] | None = None
    metric_labels_strip: list[str] | None = None
    namespace_exclude: list[str] | None = None
    raw_ottl: dict[str, list[str]] | None = None # {"logs"|"metrics"|"traces": [stmts]}
    justification: str | None = None             # REQUIRED when raw_ottl set (validator)

class SLO(BaseModel):
    name: str; objective: float                  # 99.9 means 99.9%
    signal: Literal["availability", "latency"]
    latency_threshold: str | None = None         # "500ms", required when signal == latency
    enabled: bool = True

class Dashboard(BaseModel):
    name: str; panels: list[str]; enabled: bool = True

class Budgets(BaseModel):
    retention: dict[str, str]                    # {"metrics": "30d", "logs": "7d", "traces": "7d"}
    cardinality: dict[str, int]                  # per env: {"dev": 200000, "qa": ..., "prod": ...}

class BaselineProfile(BaseModel):
    budgets: Budgets
    drop: list[DropRule] = []
    dashboards: list[Dashboard] = []
    log_collection: Literal["filelog"] = "filelog"   # the shipper seam surface

class ClassProfile(BaseModel):
    class_name: Literal["web", "worker", "cron", "heavy", "gpu"] = Field(alias="class")
    signals: list[Literal["metrics", "logs", "traces"]]
    slos: list[SLO] = []
    dashboards: list[Dashboard] = []
    drop: list[DropRule] = []

class Override(BaseModel):                        # model_config extra="allow" + warn on extras
    profile: str | None = None
    traces: bool | None = None
    retention: dict[str, str] | None = None
    slos: list[SLO] | None = None
    drop: list[DropRule] | None = None
    cardinality_budget: int | None = None
    justification: str | None = None              # REQUIRED when cardinality_budget set

class ResolvedProfile(BaseModel):                 # frozen; what renderers consume
    class_name: str; signals: list[str]; budgets: Budgets
    slos: list[SLO]; dashboards: list[Dashboard]; drop: list[DropRule]
    log_collection: str
```

- [ ] **Step 1:** Write failing tests: valid DropRule roundtrip; `raw_ottl` without `justification` raises `ValidationError`; latency SLO without `latency_threshold` raises; unknown field on `ClassProfile` raises; unknown field on `Override` warns (use `pytest.warns(UserWarning)`) and is retained in `model_extra`.
- [ ] **Step 2:** Run `uv run pytest tests/unit/test_models.py -v` - expect FAIL (module missing).
- [ ] **Step 3:** Implement `models.py` exactly as above (`model_config = ConfigDict(frozen=True, extra="forbid")` everywhere except Override: `extra="allow"` plus a `model_validator(mode="after")` emitting `warnings.warn` per extra key; validators for the two justification rules and latency threshold).
- [ ] **Step 4:** Tests pass; `make test` green. Commit: `feat(profiles): pydantic models for baseline/class/override/resolved`.

### Task 3: Loader

**Files:**
- Create: `agent/argus/profiles/loader.py`
- Test: `tests/unit/test_loader.py` (+ `tests/unit/fixtures/profiles/` minimal YAML)

**Interfaces (produces):**
```python
def load_baseline(path: Path) -> BaselineProfile      # raises ProfileError(file, field) on invalid
def load_class(path: Path) -> ClassProfile
def load_override(data: dict | None) -> Override      # None -> Override()
class ProfileError(ValueError): file: str; detail: str
```

- [ ] **Step 1:** Failing tests: loads valid fixture baseline + class; invalid YAML syntax raises `ProfileError` naming the file; unknown field in class fixture raises `ProfileError` naming file AND field; `load_override(None)` returns empty Override.
- [ ] **Step 2:** Run - FAIL. **Step 3:** Implement with `yaml.safe_load`, wrap `ValidationError` into `ProfileError` including `e.errors()[0]["loc"]`. **Step 4:** Pass; commit `feat(profiles): YAML loader with loud file+field errors`.

### Task 4: Resolver

**Files:**
- Create: `agent/argus/profiles/resolve.py`
- Test: `tests/unit/test_resolve.py`

**Interfaces (produces):** `def resolve(baseline: BaselineProfile, cls: ClassProfile, override: Override) -> ResolvedProfile`

Merge semantics (from spec): scalars last-wins (`override.traces=True` adds `"traces"` to signals;
`override.retention` keys replace `budgets.retention` keys; `override.cardinality_budget` replaces
every env's cardinality value); named collections (slos, dashboards, drop) merge by `name`, later
layer replaces same-name item, `enabled: false` filters it from the resolved lists; resolution
never mutates inputs.

- [ ] **Step 1:** Failing tests: baseline+web produces web signals plus baseline drop rules and dashboards; class drop rule with same `name` as baseline replaces it; override `enabled: false` on an inherited SLO removes it; override retention key replaces only that key; inputs unchanged after resolve (frozen anyway - assert identity of originals' fields).
- [ ] **Step 2:** FAIL. **Step 3:** Implement (dict-by-name merge helper, ~40 lines). **Step 4:** Pass; commit `feat(profiles): layered resolver (baseline + class + override)`.

### Task 5: OTTL compiler

**Files:**
- Create: `agent/argus/rendering/ottl.py`
- Test: `tests/unit/test_ottl.py`

**Interfaces (produces):**
```python
class CompiledFilter(BaseModel):     # per signal, ready for the filter processor
    logs: list[str] = []; metrics: list[str] = []; traces: list[str] = []
def compile_rules(rules: list[DropRule], stage: Stage) -> CompiledFilter
class UnsupportedRule(ValueError): ...
```

Exact OTTL emitted (table-driven; these strings ARE the contract):
- `log_level_below: "info"` -> logs: `severity_number < SEVERITY_NUMBER_INFO`
- `path_matches: ["/health"]` -> traces: `IsMatch(attributes["url.path"], "^/health")`; logs: `IsMatch(attributes["url.path"], "^/health")`
- `metric_names: ["go_gc_.*"]` -> metrics: `IsMatch(name, "^go_gc_.*$")`
- `namespace_exclude: ["kube-system"]` -> all three signals: `resource.attributes["k8s.namespace.name"] == "kube-system"`
- `metric_labels_strip` is NOT a filter drop - compiler raises `UnsupportedRule` pointing to the transform path (rendered by collectors.py as a `transform` processor `delete_key` statement, Task 7).
- `raw_ottl` entries appended verbatim to their signal list.

- [ ] **Step 1:** Failing table-driven tests: one per mapping above (exact string equality), stage filtering (rule with `stage=gateway` excluded when compiling `agent`), disabled rule excluded, raw_ottl passthrough, `metric_labels_strip` raises `UnsupportedRule`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement (~60 lines, pure). **Step 4:** Pass; commit `feat(rendering): OTTL compiler for structured drop rules`.

### Task 6: Context

**Files:**
- Create: `agent/argus/rendering/context.py`
- Test: `tests/unit/rendering/test_context.py`

**Interfaces (produces):**
```python
class Context(BaseModel):            # frozen
    topology: Literal["solo", "regional"]
    cluster: str; tenant: str        # tenant defaults to cluster (validator if unset)
    app: str | None = None; namespace: str | None = None
    environment: Literal["dev", "qa", "prod"]
    region: str | None = None                     # required when topology == regional (validator)
    endpoints: Endpoints                          # backend URLs, defaulted per topology

class Endpoints(BaseModel):
    mimir_otlp_http: str; loki_otlp_http: str; tempo_otlp_grpc: str
    kafka_bootstrap: str | None = None            # required when regional (validator)
```

Defaults (solo): `http://mimir.argus.svc:9009/otlp`, `http://loki.argus.svc:3100/otlp`,
`tempo.argus.svc:4317`. Regional variants take the region's shared endpoints via constructor.

- [ ] **Step 1:** Failing tests: tenant defaults to cluster; regional without region/kafka raises; solo defaults populated. **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** Pass; commit `feat(rendering): Context model with topology validation`.

### Task 7: Collector renderers (the big one)

**Files:**
- Create: `agent/argus/rendering/collectors.py`
- Test: `tests/unit/rendering/test_collectors.py`

**Interfaces (produces):**
```python
def render_agent(p: ResolvedProfile, ctx: Context) -> dict            # OpenTelemetryCollector, mode=daemonset
def render_gateway(p: ResolvedProfile, ctx: Context) -> dict          # mode=deployment
def render_ingest(p: ResolvedProfile, ctx: Context) -> dict           # regional only; raises on solo
def render_targetallocator(p: ResolvedProfile, ctx: Context) -> dict  # mode=statefulset
```

Each returns a full CRD dict: `apiVersion: opentelemetry.io/v1beta1`, `kind: OpenTelemetryCollector`,
`metadata` (name `argus-<role>`, namespace `argus`, labels incl. `argus.dev/managed: "true"`),
`spec.config` holding receivers/processors/pipelines/exporters/extensions.

Pipeline composition rules (implemented as small helpers so invariants hold by construction):
- every pipeline's processors: `["memory_limiter", <middle...>, "batch"]` via `_pipeline(middle)`.
- **agent**: receivers otlp + filelog (from `p.log_collection`, gated on `"logs" in p.signals`) +
  statsd (defined in receivers, wired into NO default pipeline); processors middle:
  `k8sattributes` (passthrough=true, node filter env var), `resourcedetection`,
  `filter/argus-agent` (from `compile_rules(p.drop, "agent")` when non-empty); exporters: otlp ->
  gateway.
- **gateway**: `k8sattributes` (full enrichment), `resource/default` (environment, cluster,
  region from ctx), `transform/labels-strip` (from `metric_labels_strip` rules ->
  `delete_key(attributes, "<label>")` statements), `filter/argus-gateway`, `groupbyattrs` in the
  KSM/cAdvisor metrics path; solo: also `filter/argus-ingest` + routing duties + tenancy header +
  exporters to `ctx.endpoints` with `sending_queue.storage: file_storage` and
  `compression: zstd`, headers `X-Scope-OrgID: ctx.tenant`; regional: single kafka exporter,
  no backend exporters.
- **ingest** (regional): kafka receiver, `filter/argus-ingest`, routing connector table
  (metrics->mimir, logs->loki, traces->tempo per ADR), tenancy stamp, backend exporters as above.
- extensions everywhere: `health_check` (:13133), `zpages`, `pprof`; `file_storage` on
  gateway-solo; self-scrape pipeline (prometheus/self receiver :8888 -> metrics pipeline).
- memory_limiter config: `limit_percentage: 90, spike_limit_percentage: 10, check_interval: 1s`;
  batch: `send_batch_size: 1000, send_batch_max_size: 2000, timeout: 1s` (constants module-level,
  they are opinions not scale inputs; HPA/replica sizing comes from ctx later - NOT rendered here).

- [ ] **Step 1:** Failing tests (unit level; deep invariants live in contract tests): agent config has filelog when logs in signals and not otherwise; statsd defined but absent from all pipeline receiver lists; gateway-solo exporters carry zstd + X-Scope-OrgID + file_storage queue; gateway-regional exports only kafka; render_ingest raises `ValueError` on solo ctx; every pipeline in every role starts memory_limiter / ends batch (loop assertion helper `iter_pipelines(cfg)` - put it in the test module, contract tests will import it from there... no: create `tests/support/otel.py` with `iter_pipelines(crd) -> Iterator[tuple[str, dict]]` so both unit and contract tests share it).
- [ ] **Step 2:** FAIL. **Step 3:** Implement (~250 lines; helpers `_pipeline`, `_exporters(ctx)`, `_filter(name, compiled)`). **Step 4:** Pass; `make test`; commit `feat(rendering): collector CRD renderers - agent/gateway/ingest/targetallocator`.

### Task 8: Scrape renderers

**Files:**
- Create: `agent/argus/rendering/scrape.py`
- Test: `tests/unit/rendering/test_scrape.py`

**Interfaces (produces):**
```python
def render_servicemonitor(p, ctx) -> dict   # monitoring.coreos.com/v1, port "http", path /metrics, interval 15s
def render_podmonitor(p, ctx) -> dict
```
Selector `app.kubernetes.io/name: ctx.app`; labels include `argus.dev/managed: "true"` and
`argus.dev/class: p.class_name`. Requires `ctx.app`/`ctx.namespace` (raise ValueError otherwise).

- [ ] Steps: failing tests (shape, selector, raises without app) -> FAIL -> implement (~40 lines) -> pass -> commit `feat(rendering): ServiceMonitor/PodMonitor renderers`.

### Task 9: Sloth renderer

**Files:**
- Create: `agent/argus/rendering/slo.py`
- Test: `tests/unit/rendering/test_slo.py`

**Interfaces (produces):** `def render_sloth(p, ctx) -> dict | None` (None when no SLOs).

Emits `apiVersion: sloth.slok.dev/v1, kind: PrometheusServiceLevel`, `spec.service = ctx.app`,
one `spec.slos[]` entry per enabled SLO: availability -> error-ratio query pair over
`http_server_request_duration_seconds_count{k8s_namespace_name=...}` with 5xx error events;
latency -> `histogram_quantile`-free ratio form using `_bucket{le="<threshold-seconds>"}`
(Sloth ratio: events not meeting threshold / total). `objective: slo.objective`. Alerting section:
`name: ArgusSLO-<app>-<slo.name>` with page/ticket labels `severity: page|ticket`.

- [ ] Steps: failing tests (availability query contains `status_code=~"5.."`, latency uses the
  le bucket for "500ms" -> `le="0.5"`, objective passthrough, None when empty, disabled SLO
  excluded) -> FAIL -> implement (~80 lines incl. `_duration_to_seconds`) -> pass -> commit
  `feat(rendering): Sloth PrometheusServiceLevel renderer`.

### Task 10: Perses renderer

**Files:**
- Create: `agent/argus/rendering/perses.py`
- Test: `tests/unit/rendering/test_perses.py`

**Interfaces (produces):** `def render_perses(p, ctx) -> list[dict]` - project doc, three
datasource docs, one dashboard doc per enabled `p.dashboards`.

Shapes exactly as verified in the spike (docs/spikes/perses/RESULTS.md): datasources use
`HTTPProxy` with `headers: {"X-Scope-OrgID": ctx.tenant}`; dashboard docs
`kind: Dashboard, metadata: {name, project}, spec: {panels, layouts, duration: "1h"}`; panel
plugin kinds by panel name prefix: `metric:*` -> TimeSeriesChart + PrometheusTimeSeriesQuery,
`logs:*` -> LogsTable + LokiLogQuery, `traces:*` -> TraceTable + TempoTraceQuery (correlated
co-location per dashboard - the spike-proven mitigation).

- [ ] Steps: failing tests (project first, datasource headers carry tenant, panel plugin mapping,
  disabled dashboard excluded) -> FAIL -> implement (~120 lines) -> pass -> commit
  `feat(rendering): Perses project/datasource/dashboard renderers`.

### Task 11: Alert-pack renderer

**Files:**
- Create: `agent/argus/rendering/alerts.py`
- Test: `tests/unit/rendering/test_alerts.py`

**Interfaces (produces):** `def render_alert_rules(p, ctx) -> dict` - one PrometheusRule
(`monitoring.coreos.com/v1`) with two groups: `argus-collector-health` (always:
`ArgusCollectorMemoryHigh` on `otelcol_process_memory_rss`, `ArgusCollectorRefusing` on
`rate(otelcol_receiver_refused_spans[5m]) > 0` and metric/log variants, `ArgusExporterFailing` on
`otelcol_exporter_send_failed_*`) and `argus-app-<app>` (per app: `ArgusScrapeDown` on
`up{job="<app>"} == 0 for 5m` - the portable replacement for gojira's GMP rule).

- [ ] Steps: failing tests (group names, scrape-down expr, always-present collector-health) ->
  FAIL -> implement (~70 lines) -> pass -> commit `feat(rendering): alert packs - collector health + scrape-down`.

### Task 12: The real profiles (baseline + five classes)

**Files:**
- Create: `profiles/baseline.yaml`, `profiles/classes/{web,worker,cron,heavy,gpu}.yaml`
- Test: `tests/unit/test_real_profiles.py`

Content (the opinions, from design.md's class table + ADR filter gradient):
- baseline: retention `{metrics: 30d, logs: 7d, traces: 7d}`; cardinality
  `{dev: 200000, qa: 500000, prod: 1000000}`; drop rules - `health-endpoints`
  (agent, path_matches `[/health, /healthz, /ready, /livez]`), `debug-logs` (agent,
  log_level_below info; applies in every environment - a dev deployment wanting debug logs
  disables it by name in its override, per the resolve semantics); `ksm-churn` (gateway,
  metric_names `[kube_pod_status_reason, kube_pod_tolerations]`), `unbounded-labels` (gateway,
  metric_labels_strip `[pod_uid, request_id]`), `kube-system-noise` (gateway, namespace_exclude
  `[kube-system]`); dashboard `cluster-baseline` (panels: `metric:node-cpu`, `metric:node-memory`,
  `metric:pod-restarts`, `logs:cluster-errors`).
- web: signals `[metrics, logs]`; SLOs `availability-99.9` (99.9) + `latency-p99-500ms` (99.0,
  latency, 500ms); dashboard `web-red` (`metric:request-rate`, `metric:error-rate`,
  `metric:latency-p99`, `traces:slow-requests`, `logs:errors`).
- worker: signals `[metrics, logs]`; SLO `availability-99.5`; dashboard `worker-throughput`
  (`metric:processing-rate`, `metric:queue-depth`, `metric:failure-rate`, `logs:errors`).
- cron: signals `[metrics, logs]`; SLO none (alert-based); dashboard `cron-health`
  (`metric:run-success`, `metric:run-duration`, `logs:errors`).
- heavy: signals `[metrics, logs]`; dashboard `heavy-use` (`metric:cpu-saturation`,
  `metric:memory-saturation`, `metric:io`, `metric:throttling`, `logs:errors`).
- gpu: signals `[metrics, logs]`; dashboard `gpu-use` (heavy panels + `metric:gpu-utilization`,
  `metric:gpu-memory`).

- [ ] **Step 1:** Failing test: `load_baseline(REPO/profiles/baseline.yaml)` and all five classes
  load and resolve against the baseline without error; web resolved profile contains both SLOs and
  the five baseline+class drop rules.
- [ ] **Step 2:** FAIL (files missing). **Step 3:** Write the YAML. **Step 4:** Pass; commit
  `feat(profiles): the shipped opinions - baseline + five class profiles`.

### Task 13: Contract invariants

**Files:**
- Create: `tests/support/otel.py` (move `iter_pipelines` here if not already), `tests/contract/test_invariants.py`
- Delete: `tests/contract/test_placeholder.py`

For every (class x topology) combination, render agent+gateway(+ingest when regional)+TA with the
REAL profiles from Task 12 and assert every Global Constraint bullet (memory_limiter/batch order,
passthrough/enrich split, resource-not-attributes, groupbyattrs presence in KSM path, exporter
endpoints/compression/headers, file_storage on solo gateway only, tenancy stamped exactly once
across the chain, each drop rule's OTTL present at its declared stage and absent elsewhere, statsd
never in default pipelines, routing table == ADR table for regional ingest). Mark all
`@pytest.mark.contract`.

- [ ] Steps: write tests (they should PASS immediately if Tasks 7-12 were honest - any failure is
  a real bug to fix now) -> `make test` green -> commit
  `test(contract): invariant suite over every class x topology`.

### Task 14: Golden files + regen + wrap-up

**Files:**
- Create: `scripts/regen-golden.py`, `tests/contract/test_golden.py`, `tests/contract/golden/**`
- Modify: `Makefile` (regen-golden target), `CHANGELOG.md`

- [ ] **Step 1:** `scripts/regen-golden.py`: for each class x {solo, regional}, resolve real
  profiles with a fixed fixture Context (cluster `golden-cluster`, app `sample-<class>`, namespace
  `sample`, env prod, region `us-east4` for regional), call every renderer, write
  `tests/contract/golden/<class>-<topology>/<artifact>.yaml` with `yaml.safe_dump(..., sort_keys=True)`.
- [ ] **Step 2:** `test_golden.py`: re-render in-test and diff against files byte-exact; a missing
  golden dir fails with the regen instruction. Makefile: `regen-golden: uv run python scripts/regen-golden.py`.
- [ ] **Step 3:** Run regen once, eyeball the diff (this review IS the point), `make test` green.
- [ ] **Step 4:** CHANGELOG entry (profiles + renderers + contract tests shipped; placeholder
  contract test replaced). Commit `test(contract): golden files per class x topology + make regen-golden`.
- [ ] **Step 5:** Push branch, open PR "feat: profiles, renderers, contract tests (closes #17)",
  wait for green checks, merge.

## Self-review (done at planning time)

- Spec coverage: schema/layers (T2-3), merge (T4), filter+hatch (T5), Context seam (T6), all five
  renderer families (T7-11), real opinions (T12), invariants (T13), golden + explicit regen (T14),
  deps pinned (T1), scale-posture (constants limited to opinion values; sizing flows from ctx -
  noted in T7), shipper seam (log_collection field, T2/T7). Gap check: none found.
- The `metric_labels_strip`-is-a-transform subtlety is handled consistently in T5 (compiler raises)
  and T7 (transform renderer) and tested in both.
- Type consistency: `compile_rules(rules, stage) -> CompiledFilter` consumed in T7; `Context`
  fields referenced in T7-11 all exist in T6; `iter_pipelines` lives in `tests/support/otel.py`
  (T7 creates it, T13 imports it).
