"""Contract invariants: every pipeline opinion, asserted over the output that actually
ships, in every topology.

This suite is how "non-negotiable defaults" (design.md) become enforceable. It renders
the REAL shipped profiles (profiles/) - not fixtures - so drift in either the renderers
or the YAML opinions fails here.

The collector tier is platform-owned: one agent DaemonSet, one gateway and one target
allocator per cluster, rendered from the platform profile and never from an app. So the
pipeline invariants are asserted over the PLATFORM output (topology-parameterized), and
the per-class parameterization now guards the other half of that contract: no class, in
any topology, can put a collector into one app's output.
"""

from pathlib import Path

import pytest
from tests.support.otel import config_of, iter_pipelines

from argus import cli
from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.models import ResolvedProfile
from argus.profiles.resolve import resolve
from argus.rendering.collectors import (
    render_agent,
    render_gateway,
    render_ingest,
    render_targetallocator,
)
from argus.rendering.context import Context
from argus.rendering.ottl import compile_rules
from argus.rendering.rbac import render_observe_rbac
from argus.rendering.slo import render_sloth

REPO = Path(__file__).parents[2]
PROFILES = REPO / "profiles"
CLASSES = ["web", "worker", "cron", "heavy", "gpu", "agent", "gateway"]
TOPOLOGIES = ["solo", "regional"]

pytestmark = pytest.mark.contract


def _profile(class_name: str):
    return resolve(
        load_baseline(PROFILES / "baseline.yaml"),
        load_class(PROFILES / "classes" / f"{class_name}.yaml"),
        load_override(None),
    )


def _ctx(topology: str, class_name: str) -> Context:
    kw: dict = dict(
        topology=topology,
        cluster="golden-cluster",
        environment="prod",
        app=f"sample-{class_name}",
        namespace="sample",
    )
    if topology == "regional":
        kw["region"] = "us-east4"
        kw["endpoints"] = {
            "mimir_otlp_http": "http://mimir.us-east4.argus.internal:9009/otlp",
            "loki_otlp_http": "http://loki.us-east4.argus.internal:3100/otlp",
            "tempo_otlp_grpc": "tempo.us-east4.argus.internal:4317",
            "kafka_bootstrap": "redpanda.us-east4.argus.internal:9092",
        }
    return Context(**kw)


def _platform_ctx(topology: str) -> Context:
    """The collector tier is platform-owned, so its Context carries no app and no
    namespace: nothing in the shared tier is allowed to depend on one app."""
    kw: dict = dict(topology=topology, cluster="golden-cluster", environment="prod")
    if topology == "regional":
        kw["region"] = "us-east4"
        kw["endpoints"] = {
            "mimir_otlp_http": "http://mimir.us-east4.argus.internal:9009/otlp",
            "loki_otlp_http": "http://loki.us-east4.argus.internal:3100/otlp",
            "tempo_otlp_grpc": "tempo.us-east4.argus.internal:4317",
            "kafka_bootstrap": "redpanda.us-east4.argus.internal:9092",
        }
    return Context(**kw)


def _role(docs: list[dict], role: str) -> dict:
    def _labels(doc: dict) -> dict:
        return doc.get("metadata", {}).get("labels", {})

    return next(d for d in docs if _labels(d).get("argus.dev/role") == role)


def test_observing_an_app_without_traces_does_not_change_the_gateway() -> None:
    """The bug this invariant exists to keep dead. The collector tier is SHARED - one
    gateway, one agent DaemonSet, one target allocator per cluster - and it used to be
    re-rendered from the profile of whichever single app was being observed, then
    server-side applied: last writer wins. Observing an app whose class has no traces
    signal therefore DELETED the traces pipeline out from under every other app in the
    cluster. The tier is platform-owned now: per-app output carries no collector at all,
    and the platform gateway always carries all three signals."""
    p = _profile("web")
    assert "traces" not in p.signals, "fixture drift: this test needs a traces-less class"

    app_families = cli._render_families(p, _ctx("solo", "web"))
    assert "collectors" not in app_families
    for family, docs in app_families.items():
        for doc in docs:
            assert doc.get("kind") != "OpenTelemetryCollector", family

    platform = cli._render_platform_families(_platform_ctx("solo"))
    pipelines = dict(iter_pipelines(_role(platform["collectors"], "gateway")))
    assert {"metrics", "logs", "traces", "metrics/self"} <= set(pipelines)


@pytest.mark.parametrize("class_name", CLASSES)
def test_per_app_output_carries_no_collector_for_any_class(class_name: str) -> None:
    families = cli._render_families(_profile(class_name), _ctx("solo", class_name))
    for family, docs in families.items():
        assert family != "collectors"
        for doc in docs:
            assert doc.get("kind") != "OpenTelemetryCollector", (class_name, family)


