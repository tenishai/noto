"""Contract invariants: every pipeline opinion, asserted over every class x topology.

This suite is how "non-negotiable defaults" (design.md) become enforceable. It renders
the REAL shipped profiles (profiles/) - not fixtures - so drift in either the renderers
or the YAML opinions fails here.
"""

from pathlib import Path

import pytest
from tests.support.otel import config_of, iter_pipelines

from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.resolve import resolve
from argus.rendering.collectors import (
    render_agent,
    render_gateway,
    render_ingest,
    render_targetallocator,
)
from argus.rendering.context import Context
from argus.rendering.ottl import compile_rules

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


def _all_crds(class_name: str, topology: str) -> list[dict]:
    p = _profile(class_name)
    ctx = _ctx(topology, class_name)
    crds = [
        render_agent(p, ctx),
        render_gateway(p, ctx),
        render_targetallocator(p, ctx),
    ]
    if topology == "regional":
        crds.append(render_ingest(p, ctx))
    return crds


@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize("class_name", CLASSES)
def test_memory_limiter_first_batch_last(class_name: str, topology: str) -> None:
    for crd in _all_crds(class_name, topology):
        for name, pipeline in iter_pipelines(crd):
            procs = pipeline["processors"]
            assert procs[0] == "memory_limiter", (crd["metadata"]["name"], name)
            assert procs[-1] == "batch", (crd["metadata"]["name"], name)


@pytest.mark.parametrize("class_name", CLASSES)
def test_k8sattributes_split(class_name: str) -> None:
    p, ctx = _profile(class_name), _ctx("solo", class_name)
    agent = render_agent(p, ctx)
    gateway = render_gateway(p, ctx)
    assert config_of(agent)["processors"]["k8sattributes"]["passthrough"] is True
    assert config_of(gateway)["processors"]["k8sattributes"].get("passthrough") is not True


@pytest.mark.parametrize("class_name", CLASSES)
def test_resource_not_attributes_for_env_enrichment(class_name: str) -> None:
    gateway = render_gateway(_profile(class_name), _ctx("solo", class_name))
    processors = config_of(gateway)["processors"]
    assert "resource/default" in processors
    assert not any(name.startswith("attributes") for name in processors)
    keys = {a["key"] for a in processors["resource/default"]["attributes"]}
    assert {"environment", "cluster"} <= keys


@pytest.mark.parametrize("class_name", CLASSES)
def test_groupbyattrs_in_metrics_path(class_name: str) -> None:
    gateway = render_gateway(_profile(class_name), _ctx("solo", class_name))
    pipelines = dict(iter_pipelines(gateway))
    assert "groupbyattrs" in pipelines["metrics"]["processors"]


@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize("class_name", CLASSES)
def test_backend_exporters_native_otlp_zstd(class_name: str, topology: str) -> None:
    p, ctx = _profile(class_name), _ctx(topology, class_name)
    stamping_crd = render_gateway(p, ctx) if topology == "solo" else render_ingest(p, ctx)
    exporters = config_of(stamping_crd)["exporters"]
    assert exporters["otlphttp/mimir"]["endpoint"].endswith("/otlp")
    assert exporters["otlphttp/loki"]["endpoint"].endswith("/otlp")
    assert ":4317" in exporters["otlp/tempo"]["endpoint"]
    for name in ("otlphttp/mimir", "otlphttp/loki", "otlp/tempo"):
        assert exporters[name]["compression"] == "zstd", name
    assert not any("prometheusremotewrite" in name for name in exporters)


@pytest.mark.parametrize("class_name", CLASSES)
def test_solo_gateway_file_storage_queue(class_name: str) -> None:
    gateway = render_gateway(_profile(class_name), _ctx("solo", class_name))
    cfg = config_of(gateway)
    assert "file_storage" in cfg["extensions"]
    for name in ("otlphttp/mimir", "otlphttp/loki", "otlp/tempo"):
        assert cfg["exporters"][name]["sending_queue"]["storage"] == "file_storage"


@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize("class_name", CLASSES)
def test_tenancy_stamped_exactly_once(class_name: str, topology: str) -> None:
    crds = _all_crds(class_name, topology)
    stamps = 0
    for crd in crds:
        for exporter in config_of(crd)["exporters"].values():
            if exporter.get("headers", {}).get("X-Scope-OrgID"):
                stamps += 1
    # exactly one stamping component (3 backend exporters on it), never two components
    assert stamps == 3, (
        f"{class_name}/{topology}: expected one stamping component, got {stamps} stamped exporters"
    )


@pytest.mark.parametrize("class_name", CLASSES)
def test_drop_rules_present_at_declared_stage_only(class_name: str) -> None:
    p = _profile(class_name)
    ctx = _ctx("regional", class_name)
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


@pytest.mark.parametrize("class_name", CLASSES)
def test_labels_strip_renders_as_transform(class_name: str) -> None:
    gateway = render_gateway(_profile(class_name), _ctx("solo", class_name))
    transform = config_of(gateway)["processors"]["transform/labels-strip"]
    statements = transform["metric_statements"][0]["statements"]
    assert 'delete_key(attributes, "pod_uid")' in statements
    assert 'delete_key(attributes, "request_id")' in statements


@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize("class_name", CLASSES)
def test_statsd_never_in_default_pipelines(class_name: str, topology: str) -> None:
    for crd in _all_crds(class_name, topology):
        receivers = config_of(crd)["receivers"]
        for _, pipeline in iter_pipelines(crd):
            assert "statsd" not in pipeline["receivers"]
        if crd["metadata"]["labels"]["argus.dev/role"] == "agent":
            assert "statsd" in receivers  # defined as the legacy affordance


@pytest.mark.parametrize("class_name", CLASSES)
def test_regional_routing_matches_adr(class_name: str) -> None:
    """The ADR routing table: metrics -> Mimir, logs -> Loki, traces -> Tempo."""
    p = _profile(class_name)
    ingest = render_ingest(p, _ctx("regional", class_name))
    pipelines = dict(iter_pipelines(ingest))
    expected = {"metrics": "otlphttp/mimir", "logs": "otlphttp/loki", "traces": "otlp/tempo"}
    for signal in p.signals:
        assert pipelines[signal]["exporters"] == [expected[signal]], signal
        assert pipelines[signal]["receivers"] == ["kafka"], signal


@pytest.mark.parametrize("class_name", CLASSES)
def test_self_scrape_pipeline_on_gateway(class_name: str) -> None:
    gateway = render_gateway(_profile(class_name), _ctx("solo", class_name))
    pipelines = dict(iter_pipelines(gateway))
    assert "metrics/self" in pipelines
    assert pipelines["metrics/self"]["receivers"] == ["prometheus/self"]
    ext = config_of(gateway)["extensions"]
    assert ext["health_check"]["endpoint"].endswith(":13133")
    assert "zpages" in ext and "pprof" in ext


@pytest.mark.parametrize("class_name", CLASSES)
def test_integrations_autodiscovery_wired(class_name: str) -> None:
    """The shipped catalog renders into every agent: k8s_observer + receiver_creator
    in the metrics pipeline, with the exact contract rule strings."""
    from argus.integrations.catalog import load_catalog
    from argus.rendering.collectors import render_agent

    catalog = load_catalog(REPO / "integrations")
    agent = render_agent(_profile(class_name), _ctx("solo", class_name), catalog=catalog)
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
