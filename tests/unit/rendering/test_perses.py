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


# ── Row dashboards (the agent class) ─────────────────────────────────────────

REPO_PROFILES = Path(__file__).parents[3] / "profiles"


def _agent_dashboard(links: dict[str, str] | None = None) -> dict:
    p = resolve(
        load_baseline(REPO_PROFILES / "baseline.yaml"),
        load_class(REPO_PROFILES / "classes" / "agent.yaml"),
        load_override({"links": links} if links else None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="dev", app="ward", namespace="ward")
    (dash,) = [
        d
        for d in render_perses(p, ctx)
        if d["kind"] == "Dashboard" and d["metadata"]["name"] == "ward-agent-health"
    ]
    return dash


def test_agent_dashboard_is_titled_collapsible_rows_with_a_pod_variable() -> None:
    spec = _agent_dashboard()["spec"]
    titles = [layout["spec"]["display"]["title"] for layout in spec["layouts"]]
    assert titles == ["Is it alive", "Resources", "Logs", "About"]
    assert all(
        layout["spec"]["display"]["collapse"] == {"open": True} for layout in spec["layouts"]
    )
    (variable,) = spec["variables"]
    assert variable["spec"]["name"] == "pod"
    assert variable["spec"]["plugin"]["kind"] == "PrometheusLabelValuesVariable"
    assert spec["duration"] == "24h" and spec["refreshInterval"] == "1m"


def test_agent_panels_use_honest_kinds_and_real_queries() -> None:
    panels = _agent_dashboard()["spec"]["panels"]
    kinds = {key: panel["spec"]["plugin"]["kind"] for key, panel in panels.items()}
    assert kinds["metric-ready-replicas"] == "StatChart"
    assert kinds["metric-memory-of-limit"] == "GaugeChart"
    assert kinds["metric-image"] == "Table"
    assert kinds["metric-readiness-history"] == "StatusHistoryChart"
    assert kinds["markdown-about"] == "Markdown"
    queries = [
        q["spec"]["plugin"]["spec"]["query"]
        for panel in panels.values()
        for q in panel["spec"].get("queries", [])
    ]
    assert not any(q.startswith("up{") for q in queries), "a placeholder query shipped"
    # The pod-level readiness series is dropped at the gateway (baseline ksm-churn);
    # a panel built on it would be empty forever.
    assert not any("kube_pod_status_ready{" in q for q in queries)
    assert any('k8s_pod_name=~"$pod"' in q for q in queries), "resource panels narrow by $pod"


def test_cpu_panel_draws_usage_beside_request_and_limit() -> None:
    cpu = _agent_dashboard()["spec"]["panels"]["metric-cpu"]["spec"]
    names = [q["spec"]["plugin"]["spec"].get("seriesNameFormat") for q in cpu["queries"]]
    assert names == ["{{k8s_pod_name}}", "request", "limit"]
    assert {s["queryIndex"] for s in cpu["plugin"]["spec"]["querySettings"]} == {1, 2}


def test_row_panels_pack_by_size_and_wrap_at_the_grid_edge() -> None:
    alive = _agent_dashboard()["spec"]["layouts"][0]["spec"]["items"]
    assert [(i["x"], i["y"], i["width"]) for i in alive[:4]] == [
        (0, 0, 6),
        (6, 0, 6),
        (12, 0, 6),
        (18, 0, 6),
    ]
    assert (alive[4]["x"], alive[4]["y"]) == (0, 4), "the fifth panel wraps under the stats"


def test_links_from_the_override_reach_the_dashboard_and_the_about_panel() -> None:
    links = {"room": "https://room.example/#/agents/ward", "repository": "https://git.example/ward"}
    spec = _agent_dashboard(links)["spec"]
    assert [link["name"] for link in spec["links"]] == ["repository", "room"]
    assert all(link["targetBlank"] for link in spec["links"])
    about = spec["panels"]["markdown-about"]["spec"]["plugin"]["spec"]["text"]
    assert "[room](https://room.example/#/agents/ward)" in about
    assert "| Namespace | ward |" in about
    assert "links" not in _agent_dashboard()["spec"], "no links declared, none rendered"


def test_flat_dashboards_keep_the_untitled_two_column_grid() -> None:
    p = resolve(
        load_baseline(REPO_PROFILES / "baseline.yaml"),
        load_class(REPO_PROFILES / "classes" / "web.yaml"),
        load_override(None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="dev", app="shop", namespace="shop")
    (web,) = [d for d in render_perses(p, ctx) if d["metadata"]["name"] == "shop-web-red"]
    (layout,) = web["spec"]["layouts"]
    assert "display" not in layout["spec"] and "variables" not in web["spec"]
    assert [(i["x"], i["y"]) for i in layout["spec"]["items"][:3]] == [(0, 0), (12, 0), (0, 8)]


def test_error_log_panel_matches_levels_not_prose() -> None:
    errors = _agent_dashboard()["spec"]["panels"]["logs-errors"]["spec"]
    query = errors["queries"][0]["spec"]["plugin"]["spec"]["query"]
    assert "(?i)error" not in query and "level=(error|fatal)" in query
    assert "Prose that merely mentions an error can still match" in errors["display"]["description"]
