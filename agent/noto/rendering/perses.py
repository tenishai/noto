"""Render Perses resources: project, tenant-header datasources, dashboards-as-code.

Shapes verified hands-on in the capability spike (docs/spikes/perses/RESULTS.md) and
against the Perses 0.53 plugin schemas (StatChart, GaugeChart, Table,
StatusHistoryChart, Markdown beside TimeSeriesChart, LogsTable and TraceTable).
Panel names are prefixed by signal: metric:* / logs:* / traces:* / markdown:* -
correlated panels co-locate on one dashboard (the spike-proven mitigation while
exemplar click-through is unverified).

A dashboard declares either a flat panel list (one untitled grid, two columns) or
titled rows (one collapsible grid per row, panels packed by their own sizes). An app
dashboard with rows also gets a `pod` variable, so a multi-pod app can be narrowed to
one pod without editing anything.

Naming (first-real-deploy lessons): the project is the cluster; app dashboards are
"{app}-{name}" so two agents never overwrite each other's dashboard; cluster-scope
dashboards (Dashboard.scope == "cluster") render once, app-less, with cluster-wide
queries. Display names are for humans ("Ledger · Agent Health"), metadata names for
machines.
"""

from noto.profiles.models import Dashboard, ResolvedProfile, Row
from noto.rendering.context import Context

# The one dashboard each class ships; anything beyond these (a per-app
# override extra) keeps its own name in the display.
_CLASS_PRIMARY_DASHBOARDS = frozenset(
    {"agent-health", "gateway-red", "web-red", "worker-throughput", "cron-health"}
)

_KIND_LABELS = {
    "web": "Web",
    "worker": "Worker",
    "cron": "Cron",
    "heavy": "Heavy",
    "gpu": "GPU",
    "agent": "Agent",
    "gateway": "Gateway",
}

# prefix -> (default chart kind, query kind, query plugin kind, default query).
# `markdown` panels carry no query at all.
_PANEL_PLUGINS = {
    "metric": ("TimeSeriesChart", "TimeSeriesQuery", "PrometheusTimeSeriesQuery", "up"),
    "logs": ("LogsTable", "LogQuery", "LokiLogQuery", '{k8s_namespace_name="%(namespace)s"}'),
    "traces": ("TraceTable", "TraceQuery", "TempoTraceQuery", '{resource.service.name="%s"}'),
    "markdown": ("Markdown", None, None, None),
}

# Lines that are errors, not lines that mention errors. An agent's own prose ("the
# build error was a missing import") carries the word in every other turn, so the
# old `(?i)error` match filled this panel with conversation. Anchors: a structured
# level field (logfmt or JSON), an upper-case level token, a Python traceback, a Go
# panic. Prose that happens to shout ERROR still matches: Loki has no level field
# until the log line carries one, and this is the honest ceiling of a regex.
_ERROR_FILTER = (
    '|~ "level=(error|fatal)|\\"level\\":\\\\s*\\"(error|fatal)\\"'
    '|\\\\bERROR\\\\b|\\\\bFATAL\\\\b|Traceback \\\\(most recent call last\\\\)|panic:"'
)

# Selectors. kubeletstats via OTLP keeps the OTel resource names (k8s_namespace_name,
# k8s_pod_name; names WITHOUT unit/_total suffixes, live-verified in feedback/mimir.py);
# kube-state-metrics keeps its own namespace/pod labels through the TargetAllocator
# scrape ONLY because the KSM ServiceMonitor sets honorLabels (live-found: without it
# the scrape renames them exported_*). Namespace-scoped: gojira gives every app its
# own namespace. `%(pod)s` is the pod matcher: the `$pod` variable on a dashboard that
# has one, `<app>-.*` on one that does not.
_KUBELET = 'k8s_namespace_name="%(namespace)s", k8s_pod_name=~"%(pod)s"'
_KSM = 'namespace="%(namespace)s", pod=~"%(pod)s"'

