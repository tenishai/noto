"""Context: topology-validated identity + endpoints for renderers."""

import pytest
from pydantic import ValidationError

from argus.rendering.context import Context


def test_tenant_defaults_to_cluster() -> None:
    ctx = Context(topology="solo", cluster="dev-us-east4", environment="dev")
    assert ctx.tenant == "dev-us-east4"


def test_solo_endpoint_defaults() -> None:
    ctx = Context(topology="solo", cluster="c1", environment="dev")
    assert ctx.endpoints.mimir_otlp_http == "http://argus-mimir-gateway.argus.svc:80/otlp"
    assert ctx.endpoints.loki_otlp_http == "http://argus-loki.argus.svc:3100/otlp"
    assert ctx.endpoints.tempo_otlp_grpc == "argus-tempo.argus.svc:4317"
    assert ctx.endpoints.kafka_bootstrap is None


def test_regional_requires_region_and_kafka() -> None:
    with pytest.raises(ValidationError, match="region"):
        Context(topology="regional", cluster="c1", environment="prod")
    with pytest.raises(ValidationError, match="kafka"):
        Context(
            topology="regional",
            cluster="c1",
            environment="prod",
            region="us-east4",
        )


def test_regional_valid() -> None:
    ctx = Context(
        topology="regional",
        cluster="c1",
        environment="prod",
        region="us-east4",
        endpoints={
            "mimir_otlp_http": "http://mimir.us-east4.argus.internal/otlp",
            "loki_otlp_http": "http://loki.us-east4.argus.internal/otlp",
            "tempo_otlp_grpc": "tempo.us-east4.argus.internal:4317",
            "kafka_bootstrap": "redpanda.us-east4.argus.internal:9092",
        },
    )
    assert ctx.region == "us-east4"
