"""Context: topology-validated identity + endpoints for renderers."""

import pytest
from pydantic import ValidationError

from argus.rendering.context import SOLO_DEFAULTS, Context, solo_endpoints


def test_tenant_defaults_to_cluster() -> None:
    ctx = Context(topology="solo", cluster="dev-us-east4", environment="dev")
    assert ctx.tenant == "dev-us-east4"


def test_solo_endpoint_defaults() -> None:
    ctx = Context(topology="solo", cluster="c1", environment="dev")
    assert ctx.endpoints.mimir_otlp_http == "http://argus-mimir-gateway.argus.svc:80/otlp"
    assert ctx.endpoints.loki_otlp_http == "http://argus-loki.argus.svc:3100/otlp"
    assert ctx.endpoints.tempo_otlp_grpc == "argus-tempo.argus.svc:4317"
    assert ctx.endpoints.kafka_bootstrap is None


def test_solo_endpoints_for_argus_matches_module_constant() -> None:
    """SOLO_DEFAULTS is `solo_endpoints("argus")` - the back-compat identity every
    golden file relies on."""
    assert solo_endpoints("argus") == SOLO_DEFAULTS


def test_solo_endpoints_default_values_are_todays_constants() -> None:
    assert solo_endpoints("argus") == {
        "mimir_otlp_http": "http://argus-mimir-gateway.argus.svc:80/otlp",
        "loki_otlp_http": "http://argus-loki.argus.svc:3100/otlp",
        "tempo_otlp_grpc": "argus-tempo.argus.svc:4317",
        "mimir_query_url": "http://argus-mimir-gateway.argus.svc:80/prometheus",
        "perses_url": "http://argus-perses.argus.svc:8080",
    }


def test_solo_endpoints_custom_namespace_changes_every_host() -> None:
    default = solo_endpoints("argus")
    custom = solo_endpoints("obs")
    assert set(custom) == set(default)
    for key, value in default.items():
        assert custom[key] != value
        assert ".obs.svc" in custom[key] or "argus-tempo.obs.svc" in custom[key]


def test_solo_endpoints_custom_namespace_exact_values() -> None:
    assert solo_endpoints("obs") == {
        "mimir_otlp_http": "http://argus-mimir-gateway.obs.svc:80/otlp",
        "loki_otlp_http": "http://argus-loki.obs.svc:3100/otlp",
        "tempo_otlp_grpc": "argus-tempo.obs.svc:4317",
        "mimir_query_url": "http://argus-mimir-gateway.obs.svc:80/prometheus",
        "perses_url": "http://argus-perses.obs.svc:8080",
    }


def test_context_default_platform_namespace_is_byte_identical_to_today() -> None:
    """No `platform_namespace` given: a solo Context must derive exactly the
    pre-existing SOLO_DEFAULTS hosts - the golden-file invariant."""
    ctx = Context(topology="solo", cluster="c1", environment="dev")
    assert ctx.endpoints.mimir_otlp_http == SOLO_DEFAULTS["mimir_otlp_http"]
    assert ctx.endpoints.loki_otlp_http == SOLO_DEFAULTS["loki_otlp_http"]
    assert ctx.endpoints.tempo_otlp_grpc == SOLO_DEFAULTS["tempo_otlp_grpc"]


def test_context_custom_platform_namespace_changes_every_endpoint_host() -> None:
    ctx = Context(topology="solo", cluster="c1", environment="dev", platform_namespace="obs")
    assert ctx.endpoints.mimir_otlp_http == "http://argus-mimir-gateway.obs.svc:80/otlp"
    assert ctx.endpoints.loki_otlp_http == "http://argus-loki.obs.svc:3100/otlp"
    assert ctx.endpoints.tempo_otlp_grpc == "argus-tempo.obs.svc:4317"


def test_context_explicit_endpoints_win_over_platform_namespace() -> None:
    """An explicit `endpoints=` always wins - platform_namespace only feeds the
    default when nothing is supplied."""
    ctx = Context(
        topology="solo",
        cluster="c1",
        environment="dev",
        platform_namespace="obs",
        endpoints={
            "mimir_otlp_http": "http://explicit/otlp",
            "loki_otlp_http": "http://explicit/otlp",
            "tempo_otlp_grpc": "explicit:4317",
        },
    )
    assert ctx.endpoints.mimir_otlp_http == "http://explicit/otlp"


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