# Default PromQL per known metric panel name; unknown names fall back to a placeholder
# that dashboards-as-code reviewers see immediately in the golden diff. A tuple of
# (series name format, query) pairs renders one panel with several queries (usage
# beside its request and limit); a plain string is one query.
# Prometheus-client conventions - what gojira-contract apps (promhttp on :8080/metrics)
# actually expose; verified against a live app in the smoke run (semconv names were wrong).
_METRIC_QUERIES: dict[str, str | tuple[tuple[str, str], ...]] = {
    "request-rate": 'sum(rate(http_requests_total{job="%(app)s"}[5m]))',
    "error-rate": 'sum(rate(http_requests_total{job="%(app)s", status=~"5.."}[5m]))',
    "latency-p99": 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{job="%(app)s"}[5m])) by (le))',  # noqa: E501
    # Saturation view next to latency (same selector as feedback/mimir.py's
    # app_selector/measurements()).
    "memory-working-set": f"sum by (k8s_pod_name) (k8s_pod_memory_working_set{{{_KUBELET}}})",
    "pod-restarts": (
        'sum(increase(kube_pod_container_status_restarts_total{namespace="%(namespace)s"}[1h]))'
    ),
    # ── Is it alive ──────────────────────────────────────────────────────
    "ready-replicas": ('sum(kube_deployment_status_replicas_available{namespace="%(namespace)s"})'),
    "restarts-24h": (
        'sum(increase(kube_pod_container_status_restarts_total{namespace="%(namespace)s"}[24h]))'
    ),
    "up-for": f"time() - max(kube_pod_start_time{{{_KSM}}})",
    "memory-of-limit": (
        f"100 * sum(k8s_pod_memory_working_set{{{_KUBELET}}}) "
        f'/ sum(kube_pod_container_resource_limits{{{_KSM}, resource="memory"}})'
    ),
    "image": f"kube_pod_container_info{{{_KSM}}}",
    # kube_pod_status_ready is dropped at the gateway (baseline drop rule ksm-churn);
    # the per-container readiness series is kept, and a pod is ready when its
    # containers are.
    "readiness-history": f"min by (pod) (kube_pod_container_status_ready{{{_KSM}}})",
    # ── Resources ────────────────────────────────────────────────────────
    "cpu": (
        ("{{k8s_pod_name}}", f"sum by (k8s_pod_name) (rate(k8s_pod_cpu_time{{{_KUBELET}}}[5m]))"),
        ("request", f'sum(kube_pod_container_resource_requests{{{_KSM}, resource="cpu"}})'),
        ("limit", f'sum(kube_pod_container_resource_limits{{{_KSM}, resource="cpu"}})'),
    ),
    "memory": (
        ("{{k8s_pod_name}}", f"sum by (k8s_pod_name) (k8s_pod_memory_working_set{{{_KUBELET}}})"),
        ("limit", f'sum(kube_pod_container_resource_limits{{{_KSM}, resource="memory"}})'),
    ),
    "network": f"sum by (direction) (rate(k8s_pod_network_io{{{_KUBELET}}}[5m]))",
    "pod-filesystem": f"sum by (k8s_pod_name) (k8s_pod_filesystem_usage{{{_KUBELET}}})",
    "oom-kills": (
        f'sum(kube_pod_container_status_last_terminated_reason{{{_KSM}, reason="OOMKilled"}})'
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
    # Unfiltered, beside the error panel: an empty error panel on a healthy app
    # is indistinguishable from a broken log pipeline - this panel is the
    # pipeline's visible heartbeat (first-operator-hour lesson).
    "recent": '{k8s_namespace_name="%(namespace)s"}',
}

_GREEN, _AMBER, _RED = "#2e7d32", "#f9a825", "#c62828"
_PERCENT_STEPS = {"steps": [{"value": 70, "color": _AMBER}, {"value": 90, "color": _RED}]}

