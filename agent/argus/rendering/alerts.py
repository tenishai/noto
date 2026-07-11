"""Non-SLO alert packs: collector self-health (always) + per-app scrape-down.

The scrape-down rule is the portable replacement for gojira's GMP GojiraAppScrapeDown.
SLO burn-rate alerts are NOT here - Sloth generates those (see slo.py).
"""

from argus.profiles.models import ResolvedProfile
from argus.rendering.context import Context

_COLLECTOR_HEALTH_RULES = [
    {
        "alert": "ArgusCollectorMemoryHigh",
        "expr": "otelcol_process_memory_rss / otelcol_process_memory_limit > 0.85",
        "for": "10m",
        "labels": {"severity": "ticket"},
        "annotations": {"summary": "Collector {{ $labels.job }} memory near the limiter threshold"},
    },
    {
        "alert": "ArgusCollectorRefusing",
        "expr": (
            "sum by (job) (rate(otelcol_receiver_refused_spans[5m])"
            " + rate(otelcol_receiver_refused_metric_points[5m])"
            " + rate(otelcol_receiver_refused_log_records[5m])) > 0"
        ),
        "for": "5m",
        "labels": {"severity": "page"},
        "annotations": {"summary": "Collector {{ $labels.job }} is refusing telemetry at the door"},
    },
    {
        "alert": "ArgusExporterFailing",
        "expr": (
            "sum by (job, exporter) (rate(otelcol_exporter_send_failed_spans[5m])"
            " + rate(otelcol_exporter_send_failed_metric_points[5m])"
            " + rate(otelcol_exporter_send_failed_log_records[5m])) > 0"
        ),
        "for": "5m",
        "labels": {"severity": "page"},
        "annotations": {"summary": "Collector exporter {{ $labels.exporter }} failing sends"},
    },
]


def render_alert_rules(p: ResolvedProfile, ctx: Context) -> dict:
    if not ctx.app or not ctx.namespace:
        raise ValueError("alert packs are per-app: Context.app and Context.namespace are required")
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {
            "name": f"argus-alerts-{ctx.app}",
            "namespace": ctx.namespace,
            "labels": {"argus.dev/managed": "true", "argus.dev/class": p.class_name},
        },
        "spec": {
            "groups": [
                {"name": "argus-collector-health", "rules": list(_COLLECTOR_HEALTH_RULES)},
                {
                    "name": f"argus-app-{ctx.app}",
                    "rules": [
                        {
                            "alert": "ArgusScrapeDown",
                            "expr": f'up{{job="{ctx.app}"}} == 0',
                            "for": "5m",
                            "labels": {"severity": "page"},
                            "annotations": {
                                "summary": f"{ctx.app} scrape target down (no /metrics for 5m)"
                            },
                        }
                    ],
                },
            ]
        },
    }
