"""Sync rendered rule groups into Mimir's ruler (per tenant).

PrometheusRule CRDs alone are inert in our stack (no Prometheus runs; Mimir is the
metrics engine) - the ruler evaluates rules, and this pushes each rendered group to
its API: POST /prometheus/config/v1/rules/{namespace} with the group YAML, tenant
header on every call. Idempotent: same name overwrites.
"""

import urllib.request
from collections.abc import Callable

import yaml

Http = Callable[[str, bytes, dict], tuple[int, str]]


class RulerSyncError(RuntimeError):
    pass


def _urllib_http(url: str, data: bytes, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def prometheusrule_to_groups(doc: dict) -> list[dict]:
    """A PrometheusRule CRD's spec.groups are already ruler-shaped group dicts."""
    return list(doc.get("spec", {}).get("groups", []))


def sync_rules(
    mimir_base: str,
    tenant: str,
    namespace: str,
    docs: list[dict],
    http: Http = _urllib_http,
) -> int:
    """Push every group from every PrometheusRule doc; returns groups synced."""
    synced = 0
    for doc in docs:
        if doc.get("kind") != "PrometheusRule":
            continue
        for group in prometheusrule_to_groups(doc):
            body = yaml.safe_dump(group).encode()
            url = f"{mimir_base.rstrip('/')}/config/v1/rules/{namespace}"
            status, text = http(
                url,
                body,
                {"X-Scope-OrgID": tenant, "Content-Type": "application/yaml"},
            )
            if status not in (200, 201, 202):
                raise RulerSyncError(
                    f"ruler rejected group {group.get('name')}: {status} {text[:200]}"
                )
            synced += 1
    return synced
