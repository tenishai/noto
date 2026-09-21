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


def test_no_scrape_class_ships_no_scrape_down_group() -> None:
    REAL = Path(__file__).parents[3] / "profiles"
    p = resolve(
        load_baseline(REAL / "baseline.yaml"),
        load_class(REAL / "classes" / "agent.yaml"),
        load_override(None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="dev", app="hello", namespace="hello")
    doc = render_alert_rules(p, ctx)
    names = [g["name"] for g in doc["spec"]["groups"]]
    assert names == ["argus-collector-health", "argus-cardinality"]


def test_every_alert_rule_says_whose_it_is() -> None:
    """Every rule with an `alert` key (collector health, cardinality, scrape-down) must
    carry who owns it, beside the severity it already had, so it can be routed later."""
    p = resolve(
        load_baseline(FIXTURES / "baseline.yaml"),
        load_class(FIXTURES / "classes" / "web.yaml"),
        load_override(None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="dev", app="clips", namespace="clips")
    doc = render_alert_rules(p, ctx)
    seen_alert = False
    for group in doc["spec"]["groups"]:
        for rule in group["rules"]:
            if "alert" not in rule:
                continue
            seen_alert = True
            assert rule["labels"]["app"] == "clips"
            assert rule["labels"]["namespace"] == "clips"
            assert rule["labels"]["class"] == p.class_name
            assert rule["labels"]["environment"] == "dev"
            assert "severity" in rule["labels"]
    assert seen_alert


def test_owned_leaves_a_recording_rule_untouched() -> None:
    """A rule with no `alert` key (a recording rule) is not this task's concern: `_owned`
    must return it exactly as given, with no owner labels added."""
    from argus.rendering.alerts import _owned

    recording_rule = {"record": "job:up:avg", "expr": "avg(up)"}
    owner = {"app": "clips", "namespace": "clips", "class": "web", "environment": "dev"}
    assert _owned(recording_rule, owner) == recording_rule
    assert "labels" not in _owned(recording_rule, owner)


def test_owned_does_not_mutate_the_module_level_collector_health_rules() -> None:
    """`_COLLECTOR_HEALTH_RULES` is shared across every render call; `_owned` must build a
    new dict rather than writing labels into the module constant."""
    from argus.rendering.alerts import _COLLECTOR_HEALTH_RULES

    before = [dict(r) for r in _COLLECTOR_HEALTH_RULES]
    p = resolve(
        load_baseline(FIXTURES / "baseline.yaml"),
        load_class(FIXTURES / "classes" / "web.yaml"),
        load_override(None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="dev", app="clips", namespace="clips")
    render_alert_rules(p, ctx)
    assert before == _COLLECTOR_HEALTH_RULES
    for rule in _COLLECTOR_HEALTH_RULES:
        assert set(rule["labels"]) == {"severity"}


def test_a_rules_own_label_wins_over_the_owners() -> None:
    """`_owned`'s docstring promise: a rule that already sets one of the owner keys on
    purpose keeps its own value rather than being overwritten."""
    from argus.rendering.alerts import _owned

    rule = {"alert": "X", "labels": {"severity": "page", "app": "not-clips"}}
    owner = {"app": "clips", "namespace": "clips", "class": "web", "environment": "dev"}
    owned = _owned(rule, owner)
    assert owned["labels"]["app"] == "not-clips"
    assert owned["labels"]["namespace"] == "clips"
