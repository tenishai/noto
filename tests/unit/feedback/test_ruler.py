"""The ruler client: push, list, prune, and the one failure a caller must tell apart.

A group the ruler still holds but nothing renders any more evaluates forever (issue #57),
so listing and deleting are part of this client, not of whoever calls it. Every test here
drives an injected `http`: no socket is ever opened.
"""

import io
import urllib.error

import pytest
import yaml

from noto.feedback.ruler import (
    PLATFORM_RULE_GROUP,
    RulerSyncError,
    RulerUnreachable,
    list_groups,
    prune_groups,
    refuse_platform_namespace,
    rendered_group_count,
    rendered_group_names,
    sync_rules,
    urllib_http,
)

BASE = "http://mimir/prometheus"


class FakeHttp:
    """Answers `(status, body)` per (method, url), and records every call in order.

    A canned response missing for a call is an assertion failure, not a silent 200: a
    test must say what the ruler answers to every request the code makes.
    """

    def __init__(self, responses: dict[tuple[str, str], tuple[int, str]]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[str, str, bytes | None, dict]] = []

    def __call__(self, method: str, url: str, data: bytes | None, headers: dict) -> tuple[int, str]:
        self.calls.append((method, url, data, headers))
        try:
            return self.responses[(method, url)]
        except KeyError:
            raise AssertionError(f"no canned response for {method} {url}") from None


def _listing(namespace: str, *names: str) -> str:
    return yaml.safe_dump({namespace: [{"name": n, "rules": []} for n in names]})


def _docs(*names: str) -> list[dict]:
    return [
        {
            "kind": "PrometheusRule",
            "spec": {"groups": [{"name": n, "rules": [{"alert": "A", "expr": "up == 0"}]}]},
        }
        for n in names
    ]


# --- list_groups -------------------------------------------------------------


def test_list_groups_returns_the_namespaces_group_names() -> None:
    http = FakeHttp({("GET", f"{BASE}/config/v1/rules/app"): (200, _listing("app", "g1", "g2"))})
    assert list_groups(BASE, "t1", "app", http=http) == ["g1", "g2"]
    _method, _url, data, headers = http.calls[0]
    assert data is None
    assert headers["X-Scope-OrgID"] == "t1"


def test_list_groups_is_empty_when_the_namespace_holds_nothing() -> None:
    """What the pinned Mimir answers for a namespace holding no groups, read off a live
    ruler: 200 with the body `{}`. A 404 and an empty body are tolerated too, because the
    status a ruler picks for "nothing here" is not something to depend on."""
    http = FakeHttp({("GET", f"{BASE}/config/v1/rules/app"): (200, "{}")})
    assert list_groups(BASE, "t1", "app", http=http) == []
    http = FakeHttp({("GET", f"{BASE}/config/v1/rules/app"): (404, "namespace not found")})
    assert list_groups(BASE, "t1", "app", http=http) == []
    http = FakeHttp({("GET", f"{BASE}/config/v1/rules/app"): (200, "")})
    assert list_groups(BASE, "t1", "app", http=http) == []


def test_list_groups_skips_an_entry_with_no_name() -> None:
    """A group the ruler lists without a name is nothing this can address: `str(None)`
    would make it the literal group "None" and the next prune would try to DELETE it."""
    listing = yaml.safe_dump({"app": [{"name": "g1", "rules": []}, {"rules": []}]})
    http = FakeHttp({("GET", f"{BASE}/config/v1/rules/app"): (200, listing)})
    assert list_groups(BASE, "t1", "app", http=http) == ["g1"]


def test_list_groups_raises_on_any_other_status() -> None:
    http = FakeHttp({("GET", f"{BASE}/config/v1/rules/app"): (500, "boom")})
    with pytest.raises(RulerSyncError, match="500"):
        list_groups(BASE, "t1", "app", http=http)


# --- prune_groups ------------------------------------------------------------


def test_prune_deletes_only_the_groups_nobody_renders_any_more() -> None:
    http = FakeHttp(
        {
            ("GET", f"{BASE}/config/v1/rules/app"): (200, _listing("app", "keep", "stale")),
            ("DELETE", f"{BASE}/config/v1/rules/app/stale"): (202, ""),
        }
    )
    assert prune_groups(BASE, "t1", "app", {"keep"}, http=http) == ["stale"]
    deletes = [(m, u) for m, u, _d, _h in http.calls if m == "DELETE"]
    assert deletes == [("DELETE", f"{BASE}/config/v1/rules/app/stale")]
    assert http.calls[-1][3]["X-Scope-OrgID"] == "t1"


def test_prune_deletes_nothing_when_the_ruler_holds_only_rendered_groups() -> None:
    http = FakeHttp({("GET", f"{BASE}/config/v1/rules/app"): (200, _listing("app", "g1"))})
    assert prune_groups(BASE, "t1", "app", {"g1", "g2"}, http=http) == []
    assert [m for m, _u, _d, _h in http.calls] == ["GET"]


