"""ServiceMonitor/PodMonitor renderers - the portable GMP replacements."""

from pathlib import Path

import pytest

from noto.profiles.loader import load_baseline, load_class, load_override
from noto.profiles.resolve import resolve
from noto.rendering.context import Context
from noto.rendering.scrape import render_podmonitor, render_servicemonitor

FIXTURES = Path(__file__).parents[1] / "fixtures" / "profiles"


def _profile():
    return resolve(
        load_baseline(FIXTURES / "baseline.yaml"),
        load_class(FIXTURES / "classes" / "web.yaml"),
        load_override(None),
    )


def _ctx(**kw):
    defaults = dict(
        topology="solo", cluster="c1", environment="dev", app="clips", namespace="clips"
    )
    defaults.update(kw)
    return Context(**defaults)


def test_servicemonitor_shape() -> None:
    sm = render_servicemonitor(_profile(), _ctx())
    assert sm["apiVersion"] == "monitoring.coreos.com/v1"
    assert sm["kind"] == "ServiceMonitor"
    assert sm["metadata"]["namespace"] == "clips"
    assert sm["metadata"]["labels"]["noto.dev/class"] == "web"
    endpoint = sm["spec"]["endpoints"][0]
    assert endpoint == {"port": "http", "path": "/metrics", "interval": "15s"}
    assert sm["spec"]["selector"]["matchLabels"] == {"app.kubernetes.io/name": "clips"}


def test_podmonitor_shape() -> None:
    pm = render_podmonitor(_profile(), _ctx())
    assert pm["kind"] == "PodMonitor"
    assert pm["spec"]["podMetricsEndpoints"][0]["port"] == "http"


def test_requires_app_and_namespace() -> None:
    with pytest.raises(ValueError, match="app"):
        render_servicemonitor(_profile(), _ctx(app=None))
    with pytest.raises(ValueError, match="namespace"):
        render_podmonitor(_profile(), _ctx(namespace=None))
