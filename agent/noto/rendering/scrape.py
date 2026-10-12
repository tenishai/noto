"""ServiceMonitor/PodMonitor per app - the portable replacements for gojira's GMP CRDs.

Every gojira app serves /metrics on port "http" (8080); the selector keys on the
standard app.kubernetes.io/name label gojira's manifests set.
"""

from noto.profiles.models import ResolvedProfile
from noto.rendering.context import Context

API_VERSION = "monitoring.coreos.com/v1"
ENDPOINT = {"port": "http", "path": "/metrics", "interval": "15s"}


def _require_app(ctx: Context) -> None:
    if not ctx.app:
        raise ValueError("scrape wiring is per-app: Context.app is required")
    if not ctx.namespace:
        raise ValueError("scrape wiring is per-app: Context.namespace is required")


def _meta(kind: str, p: ResolvedProfile, ctx: Context) -> dict:
    return {
        "name": ctx.app,
        "namespace": ctx.namespace,
        "labels": {
            "app.kubernetes.io/name": ctx.app,
            "noto.dev/managed": "true",
            "noto.dev/class": p.class_name,
        },
    }


def render_servicemonitor(p: ResolvedProfile, ctx: Context) -> dict:
    _require_app(ctx)
    return {
        "apiVersion": API_VERSION,
        "kind": "ServiceMonitor",
        "metadata": _meta("ServiceMonitor", p, ctx),
        "spec": {
            "selector": {"matchLabels": {"app.kubernetes.io/name": ctx.app}},
            "endpoints": [dict(ENDPOINT)],
        },
    }


def render_podmonitor(p: ResolvedProfile, ctx: Context) -> dict:
    _require_app(ctx)
    return {
        "apiVersion": API_VERSION,
        "kind": "PodMonitor",
        "metadata": _meta("PodMonitor", p, ctx),
        "spec": {
            "selector": {"matchLabels": {"app.kubernetes.io/name": ctx.app}},
            "podMetricsEndpoints": [dict(ENDPOINT)],
        },
    }