def test_prune_never_leaves_the_namespace_it_was_given() -> None:
    """One app's run must not be able to delete another app's groups, or the platform's.
    Every URL this issues is built from the namespace argument, so a listing that somehow
    mentions another namespace still cannot produce a DELETE outside this one."""
    other = yaml.safe_dump(
        {
            "app-a": [{"name": "stale", "rules": []}],
            "app-b": [{"name": "someone-elses", "rules": []}],
            "noto-platform": [{"name": "noto-platform-retention", "rules": []}],
        }
    )
    http = FakeHttp(
        {
            ("GET", f"{BASE}/config/v1/rules/app-a"): (200, other),
            ("DELETE", f"{BASE}/config/v1/rules/app-a/stale"): (202, ""),
        }
    )
    assert prune_groups(BASE, "t1", "app-a", set(), http=http) == ["stale"]
    for method, url, _data, _headers in http.calls:
        if method == "DELETE":
            assert url.startswith(f"{BASE}/config/v1/rules/app-a/")


def test_a_delete_the_ruler_refuses_raises() -> None:
    http = FakeHttp(
        {
            ("GET", f"{BASE}/config/v1/rules/app"): (200, _listing("app", "stale")),
            ("DELETE", f"{BASE}/config/v1/rules/app/stale"): (500, "boom"),
        }
    )
    with pytest.raises(RulerSyncError, match="stale"):
        prune_groups(BASE, "t1", "app", set(), http=http)


def test_rendered_group_names_reads_every_prometheusrule_doc() -> None:
    docs = [*_docs("g1", "g2"), {"kind": "ServiceMonitor"}]
    assert rendered_group_names(docs) == {"g1", "g2"}


def test_rendered_group_names_skips_a_group_with_no_name() -> None:
    """A nameless group cannot be a keep-set entry: as the literal "None" it would match
    nothing the ruler holds, and worse, it would protect a group actually called None."""
    docs = [
        {"kind": "PrometheusRule", "spec": {"groups": [{"name": "g1", "rules": []}, {"rules": []}]}}
    ]
    assert rendered_group_names(docs) == {"g1"}


def test_the_dry_run_count_is_the_count_a_real_push_makes() -> None:
    """Two documents may define the same group name (an app pack and a generated SLO pack
    that collide). The names are a set; the pushes are not, so a dry run that counted
    names would promise one push where a real run makes two."""
    docs = [*_docs("same"), *_docs("same"), *_docs("other")]
    http = FakeHttp({("POST", f"{BASE}/config/v1/rules/app"): (202, "")})
    assert rendered_group_count(docs) == sync_rules(BASE, "t1", "app", docs, http=http)
    assert rendered_group_count(docs) == 3


# --- the platform's own namespace is not an app's to use ---------------------


def test_the_platforms_own_namespace_is_refused() -> None:
    """`noto-platform` is a legal app name, and an app run there would prune the
    platform's retention alarm: its groups are not rendered by an app's run."""
    with pytest.raises(RulerSyncError, match="the platform's own ruler namespace"):
        refuse_platform_namespace(PLATFORM_RULE_GROUP)
    assert refuse_platform_namespace("clips-api") is None


# --- sync_rules on the method-first signature --------------------------------


def test_sync_rules_posts_each_group_to_the_namespace() -> None:
    http = FakeHttp({("POST", f"{BASE}/config/v1/rules/app"): (202, "")})
    assert sync_rules(BASE, "t1", "app", _docs("g1", "g2"), http=http) == 2
    method, url, data, headers = http.calls[0]
    assert (method, url) == ("POST", f"{BASE}/config/v1/rules/app")
    assert b"name: g1" in data
    assert headers["X-Scope-OrgID"] == "t1"
    assert headers["Content-Type"] == "application/yaml"


def test_sync_rules_raises_naming_the_group_the_ruler_refused() -> None:
    http = FakeHttp({("POST", f"{BASE}/config/v1/rules/app"): (400, "bad expr")})
    with pytest.raises(RulerSyncError, match="g1"):
        sync_rules(BASE, "t1", "app", _docs("g1"), http=http)


# --- the transport turns an unreachable ruler into one exception type --------


def test_an_unreachable_ruler_is_a_ruler_error_carrying_the_reason(monkeypatch) -> None:
    """Callers decide what an unreachable ruler means (a note on a laptop, a failure in
    a Job), and they must not have to catch urllib types to do it."""

    def refused(_req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refused)
    with pytest.raises(RulerUnreachable, match="Connection refused"):
        urllib_http("GET", f"{BASE}/config/v1/rules/app", None, {})


def test_a_status_the_ruler_answered_with_is_returned_not_raised(monkeypatch) -> None:
    """An HTTPError IS a URLError; only the one that never reached the ruler is
    unreachable. A 404 has to come back as a status so `list_groups` can read it."""

    def not_found(_req, timeout=None):
        raise urllib.error.HTTPError(f"{BASE}/x", 404, "Not Found", {}, io.BytesIO(b"Not Found"))

    monkeypatch.setattr("urllib.request.urlopen", not_found)
    status, _body = urllib_http("GET", f"{BASE}/config/v1/rules/app", None, {})
    assert status == 404
