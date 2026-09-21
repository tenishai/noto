"""Sloth renderer - tool-neutral SLO block in, PrometheusServiceLevel out."""

from pathlib import Path

from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.resolve import resolve
from argus.rendering.context import Context
from argus.rendering.slo import render_sloth

FIXTURES = Path(__file__).parents[1] / "fixtures" / "profiles"


def _profile(**override):
    return resolve(
        load_baseline(FIXTURES / "baseline.yaml"),
        load_class(FIXTURES / "classes" / "web.yaml"),
        load_override(override or None),
    )


def _ctx():
    return Context(topology="solo", cluster="c1", environment="dev", app="clips", namespace="clips")


def test_none_when_no_slos() -> None:
    p = _profile(
        slos=[
            {
                "name": "availability-99.9",
                "objective": 0,
                "signal": "availability",
                "enabled": False,
            }
        ]
    )
    assert render_sloth(p, _ctx()) is None


def test_availability_slo_query() -> None:
    doc = render_sloth(_profile(), _ctx())
    assert doc is not None
    assert doc["apiVersion"] == "sloth.slok.dev/v1"
    assert doc["kind"] == "PrometheusServiceLevel"
    assert doc["spec"]["service"] == "clips"
    slo = doc["spec"]["slos"][0]
    assert slo["objective"] == 99.9
    assert 'status=~"5.."' in slo["sli"]["events"]["errorQuery"]
    assert "ArgusSLO" in slo["alerting"]["name"]


def test_latency_slo_uses_le_bucket() -> None:
    p = _profile(
        slos=[
            {
                "name": "latency-p99-500ms",
                "objective": 99.0,
                "signal": "latency",
                "latency_threshold": "500ms",
            }
        ]
    )
    doc = render_sloth(p, _ctx())
    assert doc is not None
    names = [s["name"] for s in doc["spec"]["slos"]]
    latency = next(s for s in doc["spec"]["slos"] if s["name"] == "latency-p99-500ms")
    assert 'le="0.5"' in latency["sli"]["events"]["errorQuery"]
    assert "availability-99.9" in names  # class SLO still present


def test_every_slo_carries_owner_labels_sloth_copies_onto_its_alerts() -> None:
    """Sloth copies each SLO's own `labels` onto the alerts it generates, so this is the
    only place an SLO-driven page/ticket alert can pick up who owns it."""
    p = _profile()
    doc = render_sloth(p, _ctx())
    assert doc is not None
    for slo in doc["spec"]["slos"]:
        assert slo["labels"]["app"] == "clips"
        assert slo["labels"]["namespace"] == "clips"
        assert slo["labels"]["class"] == p.class_name