def test_platform_profile_has_all_signals_and_only_baseline_rules(monkeypatch) -> None:
    """The platform profile is the baseline with every signal on and no class delta -
    and it is built without reading a single class file, so no class can ever shape the
    shared tier by accident."""
    baseline = load_baseline(PROFILES / "baseline.yaml")
    p = cli._platform_profile()
    assert set(p.signals) == {"metrics", "logs", "traces"}
    assert [r.name for r in p.drop] == [r.name for r in baseline.drop if r.enabled]
    assert p.slos == []

    ctx = _platform_ctx("solo")
    expected = ResolvedProfile(
        class_name="platform",
        signals=["metrics", "logs", "traces"],
        app_scrape=True,  # unused by the collector tier; carried for shape only
        budgets=baseline.budgets,
        slos=[],
        dashboards=[d for d in baseline.dashboards if d.enabled],
        drop=[r for r in baseline.drop if r.enabled],
        log_collection=baseline.log_collection,
    )
    first = cli._render_platform_families(ctx)
    assert _role(first["collectors"], "gateway") == render_gateway(expected, ctx)

    def _no_class_files(*_args, **_kwargs):
        raise AssertionError("the platform collector tier must not read a class profile")

    monkeypatch.setattr(cli, "load_class", _no_class_files)
    assert cli._render_platform_families(ctx) == first


def _tier_profile():
    """The one profile the shared collector tier is ever rendered from."""
    return cli._platform_profile()


def _all_crds(topology: str) -> list[dict]:
    p = _tier_profile()
    ctx = _platform_ctx(topology)
    crds = [
        render_agent(p, ctx),
        render_gateway(p, ctx),
        render_targetallocator(p, ctx),
    ]
    if topology == "regional":
        crds.append(render_ingest(p, ctx))
    return crds


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_platform_gateway_carries_every_signal(topology: str) -> None:
    """The shared gateway carries all three signals plus its own self-scrape, always.
    No app and no class can take one away - that removal was the outage lever."""
    pipelines = dict(iter_pipelines(render_gateway(_tier_profile(), _platform_ctx(topology))))
    assert set(pipelines) == {"metrics", "logs", "traces", "metrics/self"}


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_memory_limiter_first_batch_last(topology: str) -> None:
    for crd in _all_crds(topology):
        for name, pipeline in iter_pipelines(crd):
            procs = pipeline["processors"]
            assert procs[0] == "memory_limiter", (crd["metadata"]["name"], name)
            assert procs[-1] == "batch", (crd["metadata"]["name"], name)


def test_k8sattributes_split() -> None:
    p, ctx = _tier_profile(), _platform_ctx("solo")
    agent = render_agent(p, ctx)
    gateway = render_gateway(p, ctx)
    assert config_of(agent)["processors"]["k8sattributes"]["passthrough"] is True
    assert config_of(gateway)["processors"]["k8sattributes"].get("passthrough") is not True


def test_resource_not_attributes_for_env_enrichment() -> None:
    gateway = render_gateway(_tier_profile(), _platform_ctx("solo"))
    processors = config_of(gateway)["processors"]
    assert "resource/default" in processors
    assert not any(name.startswith("attributes") for name in processors)
    keys = {a["key"] for a in processors["resource/default"]["attributes"]}
    assert {"environment", "cluster"} <= keys


def test_groupbyattrs_in_metrics_path() -> None:
    gateway = render_gateway(_tier_profile(), _platform_ctx("solo"))
    pipelines = dict(iter_pipelines(gateway))
    assert "groupbyattrs" in pipelines["metrics"]["processors"]


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_backend_exporters_native_otlp_zstd(topology: str) -> None:
    p, ctx = _tier_profile(), _platform_ctx(topology)
    stamping_crd = render_gateway(p, ctx) if topology == "solo" else render_ingest(p, ctx)
    exporters = config_of(stamping_crd)["exporters"]
    assert exporters["otlphttp/mimir"]["endpoint"].endswith("/otlp")
    assert exporters["otlphttp/loki"]["endpoint"].endswith("/otlp")
    assert ":4317" in exporters["otlp/tempo"]["endpoint"]
    for name in ("otlphttp/mimir", "otlphttp/loki", "otlp/tempo"):
        assert exporters[name]["compression"] == "zstd", name
    assert not any("prometheusremotewrite" in name for name in exporters)


