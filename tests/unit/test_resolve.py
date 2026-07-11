"""Resolver merge semantics: scalars last-wins, named collections merge by name,
enabled: false filters, inputs never mutated."""

from pathlib import Path

from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.resolve import resolve

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


def _parts():
    return (
        load_baseline(FIXTURES / "baseline.yaml"),
        load_class(FIXTURES / "classes" / "web.yaml"),
    )


def test_baseline_plus_class_combines() -> None:
    baseline, cls = _parts()
    p = resolve(baseline, cls, load_override(None))
    assert p.class_name == "web"
    assert p.signals == ["metrics", "logs"]
    drop_names = [r.name for r in p.drop]
    assert "health-endpoints" in drop_names  # from baseline
    dash_names = [d.name for d in p.dashboards]
    assert dash_names == ["cluster-baseline", "web-red"]  # baseline first, then class


def test_same_name_in_later_layer_replaces() -> None:
    baseline, cls = _parts()
    override = load_override(
        {"drop": [{"name": "health-endpoints", "stage": "agent", "path_matches": ["/ping"]}]}
    )
    p = resolve(baseline, cls, override)
    rule = next(r for r in p.drop if r.name == "health-endpoints")
    assert rule.path_matches == ["/ping"]


def test_enabled_false_removes_inherited_item() -> None:
    baseline, cls = _parts()
    override = load_override(
        {
            "slos": [
                {
                    "name": "availability-99.9",
                    "objective": 0,
                    "signal": "availability",
                    "enabled": False,
                }
            ]
        }
    )
    p = resolve(baseline, cls, override)
    assert [s.name for s in p.slos] == []


def test_override_traces_adds_signal() -> None:
    baseline, cls = _parts()
    p = resolve(baseline, cls, load_override({"traces": True}))
    assert "traces" in p.signals


def test_override_retention_replaces_only_that_key() -> None:
    baseline, cls = _parts()
    p = resolve(baseline, cls, load_override({"retention": {"metrics": "90d"}}))
    assert p.budgets.retention["metrics"] == "90d"
    assert p.budgets.retention["logs"] == "7d"


def test_cardinality_budget_replaces_every_env() -> None:
    baseline, cls = _parts()
    p = resolve(
        baseline,
        cls,
        load_override({"cardinality_budget": 1500000, "justification": "approved 2026-07-11"}),
    )
    assert set(p.budgets.cardinality.values()) == {1500000}


def test_inputs_not_mutated() -> None:
    baseline, cls = _parts()
    before = baseline.model_dump()
    resolve(baseline, cls, load_override({"retention": {"metrics": "90d"}}))
    assert baseline.model_dump() == before
