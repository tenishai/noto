"""The SyncRules step, against an injected `http` fake - no sockets, ever.

One test per row of the step's behaviour table in the plan/design: nothing rendered,
dry run, a clean push, a stale group, a ruler that refuses, and an unreachable ruler in
both modes (a note on an operator's laptop, a failure in a Job).
"""

import pytest
import yaml

from noto.feedback.ruler import RulerSyncError, RulerUnreachable
from noto.steps import StepContext, SyncRules

BASE = "http://mimir/prometheus"
NS_URL = f"{BASE}/config/v1/rules/demo"


class FakeHttp:
    """Answers `(status, body)` per (method, url); records every call in order."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, str]]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[str, str, bytes | None, dict]] = []

    def __call__(self, method: str, url: str, data: bytes | None, headers: dict) -> tuple[int, str]:
        self.calls.append((method, url, data, headers))
        try:
            return self.responses[(method, url)]
        except KeyError:
            raise AssertionError(f"no canned response for {method} {url}") from None


def _exploding(method: str, url: str, data: bytes | None, headers: dict) -> tuple[int, str]:
    raise AssertionError(f"this run must send nothing: {method} {url}")


def _rule_doc(*names: str) -> dict:
    return {
        "kind": "PrometheusRule",
        "spec": {
            "groups": [{"name": n, "rules": [{"alert": "A", "expr": "up == 0"}]} for n in names]
        },
    }


def _listing(*names: str) -> str:
    return yaml.safe_dump({"demo": [{"name": n, "rules": []} for n in names]})


def _ctx(http, rendered=None, dry_run: bool = False, required: bool = False) -> StepContext:
    return StepContext(
        runner=lambda *_a, **_kw: (0, ""),
        rendered=rendered if rendered is not None else {"alerts": [_rule_doc("g1")]},
        dry_run=dry_run,
        ruler_url=BASE,
        tenant="t1",
        ruler_namespace="demo",
        ruler_http=http,
        ruler_required=required,
    )


def test_nothing_rendered_is_a_skip_not_a_push() -> None:
    ctx = _ctx(_exploding, rendered={"scrape": [{"kind": "ServiceMonitor"}]})
    assert SyncRules().check(ctx) == "no rules rendered"


def test_a_dry_run_sends_nothing_and_says_what_it_would_push() -> None:
    ctx = _ctx(_exploding, dry_run=True)
    step = SyncRules()
    assert step.check(ctx) is None
    result = step.apply(ctx)
    assert result == f"would push 1 group(s) to {BASE} as tenant t1, namespace demo"


def test_the_dry_run_count_is_the_count_the_real_push_reports() -> None:
    """The number in the preview and the number in the result must be the same quantity,
    or a dry run says one thing and the run that follows it says another."""
    rendered = {"alerts": [_rule_doc("g1")], "slo": [_rule_doc("g1", "g2")]}
    assert SyncRules().apply(_ctx(_exploding, rendered=rendered, dry_run=True)) == (
        f"would push 3 group(s) to {BASE} as tenant t1, namespace demo"
    )
    http = FakeHttp({("POST", NS_URL): (202, ""), ("GET", NS_URL): (200, _listing("g1", "g2"))})
    assert SyncRules().apply(_ctx(http, rendered=rendered)) == "pushed 3 group(s); pruned 0"


def test_an_app_cannot_target_the_platforms_own_ruler_namespace() -> None:
    """`noto-platform` is a legal app name. An app run filed there would prune every
    group it did not render - which is the platform's retention alarm, rendered only by
    `noto platform`. Refused before a single call, in every mode including a dry run."""
    ctx = _ctx(_exploding, dry_run=True)
    ctx.ruler_namespace = "noto-platform"
    with pytest.raises(RulerSyncError, match="the platform's own ruler namespace"):
        SyncRules().apply(ctx)


def test_both_rendered_families_are_pushed_under_the_apps_namespace() -> None:
    """The `slo` family holds Sloth's generated PrometheusRule documents by the time this
    step runs, so an app's SLO alerts reach the ruler with its own alert pack."""
    http = FakeHttp({("POST", NS_URL): (202, ""), ("GET", NS_URL): (200, _listing("g1", "slo-g"))})
    ctx = _ctx(http, rendered={"alerts": [_rule_doc("g1")], "slo": [_rule_doc("slo-g")]})
    assert SyncRules().apply(ctx) == "pushed 2 group(s); pruned 0"
    posts = [(m, u) for m, u, _d, _h in http.calls if m == "POST"]
    assert posts == [("POST", NS_URL), ("POST", NS_URL)]
    assert all(h["X-Scope-OrgID"] == "t1" for _m, _u, _d, h in http.calls)