# Chart kind and plugin spec per slug, where the default (TimeSeriesChart, empty spec)
# is not the honest shape. Field names are the Perses 0.53 plugin schemas' own.
_PANEL_SPECS: dict[str, tuple[str, dict]] = {
    "ready-replicas": (
        "StatChart",
        {
            "calculation": "last-number",
            "format": {"unit": "decimal", "decimalPlaces": 0},
            "thresholds": {"defaultColor": _RED, "steps": [{"value": 1, "color": _GREEN}]},
        },
    ),
    "restarts-24h": (
        "StatChart",
        {
            "calculation": "last-number",
            "format": {"unit": "decimal", "decimalPlaces": 0},
            "thresholds": {
                "defaultColor": _GREEN,
                "steps": [{"value": 1, "color": _AMBER}, {"value": 3, "color": _RED}],
            },
        },
    ),
    "up-for": (
        "StatChart",
        {"calculation": "last-number", "format": {"unit": "seconds", "decimalPlaces": 1}},
    ),
    "memory-of-limit": (
        "GaugeChart",
        {
            "calculation": "last-number",
            "format": {"unit": "percent", "decimalPlaces": 0},
            "thresholds": _PERCENT_STEPS,
            "max": 100,
        },
    ),
    "image": (
        "Table",
        {
            "density": "compact",
            "defaultColumnHidden": True,
            "columnSettings": [
                {"name": "pod", "header": "Pod", "hide": False},
                {"name": "container", "header": "Container", "hide": False},
                {"name": "image", "header": "Image", "hide": False},
            ],
        },
    ),
    "readiness-history": (
        "StatusHistoryChart",
        {
            "mappings": [
                {
                    "kind": "Value",
                    "spec": {"value": "1", "result": {"value": "Ready", "color": _GREEN}},
                },
                {
                    "kind": "Value",
                    "spec": {"value": "0", "result": {"value": "Not ready", "color": _RED}},
                },
            ],
        },
    ),
    "cpu": (
        "TimeSeriesChart",
        {
            "yAxis": {"format": {"unit": "decimal", "decimalPlaces": 2}, "label": "cores"},
            "legend": {"position": "bottom", "mode": "list"},
            "querySettings": [
                {
                    "queryIndex": 1,
                    "colorMode": "fixed",
                    "colorValue": _AMBER,
                    "lineStyle": "dashed",
                },
                {"queryIndex": 2, "colorMode": "fixed", "colorValue": _RED, "lineStyle": "dashed"},
            ],
        },
    ),
    "memory": (
        "TimeSeriesChart",
        {
            "yAxis": {"format": {"unit": "bytes"}},
            "legend": {"position": "bottom", "mode": "list"},
            "querySettings": [
                {"queryIndex": 1, "colorMode": "fixed", "colorValue": _RED, "lineStyle": "dashed"},
            ],
        },
    ),
    "network": (
        "TimeSeriesChart",
        {
            "yAxis": {"format": {"unit": "bytes/sec"}},
            "legend": {"position": "bottom", "mode": "list"},
        },
    ),
    "pod-filesystem": ("TimeSeriesChart", {"yAxis": {"format": {"unit": "bytes"}}}),
    "oom-kills": (
        "StatChart",
        {
            "calculation": "last-number",
            "format": {"unit": "decimal", "decimalPlaces": 0},
            "thresholds": {"defaultColor": _GREEN, "steps": [{"value": 1, "color": _RED}]},
        },
    ),
}

# What a panel shows and what it cannot: the description Perses puts behind the
# panel's info mark. Say the limit out loud rather than let an empty chart imply health.
_PANEL_DESCRIPTIONS = {
    "ready-replicas": "Replicas the deployment reports available right now. 0 is down.",
    "restarts-24h": "Container restarts across the namespace in the last 24 hours.",
    "up-for": "Time since the newest pod started. A reset here is a roll or a crash.",
    "memory-of-limit": (
        "Working set as a share of the memory limit. Above 90% the next spike is an OOMKill."
    ),
    "image": "The image each container runs, from kube-state-metrics: which build is live.",
    "readiness-history": (
        "Per pod: ready when every container is ready. Built from the per-container "
        "series; the pod-level one is dropped at the gateway to save cardinality."
    ),
    "cpu": "CPU used (solid) against the pod's request and limit (dashed), in cores.",
    "memory": "Working set (solid) against the pod's memory limit (dashed).",
    "network": "Bytes per second in and out of the pod, all interfaces.",
    "pod-filesystem": (
        "The pod's own writable filesystem. Mounted volumes are not collected yet, "
        "so a persistent volume filling up does not show here."
    ),
    "oom-kills": "Containers whose last termination was OOMKilled.",
    "errors": (
        "Lines that carry an error level, a traceback or a panic. Prose that merely "
        "mentions an error can still match; there is no level field to filter on until "
        "the log line carries one."
    ),
    "recent": "Every line, unfiltered: the log pipeline's heartbeat.",
}

_PANEL_TITLES = {
    "ready-replicas": "Ready",
    "restarts-24h": "Restarts (24h)",
    "up-for": "Up for",
    "memory-of-limit": "Memory of limit",
    "cpu": "CPU",
    "oom-kills": "OOM kills",
}

