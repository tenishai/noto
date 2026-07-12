"""Alert-pack renderer: collector health always; per-app scrape-down (the GMP replacement)."""

from pathlib import Path

from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.resolve import resolve
from argus.rendering.alerts import render_alert_rules
from argus.rendering.context import Context

FIXTURES = Path(__file__).parents[1] / "fixtures" / "profiles"


def _doc():
    p = resolve(
        load_baseline(FIXTURES / "baseline.yaml"),
        load_class(FIXTURES / "classes" / "web.yaml"),
        load_override(None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="dev", app="clips", namespace="clips")
    return render_alert_rules(p, ctx)


def test_groups() -> None:
    doc = _doc()
    assert doc["kind"] == "PrometheusRule"
    names = [g["name"] for g in doc["spec"]["groups"]]
    assert names == ["argus-collector-health", "argus-cardinality", "argus-app-clips"]


def test_scrape_down_expr() -> None:
    doc = _doc()
    app_group = doc["spec"]["groups"][2]
    scrape_down = next(r for r in app_group["rules"] if r["alert"] == "ArgusScrapeDown")
    assert scrape_down["expr"] == 'up{job="clips"} == 0'
    assert scrape_down["for"] == "5m"


def test_collector_health_alerts_present() -> None:
    doc = _doc()
    health = doc["spec"]["groups"][0]
    alerts = {r["alert"] for r in health["rules"]}
    assert {"ArgusCollectorMemoryHigh", "ArgusCollectorRefusing", "ArgusExporterFailing"} <= alerts
