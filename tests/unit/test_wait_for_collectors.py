"""WaitForCollectors: a fresh `noto platform` run must not race the operator turning
OpenTelemetryCollector CRs into workloads. The operator needs a beat to create the
DaemonSet/Deployment/StatefulSet and pull images, so Pending/ContainerCreating right
after the collectors are applied is expected, not a failure - unlike ValidateRoundTrip,
which fails on any non-Running/Succeeded pod.

collector_workloads() is the pure mapping from rendered CRs to the workloads the
OpenTelemetry operator creates for them (verified live-cluster naming, see the
docstring on the function itself); WaitForCollectors polls kubectl rollout status for
each one, tolerating NotFound while the operator catches up.
"""

import pytest

from noto import steps as steps_mod
from noto.steps import StepContext, WaitForCollectors, collector_workloads


class FakeRunner:
    """Replays one (code, out) pair per call, in order; records every call."""

    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], stdin_text: str | None) -> tuple[int, str]:
        self.calls.append(cmd)
        return self.responses.pop(0)


def _ctx(runner, rendered=None, dry_run=False, kube_context=None) -> StepContext:
    return StepContext(
        runner=runner, rendered=rendered or {}, dry_run=dry_run, kube_context=kube_context
    )


# ---------------------------------------------------------------------------
# collector_workloads: pure mapping, no cluster involved
# ---------------------------------------------------------------------------