# (width, height) on Perses's 24-column grid for panels on a row dashboard; the flat
# layout keeps its fixed two columns of 12x8.
_PANEL_SIZES = {
    "ready-replicas": (6, 4),
    "restarts-24h": (6, 4),
    "up-for": (6, 4),
    "memory-of-limit": (6, 4),
    "image": (12, 6),
    "readiness-history": (12, 6),
    "cpu": (12, 8),
    "memory": (12, 8),
    "network": (8, 7),
    "pod-filesystem": (8, 7),
    "oom-kills": (8, 7),
    "errors": (12, 10),
    "recent": (12, 10),
    "about": (24, 6),
}
_DEFAULT_SIZE = (12, 8)
_GRID_COLUMNS = 24

POD_VARIABLE = "pod"


def _panel_title(panel_name: str) -> str:
    prefix, _, metric = panel_name.partition(":")
    if metric in _PANEL_TITLES:
        return _PANEL_TITLES[metric]
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
        "spec": {"display": {"name": f"Noto · {name}"}},
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


def _about_text(ctx: Context, p: ResolvedProfile) -> str:
    """The Markdown `about` panel: what this is, where it runs, where it is managed,
    and what the page cannot show yet."""
    app = ctx.app or ctx.cluster
    lines = [
        f"### {app}",
        "",
        "| | |",
        "|---|---|",
        f"| Class | {p.class_name} |",
        f"| Namespace | {ctx.namespace or ctx.cluster} |",
        f"| Cluster | {ctx.cluster} |",
        f"| Environment | {ctx.environment} |",
        "",
    ]
    if p.links:
        lines.append("Links: " + " · ".join(f"[{k}]({v})" for k, v in sorted(p.links.items())))
        lines.append("")
    lines.append(
        "Signals on this page: kubelet and kube-state-metrics for the pod, and the pod's "
        "logs. The workload emits no application metrics yet, so what it is doing "
        "(turns, tool calls, tokens) is not here; its logs are."
    )
    return "\n".join(lines)


def _query_vars(ctx: Context, *, variable: bool) -> dict[str, str]:
    app = ctx.app or ctx.cluster
    return {
        "app": app,
        "namespace": ctx.namespace or ctx.cluster,
        "pod": f"${POD_VARIABLE}" if variable else f"{app}-.*",
    }


def _metric_queries(metric: str, ctx: Context, *, variable: bool) -> list[tuple[str | None, str]]:
    """(series name format, PromQL) per query of a metric panel."""
    cluster_scope = ctx.app is None
    if cluster_scope and metric in _CLUSTER_METRIC_QUERIES:
        return [(None, _CLUSTER_METRIC_QUERIES[metric])]
    template = _METRIC_QUERIES.get(metric, 'up{job="%(app)s"}')
    values = _query_vars(ctx, variable=variable)
    if isinstance(template, str):
        return [(None, template % values)]
    return [(name, query % values) for name, query in template]


def _panel(panel_name: str, ctx: Context, p: ResolvedProfile, *, variable: bool) -> dict:
    prefix, _, metric = panel_name.partition(":")
    chart_kind, query_kind, plugin_kind, default_query = _PANEL_PLUGINS[prefix]
    cluster_scope = ctx.app is None
    display: dict = {"name": _panel_title(panel_name)}
    if metric in _PANEL_DESCRIPTIONS:
        display["description"] = _PANEL_DESCRIPTIONS[metric]
    if prefix == "markdown":
        text = _about_text(ctx, p) if metric == "about" else f"_{metric}_"
        return {
            "kind": "Panel",
            "spec": {"display": display, "plugin": {"kind": "Markdown", "spec": {"text": text}}},
        }
    queries: list[tuple[str | None, str]]
    if prefix == "metric":
        queries = _metric_queries(metric, ctx, variable=variable)
    elif prefix == "logs":
        if cluster_scope and metric in _CLUSTER_LOG_QUERIES:
            query = _CLUSTER_LOG_QUERIES[metric]
        else:
            template = _LOG_QUERIES.get(metric, default_query)
            query = template % {"namespace": ctx.namespace or ctx.cluster}
        queries = [(None, query)]
    elif prefix == "traces" and metric == "slow-requests":
        # Named panel gets a query that actually matches its name (same principle as
        # _METRIC_QUERIES: a panel's title is a promise about what it shows).
        queries = [(None, _slow_requests_query(ctx.app or ctx.cluster, p))]
    else:
        queries = [(None, default_query % (ctx.app or ctx.cluster))]
    kind, plugin_spec = _PANEL_SPECS.get(metric, (chart_kind, {}))
    if prefix != "metric":
        kind, plugin_spec = chart_kind, {}
    rendered_queries = []
    for name, query in queries:
        spec: dict = {"query": query}
        if name:
            spec["seriesNameFormat"] = name
        rendered_queries.append(
            {"kind": query_kind, "spec": {"plugin": {"kind": plugin_kind, "spec": spec}}}
        )
    return {
        "kind": "Panel",
        "spec": {
            "display": display,
            "plugin": {"kind": kind, "spec": dict(plugin_spec)},
            "queries": rendered_queries,
        },
    }


