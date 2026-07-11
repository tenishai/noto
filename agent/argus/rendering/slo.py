"""Render the profile's tool-neutral slos: block into a Sloth PrometheusServiceLevel.

Sloth (sloth.slok.dev/v1) generates the multiwindow-multiburn PrometheusRules; we never
hand-roll burn-rate math (design decision). Ratio SLIs: errorQuery over totalQuery.
"""

from argus.profiles.models import SLO, ResolvedProfile
from argus.rendering.context import Context

_UNITS = {"ms": 0.001, "s": 1.0}


def _duration_to_seconds(value: str) -> float:
    for suffix, factor in _UNITS.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * factor
    raise ValueError(f"unsupported latency threshold {value!r} (use ms or s)")


def _sli(slo: SLO, app: str, namespace: str) -> dict:
    # Prometheus-client conventions (verified against a live gojira-shaped app in the
    # smoke run): http_requests_total{status=...} + http_request_duration_seconds_*.
    base = f'{{job="{app}", namespace="{namespace}"}}'
    total = f"sum(rate(http_requests_total{base}[{{{{.window}}}}]))"
    if slo.signal == "availability":
        error_selector = f'{{job="{app}", namespace="{namespace}", status=~"5.."}}'
        error = f"sum(rate(http_requests_total{error_selector}[{{{{.window}}}}]))"
    else:
        seconds = _duration_to_seconds(slo.latency_threshold or "")
        le = f"{seconds:g}"
        fast_selector = f'{{job="{app}", namespace="{namespace}", le="{le}"}}'
        duration_total = f"sum(rate(http_request_duration_seconds_count{base}[{{{{.window}}}}]))"
        fast = f"sum(rate(http_request_duration_seconds_bucket{fast_selector}[{{{{.window}}}}]))"
        error = f"({duration_total} - {fast})"
        return {"events": {"errorQuery": error, "totalQuery": duration_total}}
    return {"events": {"errorQuery": error, "totalQuery": total}}


def render_sloth(p: ResolvedProfile, ctx: Context) -> dict | None:
    if not ctx.app or not ctx.namespace:
        raise ValueError("SLOs are per-app: Context.app and Context.namespace are required")
    slos = [s for s in p.slos if s.enabled]
    if not slos:
        return None

    return {
        "apiVersion": "sloth.slok.dev/v1",
        "kind": "PrometheusServiceLevel",
        "metadata": {
            "name": f"argus-slo-{ctx.app}",
            "namespace": ctx.namespace,
            "labels": {"argus.dev/managed": "true", "argus.dev/class": p.class_name},
        },
        "spec": {
            "service": ctx.app,
            "labels": {"cluster": ctx.cluster, "environment": ctx.environment},
            "slos": [
                {
                    "name": slo.name,
                    "objective": slo.objective,
                    "description": f"Argus {p.class_name} profile SLO {slo.name} for {ctx.app}",
                    "sli": _sli(slo, ctx.app, ctx.namespace),
                    "alerting": {
                        "name": f"ArgusSLO-{ctx.app}-{slo.name}",
                        "pageAlert": {"labels": {"severity": "page"}},
                        "ticketAlert": {"labels": {"severity": "ticket"}},
                    },
                }
                for slo in slos
            ],
        },
    }
