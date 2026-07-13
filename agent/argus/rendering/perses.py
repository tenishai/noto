"""Render Perses resources: project, tenant-header datasources, dashboards-as-code.

Shapes verified hands-on in the capability spike (docs/spikes/perses/RESULTS.md).
Panel names are prefixed by signal: metric:* / logs:* / traces:* - correlated panels
co-locate on one dashboard (the spike-proven mitigation while exemplar click-through
is unverified).
"""

from argus.profiles.models import Dashboard, ResolvedProfile
from argus.rendering.context import Context

_PANEL_PLUGINS = {
    "metric": ("TimeSeriesChart", "TimeSeriesQuery", "PrometheusTimeSeriesQuery", "up"),
    "logs": ("LogsTable", "LogQuery", "LokiLogQuery", '{service_name="%s"}'),
    "traces": ("TraceTable", "TraceQuery", "TempoTraceQuery", '{resource.service.name="%s"}'),
}

# Default PromQL per known metric panel name; unknown names fall back to a placeholder
# that dashboards-as-code reviewers see immediately in the golden diff.
# Prometheus-client conventions - what gojira-contract apps (promhttp on :8080/metrics)
# actually expose; verified against a live app in the smoke run (semconv names were wrong).
_METRIC_QUERIES = {
    "request-rate": 'sum(rate(http_requests_total{job="%(app)s"}[5m]))',
    "error-rate": 'sum(rate(http_requests_total{job="%(app)s", status=~"5.."}[5m]))',
    "latency-p99": 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{job="%(app)s"}[5m])) by (le))',  # noqa: E501
    # kubeletstats via OTLP, not cAdvisor/node-exporter container_* names - Argus's Mimir
    # only has the OTel kubeletstats shape (live-verified; same selector as
    # feedback/mimir.py's app_selector/measurements()). Saturation view next to latency.
    "memory-working-set": (
        "sum by (k8s_pod_name) (k8s_pod_memory_working_set"
        '{k8s_namespace_name="%(namespace)s", k8s_pod_name=~"%(app)s-.*"})'
    ),
}


def _project(name: str) -> dict:
    return {
        "kind": "Project",
        "metadata": {"name": name},
        "spec": {"display": {"name": f"Argus - {name}"}},
    }


def _datasource(name: str, kind: str, url: str, project: str, tenant: str) -> dict:
    return {
        "kind": "Datasource",
        "metadata": {"name": name, "project": project},
        "spec": {
            "default": True,
            "plugin": {
                "kind": kind,
                "spec": {
                    "proxy": {
                        "kind": "HTTPProxy",
                        "spec": {"url": url, "headers": {"X-Scope-OrgID": tenant}},
                    }
                },
            },
        },
    }


def _slow_requests_query(service: str, p: ResolvedProfile) -> str:
    """TraceQL for traces:slow-requests - traces slower than the class's own latency SLO.

    Falls back to the spike-verified generic per-service query (docs/spikes/perses/RESULTS.md)
    when the class defines no latency SLO to borrow a threshold from, so the panel never
    goes empty for want of a number; it just stops being duration-filtered.
    """
    threshold = next(
        (s.latency_threshold for s in p.slos if s.signal == "latency" and s.latency_threshold),
        None,
    )
    if threshold is None:
        return f'{{resource.service.name="{service}"}}'
    return f'{{resource.service.name="{service}" && duration > {threshold}}}'


def _panel(panel_name: str, ctx: Context, p: ResolvedProfile) -> dict:
    prefix, _, metric = panel_name.partition(":")
    chart_kind, query_kind, plugin_kind, default_query = _PANEL_PLUGINS[prefix]
    if prefix == "metric":
        template = _METRIC_QUERIES.get(metric, 'up{job="%(app)s"}')
        query = template % {
            "app": ctx.app or ctx.cluster,
            "namespace": ctx.namespace or ctx.cluster,
        }
    elif prefix == "traces" and metric == "slow-requests":
        # Named panel gets a query that actually matches its name (same principle as
        # _METRIC_QUERIES: a panel's title is a promise about what it shows).
        query = _slow_requests_query(ctx.app or ctx.cluster, p)
    else:
        query = default_query % (ctx.app or ctx.cluster)
    return {
        "kind": "Panel",
        "spec": {
            "display": {"name": panel_name},
            "plugin": {"kind": chart_kind, "spec": {}},
            "queries": [
                {
                    "kind": query_kind,
                    "spec": {"plugin": {"kind": plugin_kind, "spec": {"query": query}}},
                }
            ],
        },
    }


def _dashboard(dash: Dashboard, project: str, ctx: Context, p: ResolvedProfile) -> dict:
    panels = {name.replace(":", "-"): _panel(name, ctx, p) for name in dash.panels}
    items = [
        {
            "x": (i % 2) * 12,
            "y": (i // 2) * 8,
            "width": 12,
            "height": 8,
            "content": {"$ref": f"#/spec/panels/{key}"},
        }
        for i, key in enumerate(panels)
    ]
    return {
        "kind": "Dashboard",
        "metadata": {"name": dash.name, "project": project},
        "spec": {
            "display": {"name": dash.name},
            "duration": "1h",
            "panels": panels,
            "layouts": [{"kind": "Grid", "spec": {"items": items}}],
        },
    }


def render_perses(p: ResolvedProfile, ctx: Context) -> list[dict]:
    assert ctx.endpoints is not None
    project = ctx.cluster
    docs = [_project(project)]
    # Perses query paths: Prometheus API base for Mimir; Loki/Tempo native APIs.
    mimir_query = ctx.endpoints.mimir_otlp_http.removesuffix("/otlp") + "/prometheus"
    loki_query = ctx.endpoints.loki_otlp_http.removesuffix("/otlp")
    tempo_query = "http://" + ctx.endpoints.tempo_otlp_grpc.rsplit(":", 1)[0] + ":3200"
    docs.append(_datasource("mimir", "PrometheusDatasource", mimir_query, project, ctx.tenant))
    docs.append(_datasource("loki", "LokiDatasource", loki_query, project, ctx.tenant))
    docs.append(_datasource("tempo", "TempoDatasource", tempo_query, project, ctx.tenant))
    docs.extend(_dashboard(d, project, ctx, p) for d in p.dashboards if d.enabled)
    return docs