def _ref(key: str) -> dict:
    return {"$ref": f"#/spec/panels/{key}"}


def _flat_layout(keys: list[str]) -> dict:
    """The untitled two-column grid a flat panel list renders to."""
    items = [
        {"x": (i % 2) * 12, "y": (i // 2) * 8, "width": 12, "height": 8, "content": _ref(key)}
        for i, key in enumerate(keys)
    ]
    return {"kind": "Grid", "spec": {"items": items}}


def _row_layout(row: Row) -> dict:
    """One titled, collapsible grid per row; panels packed left to right by their own
    sizes, wrapping at the grid's edge."""
    items = []
    x = y = line_height = 0
    for name in row.panels:
        _, _, slug = name.partition(":")
        width, height = _PANEL_SIZES.get(slug, _DEFAULT_SIZE)
        if x + width > _GRID_COLUMNS:
            x, y, line_height = 0, y + line_height, 0
        items.append(
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "content": _ref(name.replace(":", "-")),
            }
        )
        x += width
        line_height = max(line_height, height)
    return {
        "kind": "Grid",
        "spec": {"display": {"title": row.title, "collapse": {"open": row.open}}, "items": items},
    }


def _pod_variable(namespace: str) -> dict:
    """`$pod`: the pods of the app's namespace, all of them by default. The matcher is
    the one kubeletstats series every pod has."""
    return {
        "kind": "ListVariable",
        "spec": {
            "name": POD_VARIABLE,
            "display": {"name": "Pod", "hidden": False},
            "allowAllValue": True,
            "allowMultiple": True,
            "customAllValue": ".*",
            "defaultValue": "$__all",
            "sort": "alphabetical-asc",
            "plugin": {
                "kind": "PrometheusLabelValuesVariable",
                "spec": {
                    "labelName": "k8s_pod_name",
                    "matchers": [f'k8s_pod_memory_working_set{{k8s_namespace_name="{namespace}"}}'],
                },
            },
        },
    }


def _dashboard(dash: Dashboard, project: str, ctx: Context, p: ResolvedProfile) -> dict:
    # A row dashboard for an app narrows by pod; a flat or cluster one has no variable
    # (its queries already say which pods they mean, or mean every namespace).
    variable = bool(dash.rows) and ctx.app is not None
    panels = {
        name.replace(":", "-"): _panel(name, ctx, p, variable=variable) for name in dash.all_panels
    }
    rows_layout = [_row_layout(row) for row in dash.rows]
    layouts = rows_layout or [_flat_layout(list(panels))]
    # Kind-first display names so the project's flat, alphabetically sorted
    # dashboard list groups itself by kind (Agent · ..., Cluster · ...,
    # Gateway · ..., Web · ...) - Perses has no folders and this version
    # strips tags, so the name IS the taxonomy.
    kind = _KIND_LABELS.get(p.class_name, p.class_name.capitalize())
    if ctx.app:
        name = f"{ctx.app}-{dash.name}"
        display = f"{kind} · {ctx.app.capitalize()}"
        if dash.name not in _CLASS_PRIMARY_DASHBOARDS:
            pretty = dash.name.replace("-", " ").title()
            display = f"{display} · {pretty}"
    else:
        name = dash.name
        pretty = dash.name.removeprefix("cluster-").replace("-", " ").title()
        display = f"Cluster · {pretty}"
    spec: dict = {
        "display": {"name": display},
        "duration": dash.duration,
        "panels": panels,
        "layouts": layouts,
    }
    if dash.refresh:
        spec["refreshInterval"] = dash.refresh
    if variable:
        spec["variables"] = [_pod_variable(ctx.namespace or ctx.cluster)]
    if ctx.app and p.links:
        spec["links"] = [
            {"name": k, "url": v, "targetBlank": True} for k, v in sorted(p.links.items())
        ]
    return {"kind": "Dashboard", "metadata": {"name": name, "project": project}, "spec": spec}


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