def test_solo_gateway_file_storage_queue() -> None:
    gateway = render_gateway(_tier_profile(), _platform_ctx("solo"))
    cfg = config_of(gateway)
    assert "file_storage" in cfg["extensions"]
    for name in ("otlphttp/mimir", "otlphttp/loki", "otlp/tempo"):
        assert cfg["exporters"][name]["sending_queue"]["storage"] == "file_storage"


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_tenancy_stamped_exactly_once(topology: str) -> None:
    stamps = 0
    for crd in _all_crds(topology):
        for exporter in config_of(crd)["exporters"].values():
            if exporter.get("headers", {}).get("X-Scope-OrgID"):
                stamps += 1
    # exactly one stamping component (3 backend exporters on it), never two components
    assert stamps == 3, (
        f"{topology}: expected one stamping component, got {stamps} stamped exporters"
    )


def test_drop_rules_present_at_declared_stage_only() -> None:
    p = _tier_profile()
    ctx = _platform_ctx("regional")
    agent = render_agent(p, ctx)
    gateway = render_gateway(p, ctx)
    ingest = render_ingest(p, ctx)
    stage_crd = {"agent": agent, "gateway": gateway, "ingest": ingest}

    for stage, crd in stage_crd.items():
        compiled = compile_rules([r for r in p.drop if not r.metric_labels_strip], stage)  # type: ignore[arg-type]
        filter_name = f"filter/argus-{stage}"
        processors = config_of(crd)["processors"]
        if compiled.is_empty():
            assert filter_name not in processors
            continue
        spec = processors[filter_name]
        for signal, statements in (
            ("logs", compiled.logs),
            ("metrics", compiled.metrics),
            ("traces", compiled.traces),
        ):
            if statements:
                key = {"logs": "log_record", "metrics": "metric", "traces": "span"}[signal]
                assert spec[signal][key] == statements
        # and this stage's statements appear in NO other role's filter
        for other_stage, other_crd in stage_crd.items():
            if other_stage == stage:
                continue
            other = config_of(other_crd)["processors"].get(f"filter/argus-{other_stage}", {})
            flattened = [
                stmt
                for section in other.values()
                if isinstance(section, dict)
                for stmts in section.values()
                for stmt in stmts
            ]
            for stmt_list in (compiled.logs, compiled.metrics, compiled.traces):
                for stmt in stmt_list:
                    other_compiled = compile_rules(
                        [r for r in p.drop if not r.metric_labels_strip],
                        other_stage,  # type: ignore[arg-type]
                    )
                    if stmt not in (
                        other_compiled.logs + other_compiled.metrics + other_compiled.traces
                    ):
                        assert stmt not in flattened, (stage, other_stage, stmt)


def test_labels_strip_renders_as_transform() -> None:
    gateway = render_gateway(_tier_profile(), _platform_ctx("solo"))
    transform = config_of(gateway)["processors"]["transform/labels-strip"]
    statements = transform["metric_statements"][0]["statements"]
    assert 'delete_key(attributes, "pod_uid")' in statements
    assert 'delete_key(attributes, "request_id")' in statements


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_statsd_never_in_default_pipelines(topology: str) -> None:
    for crd in _all_crds(topology):
        receivers = config_of(crd)["receivers"]
        for _, pipeline in iter_pipelines(crd):
            assert "statsd" not in pipeline["receivers"]
        if crd["metadata"]["labels"]["argus.dev/role"] == "agent":
            assert "statsd" in receivers  # defined as the legacy affordance


def test_regional_routing_matches_adr() -> None:
    """The ADR routing table: metrics -> Mimir, logs -> Loki, traces -> Tempo."""
    p = _tier_profile()
    ingest = render_ingest(p, _platform_ctx("regional"))
    pipelines = dict(iter_pipelines(ingest))
    expected = {"metrics": "otlphttp/mimir", "logs": "otlphttp/loki", "traces": "otlp/tempo"}
    for signal in p.signals:
        assert pipelines[signal]["exporters"] == [expected[signal]], signal
        assert pipelines[signal]["receivers"] == ["kafka"], signal


def test_self_scrape_pipeline_on_gateway() -> None:
    gateway = render_gateway(_tier_profile(), _platform_ctx("solo"))
    pipelines = dict(iter_pipelines(gateway))
    assert "metrics/self" in pipelines
    assert pipelines["metrics/self"]["receivers"] == ["prometheus/self"]
    ext = config_of(gateway)["extensions"]
    assert ext["health_check"]["endpoint"].endswith(":13133")
    assert "zpages" in ext and "pprof" in ext