def _otel_cr(
    name: str, mode: str | None, target_allocator: bool = False, namespace: str = "noto"
) -> dict:
    spec: dict = {}
    if mode is not None:
        spec["mode"] = mode
    if target_allocator:
        spec["targetAllocator"] = {"enabled": True}
    return {
        "apiVersion": "opentelemetry.io/v1beta1",
        "kind": "OpenTelemetryCollector",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


def test_daemonset_mode_maps_to_daemonset_workload():
    docs = [_otel_cr("noto-agent", "daemonset")]
    assert collector_workloads(docs) == [("DaemonSet", "noto-agent-collector", "noto")]


def test_deployment_mode_maps_to_deployment_workload():
    docs = [_otel_cr("noto-gateway", "deployment")]
    assert collector_workloads(docs) == [("Deployment", "noto-gateway-collector", "noto")]


def test_statefulset_mode_maps_to_statefulset_workload():
    docs = [_otel_cr("noto-scraper", "statefulset")]
    assert collector_workloads(docs) == [("StatefulSet", "noto-scraper-collector", "noto")]


def test_missing_mode_defaults_to_deployment():
    """The OpenTelemetry operator defaults spec.mode to deployment when absent."""
    docs = [_otel_cr("noto-gateway", None)]
    assert collector_workloads(docs) == [("Deployment", "noto-gateway-collector", "noto")]


def test_target_allocator_enabled_adds_its_own_deployment():
    docs = [_otel_cr("noto-scraper", "statefulset", target_allocator=True)]
    assert collector_workloads(docs) == [
        ("StatefulSet", "noto-scraper-collector", "noto"),
        ("Deployment", "noto-scraper-targetallocator", "noto"),
    ]


def test_non_collector_documents_are_ignored():
    docs = [
        {"kind": "PriorityClass", "metadata": {"name": "noto-node-critical"}},
        {"kind": "ServiceAccount", "metadata": {"name": "noto-targetallocator"}},
        _otel_cr("noto-agent", "daemonset"),
    ]
    assert collector_workloads(docs) == [("DaemonSet", "noto-agent-collector", "noto")]


def test_the_real_rendered_platform_tier_yields_four_workloads():
    """End-to-end sanity against the actual renderers: agent (daemonset), gateway
    (deployment), scraper (statefulset + its own targetallocator deployment)."""
    from noto import cli as cli_mod
    from noto.integrations.catalog import load_catalog
    from noto.rendering.collectors import render_agent, render_gateway, render_targetallocator
    from noto.rendering.context import Context

    ctx = Context(topology="solo", cluster="c1", environment="dev")
    p = cli_mod._platform_profile()
    docs = [
        render_agent(p, ctx, catalog=load_catalog(cli_mod.REPO_ROOT / "integrations")),
        render_gateway(p, ctx),
        render_targetallocator(p, ctx),
    ]
    workloads = collector_workloads(docs)
    assert len(workloads) == 4
    assert ("DaemonSet", "noto-agent-collector", "noto") in workloads
    assert ("Deployment", "noto-gateway-collector", "noto") in workloads
    assert ("StatefulSet", "noto-scraper-collector", "noto") in workloads
    assert ("Deployment", "noto-scraper-targetallocator", "noto") in workloads


# ---------------------------------------------------------------------------
# WaitForCollectors.apply
# ---------------------------------------------------------------------------


def test_dry_run_calls_nothing():
    runner = FakeRunner([])
    rendered = {"collectors": [_otel_cr("noto-agent", "daemonset")]}
    ctx = _ctx(runner, rendered=rendered, dry_run=True)
    msg = WaitForCollectors().apply(ctx)
    assert runner.calls == []
    assert "noto-agent-collector" in msg or "1" in msg


def test_success_path_calls_runner_once_per_workload_with_kube_context(monkeypatch):
    sleeps = []
    monkeypatch.setattr(steps_mod, "_sleep", lambda s: sleeps.append(s))
    rendered = {
        "collectors": [
            _otel_cr("noto-agent", "daemonset"),
            _otel_cr("noto-gateway", "deployment"),
        ]
    }
    runner = FakeRunner([(0, "ok"), (0, "ok")])
    ctx = _ctx(runner, rendered=rendered, kube_context="kind-noto-smoke")
    msg = WaitForCollectors().apply(ctx)
    assert sleeps == []
    assert len(runner.calls) == 2
    for cmd in runner.calls:
        assert cmd[:3] == ["kubectl", "--context", "kind-noto-smoke"]
        assert "rollout" in cmd and "status" in cmd
        assert "-n" in cmd and "noto" in cmd
    first = runner.calls[0]
    assert first[first.index("rollout") + 2] == "DaemonSet/noto-agent-collector"
    assert msg == "collectors ready: 2 workloads"


def test_not_found_twice_then_success_sleeps_twice_never_really(monkeypatch):
    sleeps = []
    monkeypatch.setattr(steps_mod, "_sleep", lambda s: sleeps.append(s))
    rendered = {"collectors": [_otel_cr("noto-agent", "daemonset")]}
    not_found = (
        1,
        'Error from server (NotFound): daemonsets.apps "noto-agent-collector" not found',
    )
    rolled_out = (0, 'daemon set "noto-agent-collector" rolled out')
    runner = FakeRunner([not_found, not_found, rolled_out])
    ctx = _ctx(runner, rendered=rendered)
    msg = WaitForCollectors().apply(ctx)
    assert len(runner.calls) == 3
    assert sleeps == [3, 3]
    assert msg == "collectors ready: 1 workloads"


def _never_sleep(_seconds: float) -> None:
    raise AssertionError("slept")


def test_non_not_found_failure_raises_immediately_naming_the_workload(monkeypatch):
    monkeypatch.setattr(steps_mod, "_sleep", _never_sleep)
    rendered = {"collectors": [_otel_cr("noto-gateway", "deployment")]}
    runner = FakeRunner([(1, "connection refused" + "x" * 600)])
    ctx = _ctx(runner, rendered=rendered)
    with pytest.raises(RuntimeError) as exc:
        WaitForCollectors().apply(ctx)
    msg = str(exc.value)
    assert "noto-gateway-collector" in msg
    assert "\n" not in msg
    assert len(msg) < 700  # last 500 chars of output, collapsed


def test_deadline_exceeded_raises_naming_the_workload(monkeypatch):
    """The clock and sleep are both injectable so this never really sleeps."""
    clock = {"t": 0.0}

    def fake_now():
        return clock["t"]

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(steps_mod, "_now", fake_now)
    monkeypatch.setattr(steps_mod, "_sleep", fake_sleep)
    rendered = {"collectors": [_otel_cr("noto-agent", "daemonset")]}
    not_found = (1, 'Error (NotFound): daemonsets.apps "noto-agent-collector" not found')
    # Always NotFound; the deadline (5s, 3s sleeps) must be exceeded and raise.
    runner = FakeRunner([not_found] * 10)
    ctx = _ctx(runner, rendered=rendered)
    step = WaitForCollectors(timeout=5.0)
    with pytest.raises(RuntimeError) as exc:
        step.apply(ctx)
    assert "noto-agent-collector" in str(exc.value)


def test_check_always_returns_none_never_ledger_skips():
    assert WaitForCollectors().check(_ctx(FakeRunner([]))) is None


def test_no_collectors_rendered_waits_for_nothing():
    runner = FakeRunner([])
    ctx = _ctx(runner, rendered={})
    msg = WaitForCollectors().apply(ctx)
    assert runner.calls == []
    assert "0 workloads" in msg