def test_a_group_the_ruler_holds_and_nothing_renders_is_pruned() -> None:
    http = FakeHttp(
        {
            ("POST", NS_URL): (202, ""),
            ("GET", NS_URL): (200, _listing("g1", "renamed-away")),
            ("DELETE", f"{NS_URL}/renamed-away"): (202, ""),
        }
    )
    ctx = _ctx(http)
    assert SyncRules().apply(ctx) == "pushed 1 group(s); pruned 1 (renamed-away)"
    deletes = [u for m, u, _d, _h in http.calls if m == "DELETE"]
    assert deletes == [f"{NS_URL}/renamed-away"]


def test_every_group_is_pushed_before_anything_is_pruned() -> None:
    """A push that fails half way must never have deleted the groups that were working.
    Order is the whole guarantee: all POSTs, then the listing, then the deletes."""
    http = FakeHttp(
        {
            ("POST", NS_URL): (202, ""),
            ("GET", NS_URL): (200, _listing("g1", "g2", "stale")),
            ("DELETE", f"{NS_URL}/stale"): (202, ""),
        }
    )
    ctx = _ctx(http, rendered={"alerts": [_rule_doc("g1", "g2")]})
    SyncRules().apply(ctx)
    assert [m for m, _u, _d, _h in http.calls] == ["POST", "POST", "GET", "DELETE"]


def test_a_run_for_one_app_issues_no_delete_outside_that_apps_namespace() -> None:
    """The prune is scoped to the app's own ruler namespace and nothing else: another
    app's groups, and the platform's own `noto-platform` group, are untouchable from
    here even when the ruler's answer mentions them."""
    listing = yaml.safe_dump(
        {
            "demo": [{"name": "stale", "rules": []}],
            "other-app": [{"name": "theirs", "rules": []}],
            "noto-platform": [{"name": "noto-platform-retention", "rules": []}],
        }
    )
    http = FakeHttp(
        {
            ("POST", NS_URL): (202, ""),
            ("GET", NS_URL): (200, listing),
            ("DELETE", f"{NS_URL}/stale"): (202, ""),
        }
    )
    SyncRules().apply(_ctx(http))
    for method, url, _data, _headers in http.calls:
        if method == "DELETE":
            assert url.startswith(f"{BASE}/config/v1/rules/demo/")


def test_a_ruler_that_refuses_the_rules_fails_the_step_naming_the_group() -> None:
    http = FakeHttp({("POST", NS_URL): (400, "bad expression")})
    with pytest.raises(RulerSyncError, match="g1"):
        SyncRules().apply(_ctx(http))


def test_an_unreachable_ruler_is_a_note_when_nothing_requires_it() -> None:
    """Operator mode: `observe` is normally run from a laptop that cannot route to the
    in-cluster ruler URL. Everything else in the run succeeded; saying so is right."""

    def unreachable(*_args) -> tuple[int, str]:
        raise RulerUnreachable("Connection refused")

    result = SyncRules().apply(_ctx(unreachable))
    assert result == (
        f"not pushed: {BASE} is unreachable from here (port-forward and use --mimir-url)"
    )


def test_an_unreachable_ruler_fails_the_step_when_the_run_requires_it() -> None:
    """Job mode: the run is INSIDE the cluster, where that URL resolves. Unreachable
    there is a real failure, and nobody is reading a note. The failure names the URL:
    a job's whole output is one summary line, and `Name or service not known` alone
    does not say which name."""

    def unreachable(*_args) -> tuple[int, str]:
        raise RulerUnreachable("Name or service not known")

    with pytest.raises(RulerUnreachable) as raised:
        SyncRules().apply(_ctx(unreachable, required=True))
    assert str(raised.value) == f"ruler {BASE} unreachable: Name or service not known"


def _unreachable_only_on(method: str, http):
    """The ruler answers every verb but one, which nothing can reach."""

    def transport(m: str, url: str, data: bytes | None, headers: dict) -> tuple[int, str]:
        if m == method:
            raise RulerUnreachable("Connection refused")
        return http(m, url, data, headers)

    return transport


def test_a_prune_that_cannot_reach_the_ruler_does_not_claim_the_push_failed() -> None:
    """Push and prune are two trips. If the second one loses the ruler, everything this
    run renders IS in the ruler; reporting `not pushed` would send an operator looking
    for rules that are already there."""
    http = FakeHttp({("POST", NS_URL): (202, "")})
    result = SyncRules().apply(_ctx(_unreachable_only_on("GET", http)))
    assert result == f"pushed 1 group(s); stale groups not pruned: {BASE} became unreachable"


def test_a_prune_that_cannot_reach_the_ruler_still_fails_a_job() -> None:
    """Job mode does not tolerate an unreachable ruler in either half: a stale group left
    evaluating is the thing the prune exists to stop."""
    http = FakeHttp({("POST", NS_URL): (202, "")})
    ctx = _ctx(_unreachable_only_on("GET", http), required=True)
    with pytest.raises(RulerUnreachable, match=BASE):
        SyncRules().apply(ctx)


def test_the_step_refuses_to_guess_a_namespace_it_was_not_given() -> None:
    ctx = _ctx(_exploding)
    ctx.ruler_namespace = None
    with pytest.raises(RuntimeError, match="sync-rules"):
        SyncRules().apply(ctx)
