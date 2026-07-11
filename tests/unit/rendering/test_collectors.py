"""Collector CRD renderers - role behavior. Deep pipeline invariants live in contract tests."""

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


def test_agent_k8sattributes_passthrough() -> None:
    crd = render_agent(_profile(), _solo())
    assert config_of(crd)["processors"]["k8sattributes"]["passthrough"] is True


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


def test_every_pipeline_memory_limiter_first_batch_last() -> None:
    for crd in (
        render_agent(_profile(), _solo()),
        render_gateway(_profile(), _solo()),
        render_gateway(_profile(), _regional()),
        render_ingest(_profile(), _regional()),
        render_targetallocator(_profile(), _solo()),
    ):
        for name, pipeline in iter_pipelines(crd):
            procs = pipeline["processors"]
            assert procs[0] == "memory_limiter", (crd["metadata"]["name"], name)
            assert procs[-1] == "batch", (crd["metadata"]["name"], name)


def test_targetallocator_enabled() -> None:
    crd = render_targetallocator(_profile(), _solo())
    assert crd["spec"]["mode"] == "statefulset"
    assert crd["spec"]["targetAllocator"]["enabled"] is True
