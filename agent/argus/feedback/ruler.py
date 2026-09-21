"""Sync rendered rule groups into Mimir's ruler (per tenant), and prune what is stale.

PrometheusRule CRDs alone are inert in our stack (no Prometheus runs; Mimir is the
metrics engine) - the ruler evaluates rules, and this pushes each rendered group to
its API: POST /prometheus/config/v1/rules/{namespace} with the group YAML, tenant
header on every call. Idempotent: same name overwrites.

Pushing alone is not enough. A group the ruler holds that nobody renders any more keeps
evaluating forever (a renamed SLO, a removed alert pack: issue #57), so listing and
deleting live here too, beside the push, rather than in whichever caller remembers.
Every delete this module issues is built from the namespace it was given, so a caller
can never reach another app's groups.
"""

import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

import yaml

# (method, url, body or None, headers) -> (status, body). The method comes first
# because there are four verbs now, not one.
Http = Callable[[str, str, bytes | None, dict], tuple[int, str]]

_OK = (200, 201, 202)


class RulerSyncError(RuntimeError):
    pass


class RulerUnreachable(RulerSyncError):
    """Nothing answered: the ruler was never reached, so it neither took nor refused the
    rules. Callers tell this apart from a rejection - an operator on a laptop cannot
    route to the in-cluster URL and that is not a failure, while a Job running inside the
    cluster hitting this has a real problem. The distinction is made here so no caller
    has to catch urllib's exception types to make it."""


def urllib_http(method: str, url: str, data: bytes | None, headers: dict) -> tuple[int, str]:
    """The real transport. Public because the one caller with its own injectable seam
    (the SyncRules step) falls back to it by name."""
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    # An HTTPError IS a URLError, and it means the ruler answered: a 404 from a
    # namespace that holds nothing has to come back as a status, not as an exception.
    # Order matters, and only the plain URLError below is "unreachable".
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except urllib.error.URLError as e:
        raise RulerUnreachable(str(e.reason)) from e


def _namespace_url(mimir_base: str, namespace: str) -> str:
    return f"{mimir_base.rstrip('/')}/config/v1/rules/{namespace}"


def prometheusrule_to_groups(doc: dict) -> list[dict]:
    """A PrometheusRule CRD's spec.groups are already ruler-shaped group dicts."""
    return list(doc.get("spec", {}).get("groups", []))


def rendered_group_names(docs: list[dict]) -> set[str]:
    """Every group name a set of rendered documents defines - the keep-set for pruning."""
    return {
        str(group.get("name"))
        for doc in docs
        if doc.get("kind") == "PrometheusRule"
        for group in prometheusrule_to_groups(doc)
    }


def sync_rules(
    mimir_base: str,
    tenant: str,
    namespace: str,
    docs: list[dict],
    http: Http = urllib_http,
) -> int:
    """Push every group from every PrometheusRule doc; returns groups synced."""
    synced = 0
    url = _namespace_url(mimir_base, namespace)
    for doc in docs:
        if doc.get("kind") != "PrometheusRule":
            continue
        for group in prometheusrule_to_groups(doc):
            body = yaml.safe_dump(group).encode()
            status, text = http(
                "POST",
                url,
                body,
                {"X-Scope-OrgID": tenant, "Content-Type": "application/yaml"},
            )
            if status not in _OK:
                raise RulerSyncError(
                    f"ruler rejected group {group.get('name')}: {status} {text[:200]}"
                )
            synced += 1
    return synced


def list_groups(
    mimir_base: str,
    tenant: str,
    namespace: str,
    http: Http = urllib_http,
) -> list[str]:
    """The names of the groups the ruler holds in `namespace`, in the order it lists them.

    A 404 is the answer for a namespace the ruler has never been given a group for (every
    app's first run) and an empty body the answer once the last one is deleted; both mean
    the same thing here. The body is a map keyed by namespace, and only this namespace's
    key is read: a listing that mentions any other namespace contributes nothing.
    """
    url = _namespace_url(mimir_base, namespace)
    status, text = http("GET", url, None, {"X-Scope-OrgID": tenant})
    if status == 404:
        return []
    if status not in _OK:
        raise RulerSyncError(f"ruler refused to list namespace {namespace}: {status} {text[:200]}")
    listing = yaml.safe_load(text) or {}
    return [str(group.get("name")) for group in listing.get(namespace) or []]


def prune_groups(
    mimir_base: str,
    tenant: str,
    namespace: str,
    keep: set[str],
    http: Http = urllib_http,
) -> list[str]:
    """Delete every group the ruler holds in `namespace` that is not in `keep`; returns
    the names deleted.

    Only ever this namespace: each delete URL is built from the argument, never from the
    listing, so one app's run cannot reach another app's groups or the platform's own.
    """
    base = _namespace_url(mimir_base, namespace)
    deleted: list[str] = []
    for name in list_groups(mimir_base, tenant, namespace, http=http):
        if name in keep:
            continue
        # Group names come from rendered documents, but they are free-form strings in the
        # ruler's model; quoting keeps one with a slash or a space in it from addressing a
        # different path than the group it names.
        status, text = http(
            "DELETE",
            f"{base}/{urllib.parse.quote(name, safe='')}",
            None,
            {"X-Scope-OrgID": tenant},
        )
        if status not in _OK:
            raise RulerSyncError(f"ruler refused to delete group {name}: {status} {text[:200]}")
        deleted.append(name)
    return deleted