def test_integrations_autodiscovery_wired() -> None:
    """The shipped catalog renders into the agent: k8s_observer + receiver_creator
    in the metrics pipeline, with the exact contract rule strings."""
    from argus.integrations.catalog import load_catalog
    from argus.rendering.collectors import render_agent

    catalog = load_catalog(REPO / "integrations")
    agent = render_agent(_tier_profile(), _platform_ctx("solo"), catalog=catalog)
    cfg = config_of(agent)
    assert "k8s_observer" in cfg["extensions"]
    assert "k8s_observer" in cfg["service"]["extensions"]
    creator = cfg["receivers"]["receiver_creator"]
    assert creator["watch_observers"] == ["k8s_observer"]
    assert (
        'pod.labels["argus.dev/integration"] == "redis"'
        in (creator["receivers"]["redis/argus-redis"]["rule"])
    )
    pipelines = dict(iter_pipelines(agent))
    assert "receiver_creator" in pipelines["metrics"]["receivers"]


# --- Task 6: argus-observe RBAC is exactly as narrow as observe needs ------------------
#
# `render_observe_rbac`'s ClusterRole grants exactly three resources
# (servicemonitors, podmonitors, prometheusrules under monitoring.coreos.com). The
# invariant this suite proves: those three resources are sufficient for every kind
# `argus observe` can be asked to server-side apply, in every class and topology - so
# the identity a provisioner's Job runs as is narrow AND complete, never one requiring
# a later widening.

_GRANTED_KINDS = {"ServiceMonitor", "PodMonitor", "PrometheusRule"}


@pytest.mark.parametrize("class_name", CLASSES)
@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_observe_rbac_covers_every_kind_scrape_and_alerts_can_render(
    class_name: str, topology: str
) -> None:
    """`scrape` and `alerts` need no local generator step: their rendered kind IS the
    kind `ApplyRendered` server-side applies, so this checks them directly against the
    real per-app families the way the contract tests render everything else."""
    families = cli._render_families(_profile(class_name), _ctx(topology, class_name))
    for family in ("scrape", "alerts"):
        for doc in families.get(family, []):
            assert doc["kind"] in _GRANTED_KINDS, (class_name, topology, family, doc["kind"])


@pytest.mark.parametrize("class_name", ["web", "worker"])  # the only classes with slos
def test_render_sloth_itself_is_a_generator_input_not_an_applied_kind(class_name: str) -> None:
    """The one kind `render_sloth` renders (`PrometheusServiceLevel`, a Sloth CR) is
    OUTSIDE the three granted resources - documented and locked here rather than
    silently assumed. This is not an RBAC gap: `_generate_slo_rules` (agent/argus/
    cli.py) always converts this document into real PrometheusRule documents via the
    pinned, LOCAL `sloth generate` binary (a subprocess, never kubectl) before
    `_run_observe` ever builds the `ApplyRendered(family="slo")` step - see
    `test_generate_slo_rules_via_real_sloth` (tests/unit/test_cli.py) and Sloth's own
    "generator, not an operator" design decision. The ServiceAccount never needs
    `sloth.slok.dev` permissions because that document is never sent to the API
    server. Widening the ClusterRole for it would be over-permissioning for a kind
    `observe` never applies - see the next test for the kind that actually reaches
    ApplyRendered."""
    p = _profile(class_name)
    ctx = _ctx("solo", class_name)
    doc = render_sloth(p, ctx)
    assert doc is not None, "fixture drift: this class is expected to carry an slo"
    assert doc["kind"] == "PrometheusServiceLevel"
    assert doc["kind"] not in _GRANTED_KINDS


_SLOTH_BIN = REPO / ".argus" / "tools" / "sloth-0.16.0"


@pytest.mark.skipif(
    not _SLOTH_BIN.exists(), reason="pinned sloth binary not fetched (run `make validate`)"
)
@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize("class_name", ["web", "worker"])  # the only classes with slos
def test_observe_rbac_covers_the_kind_slo_actually_applies(class_name: str, topology: str) -> None:
    """What `ApplyRendered(family="slo")` actually receives, end to end through the
    real pinned `sloth generate` binary - the same conversion `_run_observe` performs -
    is always a PrometheusRule, one of the three granted kinds."""
    p = _profile(class_name)
    ctx = _ctx(topology, class_name)
    sloth_doc = render_sloth(p, ctx)
    assert sloth_doc is not None
    applied = cli._generate_slo_rules(sloth_doc, ctx.namespace or "default")
    assert applied, "sloth generate produced no PrometheusRule documents"
    for doc in applied:
        assert doc["kind"] in _GRANTED_KINDS, (class_name, topology, doc["kind"])


def test_observe_rbac_cluster_role_grants_exactly_the_three_resources() -> None:
    _sa, cluster_role, *_rest = render_observe_rbac("argus")
    assert cluster_role["kind"] == "ClusterRole"
    assert len(cluster_role["rules"]) == 1
    rule = cluster_role["rules"][0]
    assert set(rule["resources"]) == {"servicemonitors", "podmonitors", "prometheusrules"}
    assert rule["apiGroups"] == ["monitoring.coreos.com"]
