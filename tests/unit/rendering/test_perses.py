"""Perses renderers - the spike-verified shapes: project, tenant-header datasources, dashboards."""

from pathlib import Path

from noto.profiles.loader import load_baseline, load_class, load_override
from noto.profiles.resolve import resolve
from noto.rendering.context import Context
from noto.rendering.perses import render_perses

FIXTURES = Path(__file__).parents[1] / "fixtures" / "profiles"


def _docs():
    p = resolve(
        load_baseline(FIXTURES / "baseline.yaml"),
        load_class(FIXTURES / "classes" / "web.yaml"),
        load_override(None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="dev", app="clips", namespace="clips")
    return render_perses(p, ctx)


def test_project_first_then_datasources() -> None:
    docs = _docs()
    assert docs[0]["kind"] == "Project"
    ds = [d for d in docs if d["kind"] == "Datasource"]
    assert len(ds) == 3


def test_datasources_carry_tenant_header() -> None:
    for ds in (d for d in _docs() if d["kind"] == "Datasource"):
        proxy = ds["spec"]["plugin"]["spec"]["proxy"]["spec"]
        assert proxy["headers"]["X-Scope-OrgID"] == "c1"


def test_panel_plugin_mapping() -> None:
    dashboards = [d for d in _docs() if d["kind"] == "Dashboard"]
    assert dashboards, "expected at least one dashboard"
    panels = dashboards[-1]["spec"]["panels"]  # web-red from the class fixture
    kinds = {p["spec"]["plugin"]["kind"] for p in panels.values()}
    assert "TimeSeriesChart" in kinds  # metric:request-rate maps to TimeSeriesChart
