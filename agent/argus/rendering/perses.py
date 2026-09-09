"""Render Perses resources: project, tenant-header datasources, dashboards-as-code.

Shapes verified hands-on in the capability spike (docs/spikes/perses/RESULTS.md).
Panel names are prefixed by signal: metric:* / logs:* / traces:* - correlated panels
co-locate on one dashboard (the spike-proven mitigation while exemplar click-through
is unverified).

Naming (first-real-deploy lessons): the project is the cluster; app dashboards are
"{app}-{name}" so two agents never overwrite each other's dashboard; cluster-scope
dashboards (Dashboard.scope == "cluster") render once, app-less, with cluster-wide
queries. Display names are for humans ("Ledger · Agent Health"), metadata names for
machines.
"""

from argus.profiles.models import Dashboard, ResolvedProfile
from argus.rendering.context import Context

_PANEL_PLUGINS = {
    "metric": ("TimeSeriesChart", "TimeSeriesQuery", "PrometheusTimeSeriesQuery", "up"),
    "logs": ("LogsTable", "LogQuery", "LokiLogQuery", '{k8s_namespace_name="%(namespace)s"}'),
    "traces": ("TraceTable", "TraceQuery", "TempoTraceQuery", '{resource.service.name="%s"}'),
}

_ERROR_FILTER = '|~ "(?i)(error|exception|traceback|fatal|panic)"'

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
    # kube-state-metrics keeps its own namespace/pod labels through the TargetAllocator
    # scrape ONLY because the KSM ServiceMonitor sets honorLabels (live-found: without
    # it the scrape renames them exported_*). Namespace-scoped: gojira gives every app
    # its own namespace.
    "pod-restarts": (
        'sum(increase(kube_pod_container_status_restarts_total{namespace="%(namespace)s"}[1h]))'
    ),
}

# Cluster-scope variants, used when a dashboard renders app-less (scope: cluster).
# node-exporter's node_* names are the baseline dashboard source (see the
# hostmetrics-duplicates drop rule).
_CLUSTER_METRIC_QUERIES = {
    "node-cpu": '100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])))',
    "node-memory": (
        "100 * (1 - sum by (instance) (node_memory_MemAvailable_bytes) "
        "/ sum by (instance) (node_memory_MemTotal_bytes))"
    ),
    "pod-restarts": (
        "sum by (namespace) (increase(kube_pod_container_status_restarts_total[1h])) > 0"
    ),
}

_CLUSTER_LOG_QUERIES = {
    "cluster-errors": '{k8s_namespace_name=~".+"} ' + _ERROR_FILTER,
}

_LOG_QUERIES = {
    "errors": '{k8s_namespace_name="%(namespace)s"} ' + _ERROR_FILTER,
}


def _panel_title(panel_name: str) -> str:
    prefix, _, metric = panel_name.partition(":")
    title = metric.replace("-", " ").title().replace("P99", "p99").replace("Cpu", "CPU")
    if prefix == "logs":
        return f"{title} (Logs)"
    if prefix == "traces":
        return f"{title} (Traces)"
    return title


def _project(name: str) -> dict:
    return {
        "kind": "Project",
        "metadata": {"name": name},
        "spec": {"display": {"name": f"Argus · {name}"}},
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
    cluster_scope = ctx.app is None
    if prefix == "metric":
        if cluster_scope and metric in _CLUSTER_METRIC_QUERIES:
            query = _CLUSTER_METRIC_QUERIES[metric]
        else:
            template = _METRIC_QUERIES.get(metric, 'up{job="%(app)s"}')
            query = template % {
                "app": ctx.app or ctx.cluster,
                "namespace": ctx.namespace or ctx.cluster,
            }
    elif prefix == "logs":
        if cluster_scope and metric in _CLUSTER_LOG_QUERIES:
            query = _CLUSTER_LOG_QUERIES[metric]
        else:
            template = _LOG_QUERIES.get(metric, default_query)
            query = template % {"namespace": ctx.namespace or ctx.cluster}
    elif prefix == "traces" and metric == "slow-requests":
        # Named panel gets a query that actually matches its name (same principle as
        # _METRIC_QUERIES: a panel's title is a promise about what it shows).
        query = _slow_requests_query(ctx.app or ctx.cluster, p)
    else:
        query = default_query % (ctx.app or ctx.cluster)
    return {
        "kind": "Panel",
        "spec": {
            "display": {"name": _panel_title(panel_name)},
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
    pretty = dash.name.removeprefix("cluster-").replace("-", " ").title()
    if ctx.app:
        name = f"{ctx.app}-{dash.name}"
        display = f"{ctx.app.capitalize()} · {pretty}"
    else:
        name = dash.name
        display = f"Cluster · {pretty}"
    return {
        "kind": "Dashboard",
        "metadata": {"name": name, "project": project},
        "spec": {
            "display": {"name": display},
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
    # Cluster-scope dashboards render once, app-less (queries go cluster-wide); app
    # dashboards render per app and carry its name so two apps never collide.
    cluster_ctx = ctx.model_copy(update={"app": None, "namespace": None})
    for d in p.dashboards:
        if not d.enabled:
            continue
        docs.append(_dashboard(d, project, cluster_ctx if d.scope == "cluster" else ctx, p))
    return docs
