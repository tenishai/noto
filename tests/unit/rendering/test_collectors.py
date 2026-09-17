"""Collector CRD renderers - role behavior. Deep pipeline invariants live in contract tests."""

from pathlib import Path

import pytest
import yaml
from tests.support.otel import config_of, iter_pipelines

from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.resolve import resolve
from argus.rendering.collectors import (
    render_agent,
    render_gateway,
    render_ingest,
    render_targetallocator,
    render_targetallocator_rbac,
)
from argus.rendering.context import Context

FIXTURES = Path(__file__).parents[1] / "fixtures" / "profiles"


def _profile(**override):
    return resolve(
        load_baseline(FIXTURES / "baseline.yaml"),
        load_class(FIXTURES / "classes" / "web.yaml"),
        load_override(override or None),
    )


def _solo():
    return Context(topology="solo", cluster="c1", environment="dev", app="clips", namespace="clips")


def _regional():
    return Context(
        topology="regional",
        cluster="c1",
        environment="prod",
        region="us-east4",
        app="clips",
        namespace="clips",
        endpoints={
            "mimir_otlp_http": "http://mimir.r/otlp",
            "loki_otlp_http": "http://loki.r/otlp",
            "tempo_otlp_grpc": "tempo.r:4317",
            "kafka_bootstrap": "redpanda.r:9092",
        },
    )


def _solo_in(platform_namespace: str):
    return Context(
        topology="solo",
        cluster="c1",
        environment="dev",
        app="clips",
        namespace="clips",
        platform_namespace=platform_namespace,
    )


def _regional_in(platform_namespace: str):
    return Context(
        topology="regional",
        cluster="c1",
        environment="prod",
        region="us-east4",
        app="clips",
        namespace="clips",
        platform_namespace=platform_namespace,
        endpoints={
            "mimir_otlp_http": "http://mimir.r/otlp",
            "loki_otlp_http": "http://loki.r/otlp",
            "tempo_otlp_grpc": "tempo.r:4317",
            "kafka_bootstrap": "redpanda.r:9092",
        },
    )


def _all_rendered_docs(ctx: Context) -> list[dict]:
    """Every document the collector tier renders for a given context."""
    docs = [
        render_agent(_profile(), ctx),
        render_gateway(_profile(), ctx),
        render_targetallocator(_profile(), ctx),
        *render_targetallocator_rbac(ctx),
    ]
    if ctx.topology == "regional":
        docs.append(render_ingest(_profile(), ctx))
    return docs


def test_collector_metadata_namespace_follows_platform_namespace() -> None:
    ctx = _solo_in("obs")
    for role_render in (render_agent, render_gateway, render_targetallocator):
        crd = role_render(_profile(), ctx)
        assert crd["metadata"]["namespace"] == "obs"


def test_targetallocator_rbac_namespace_follows_platform_namespace() -> None:
    docs = render_targetallocator_rbac(_solo_in("obs"))
    namespaced = [d for d in docs if "namespace" in d.get("metadata", {})]
    assert namespaced, "expected at least one namespaced object (the ServiceAccount)"
    for doc in namespaced:
        assert doc["metadata"]["namespace"] == "obs"

    cluster_role_bindings = [d for d in docs if d["kind"] == "ClusterRoleBinding"]
    assert cluster_role_bindings, "expected at least one ClusterRoleBinding"
    for crb in cluster_role_bindings:
        for subject in crb["subjects"]:
            assert subject["namespace"] == "obs"


def test_targetallocator_rbac_cluster_scoped_names_stay_argus_prefixed() -> None:
    """Two platforms in one cluster is not a supported topology: cluster-scoped object
    NAMES (ClusterRole/ClusterRoleBinding) stay `argus-...` even when the platform's own
    namespace is renamed, so renaming the namespace never touches the golden names."""
    docs = render_targetallocator_rbac(_solo_in("obs"))
    for doc in docs:
        if doc["kind"] in {"ClusterRole", "ClusterRoleBinding"}:
            assert doc["metadata"]["name"].startswith("argus-") or doc["metadata"]["name"] == (
                "argus-targetallocator"
            )


def test_no_rendered_collector_document_mentions_the_argus_namespace_when_renamed() -> None:
    ctx = _solo_in("obs")
    for doc in _all_rendered_docs(ctx):
        dumped = yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
        assert "namespace: argus" not in dumped, dumped


def test_agent_filelog_follows_signals() -> None:
    with_logs = render_agent(_profile(), _solo())
    assert "filelog" in config_of(with_logs)["receivers"]
    metrics_only = _profile().model_copy(update={"signals": ["metrics"]})
    crd = render_agent(metrics_only, _solo())
    assert "filelog" not in config_of(crd)["receivers"]
    assert "logs" not in dict(iter_pipelines(crd))


def test_statsd_defined_but_never_wired() -> None:
    crd = render_agent(_profile(), _solo())
    assert "statsd" in config_of(crd)["receivers"]
    for _, pipeline in iter_pipelines(crd):
        assert "statsd" not in pipeline["receivers"]


def test_gateway_solo_exporters() -> None:
    crd = render_gateway(_profile(), _solo())
    exporters = config_of(crd)["exporters"]
    mimir = exporters["otlphttp/mimir"]
    assert mimir["compression"] == "zstd"
    assert mimir["headers"]["X-Scope-OrgID"] == "c1"
    assert mimir["sending_queue"]["storage"] == "file_storage"
    assert "file_storage" in config_of(crd)["extensions"]


def test_gateway_regional_exports_only_kafka() -> None:
    crd = render_gateway(_profile(), _regional())
    exporter_names = set(config_of(crd)["exporters"])
    assert exporter_names == {"kafka"}
    assert "file_storage" not in config_of(crd)["extensions"]


def test_ingest_raises_on_solo() -> None:
    with pytest.raises(ValueError, match="regional"):
        render_ingest(_profile(), _solo())


def test_ingest_regional_routes_to_backends() -> None:
    crd = render_ingest(_profile(), _regional())
    exporters = config_of(crd)["exporters"]
    assert exporters["otlphttp/mimir"]["endpoint"] == "http://mimir.r/otlp"
    assert exporters["otlphttp/mimir"]["headers"]["X-Scope-OrgID"] == "c1"
    assert "kafka" in config_of(crd)["receivers"]


def test_targetallocator_enabled() -> None:
    crd = render_targetallocator(_profile(), _solo())
    assert crd["spec"]["mode"] == "statefulset"
    assert crd["spec"]["targetAllocator"]["enabled"] is True
