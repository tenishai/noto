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
# because there are four verbs now, not one. Every function below takes it as `None` and
# falls back to `urllib_http` in its body rather than as a default argument: a default is
# bound once at definition, so the transport could not be replaced afterwards, and the
# test suite's rule that no unit test opens a socket depends on replacing it.
Http = Callable[[str, str, bytes | None, dict], tuple[int, str]]

_OK = (200, 201, 202)

# The ruler namespace the platform's own group is filed under - the PrometheusRule's own
# name (rendering/alerts.py renders it, `noto platform` pushes it), so the group is as
# easy to find in the ruler as it is in the cluster. It lives here because it is a ruler
# namespace first: the CLI and the SyncRules step both import it from this module, which
# neither of them can import in a cycle.
PLATFORM_RULE_GROUP = "noto-platform"


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


def refuse_platform_namespace(namespace: str) -> None:
    """Refuse the platform's own ruler namespace to an app's run.

    `noto-platform` is a legal app name, and an app filed there would push its groups
    beside the platform's retention alarm and then prune everything it did not render -
    the alarm included, because only `noto platform` renders it. The two callers that
    resolve an app's namespace (the SyncRules step and `noto rules-sync`) ask here, so
    there is one spelling of the rule and one sentence for it.
    """
    if namespace == PLATFORM_RULE_GROUP:
        raise RulerSyncError(
            f"{PLATFORM_RULE_GROUP!r} is the platform's own ruler namespace; an app cannot use it"
        )


def _namespace_url(mimir_base: str, namespace: str) -> str:
    return f"{mimir_base.rstrip('/')}/config/v1/rules/{namespace}"


def prometheusrule_to_groups(doc: dict) -> list[dict]:
    """A PrometheusRule CRD's spec.groups are already ruler-shaped group dicts."""
    return list(doc.get("spec", {}).get("groups", []))


def rendered_group_names(docs: list[dict]) -> set[str]:
    """Every group name a set of rendered documents defines - the keep-set for pruning.

    A group with no name contributes nothing: as the literal "None" it would keep a group
    actually called None from ever being pruned, and it can never match anything else.
    """
    return {
        str(name)
        for doc in docs
        if doc.get("kind") == "PrometheusRule"
        for group in prometheusrule_to_groups(doc)
        if (name := group.get("name"))
    }


def rendered_group_count(docs: list[dict]) -> int:
    """How many groups a push of `docs` would POST - the number `sync_rules` returns.

    Not `len(rendered_group_names(docs))`: names are a set, so two documents defining the
    same group name (an app's pack and a generated SLO pack) would make a dry run promise
    one push where the run that follows it makes two.
    """
    return sum(
        len(prometheusrule_to_groups(doc)) for doc in docs if doc.get("kind") == "PrometheusRule"
    )


def sync_rules(
    mimir_base: str,
    tenant: str,
    namespace: str,
    docs: list[dict],
    http: Http | None = None,
) -> int:
    """Push every group from every PrometheusRule doc; returns groups synced."""
    http = http or urllib_http
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
    http: Http | None = None,
) -> list[str]:
    """The names of the groups the ruler holds in `namespace`, in the order it lists them.

    A namespace holding no groups - every app's first run, and again once the last group
    is deleted - answers 200 with the body `{}` on the pinned Mimir (read off a live
    ruler). A 404 and an empty body are tolerated too: which status a ruler picks for
    "nothing here" is not a thing to depend on, and all three mean the same here.

    The body is a map keyed by namespace, and only this namespace's key is read: a listing
    that mentions any other namespace contributes nothing. An entry with no name is
    skipped - there is no URL that addresses it, and "None" would address a different one.
    """
    http = http or urllib_http
    url = _namespace_url(mimir_base, namespace)
    status, text = http("GET", url, None, {"X-Scope-OrgID": tenant})
    if status == 404:
        return []
    if status not in _OK:
        raise RulerSyncError(f"ruler refused to list namespace {namespace}: {status} {text[:200]}")
    listing = yaml.safe_load(text) or {}
    return [str(name) for group in listing.get(namespace) or [] if (name := group.get("name"))]


def prune_groups(
    mimir_base: str,
    tenant: str,
    namespace: str,
    keep: set[str],
    http: Http | None = None,
) -> list[str]:
    """Delete every group the ruler holds in `namespace` that is not in `keep`; returns
    the names deleted.

    Only ever this namespace: each delete URL is built from the argument, never from the
    listing, so one app's run cannot reach another app's groups or the platform's own.
    """
    http = http or urllib_http
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
