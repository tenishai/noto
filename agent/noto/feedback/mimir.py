"""Tenant-aware Mimir range queries + the measurement set for one app.

Metric names LIVE-VERIFIED against a smoke cluster (2026-07-11): Mimir's OTLP ingest
stores kubeletstats names WITHOUT unit/_total suffixes (k8s_pod_cpu_time,
k8s_pod_memory_working_set - pod-level, containers already summed), with resource
attributes promoted to labels via promote_otel_resource_attributes (helm values).
Declared request/limit and restart/OOM counts come from kube-state-metrics via the
scrape path.
"""

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

QueryFn = Callable[[str, str, int, int, int], list[float]]
"""(query, tenant, start, end, step_seconds) -> flat sample values."""


class MimirQueryError(RuntimeError):
    pass


@dataclass
class MimirClient:
    base_url: str  # e.g. http://noto-mimir-gateway.noto.svc:80/prometheus
    tenant: str

    def query_range(self, query: str, start: int, end: int, step: int) -> list[float]:
        params = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/query_range?{params}",
            headers={"X-Scope-OrgID": self.tenant},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
        except Exception as e:
            raise MimirQueryError(f"query failed: {e}") from e
        if data.get("status") != "success":
            raise MimirQueryError(f"query error: {json.dumps(data)[:300]}")
        return [
            float(value)
            for series in data["data"]["result"]
            for _, value in series.get("values", [])
        ]

    def query_scalar(self, query: str) -> float:
        """Instant query, first sample value (0.0 when empty)."""
        params = urllib.parse.urlencode({"query": query})
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/query?{params}",
            headers={"X-Scope-OrgID": self.tenant},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        result = data["data"]["result"]
        return float(result[0]["value"][1]) if result else 0.0


def app_selector(app: str, namespace: str) -> str:
    return f'k8s_namespace_name="{namespace}", k8s_pod_name=~"{app}-.*"'


def ksm_selector(app: str, namespace: str) -> str:
    return f'namespace="{namespace}", pod=~"{app}-.*"'


def measurements(app: str, namespace: str) -> dict[str, str]:
    """The per-app query set. Per-pod aggregation: sizing is per-pod requests."""
    sel = app_selector(app, namespace)
    ksm = ksm_selector(app, namespace)
    return {
        # kubeletstats via OTLP (live-verified names; pod-level = per-pod sizing basis)
        "cpu_usage": f"sum by (k8s_pod_name) (rate(k8s_pod_cpu_time{{{sel}}}[5m]))",
        "memory_usage": f"sum by (k8s_pod_name) (k8s_pod_memory_working_set{{{sel}}})",
        # kube-state-metrics (scrape path)
        "cpu_request": f'max(kube_pod_container_resource_requests{{{ksm}, resource="cpu"}})',
        "cpu_limit": f'max(kube_pod_container_resource_limits{{{ksm}, resource="cpu"}})',
        "memory_request": f'max(kube_pod_container_resource_requests{{{ksm}, resource="memory"}})',
        "memory_limit": f'max(kube_pod_container_resource_limits{{{ksm}, resource="memory"}})',
        "restarts": f"sum(increase(kube_pod_container_status_restarts_total{{{ksm}}}[{{window}}]))",
        "oom_kills": (
            f'sum(kube_pod_container_status_last_terminated_reason{{{ksm}, reason="OOMKilled"}})'
        ),
    }


def collect(
    client: MimirClient, app: str, namespace: str, window_days: int, step: int = 300
) -> dict:
    """Run the measurement set over the window; returns raw values for the distiller."""
    end = int(time.time())
    start = end - window_days * 86400
    window = f"{window_days}d"
    q = measurements(app, namespace)
    return {
        "cpu_samples": client.query_range(q["cpu_usage"], start, end, step),
        "memory_samples": client.query_range(q["memory_usage"], start, end, step),
        "cpu_request": client.query_scalar(q["cpu_request"]),
        "cpu_limit": client.query_scalar(q["cpu_limit"]),
        "memory_request": client.query_scalar(q["memory_request"]),
        "memory_limit": client.query_scalar(q["memory_limit"]),
        "restarts": int(client.query_scalar(q["restarts"].replace("{window}", window))),
        "oom_kills": int(client.query_scalar(q["oom_kills"])),
    }
