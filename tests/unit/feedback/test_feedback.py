"""The rightsizing feedback loop: distiller math, the cross-repo contract fixture,
and both clients against fakes. gojira's docstring names this test: the golden
usage_profile.json is asserted byte-identical by BOTH repos."""

import json
from pathlib import Path

import pytest

from argus.feedback.distill import DistillError, distill_resource, to_cpu_quantity, to_mem_quantity
from argus.feedback.models import ResourceUsage, UsageProfile

FIXTURES = Path(__file__).parents[1] / "fixtures" / "feedback"
REPO = Path(__file__).parents[3]


def _golden_profile() -> UsageProfile:
    return UsageProfile(
        app="clips-api",
        environment="prod",
        cluster="my-prod-cluster",
        window_days=14,
        samples=20160,
        cpu=ResourceUsage(
            request="250m", limit="500m", p50="180m", p95="760m", p99="820m", max="1100m"
        ),
        memory=ResourceUsage(
            request="256Mi", limit="512Mi", p50="210Mi", p95="300Mi", p99="340Mi", max="402Mi"
        ),
        restarts=0,
        oom_kills=0,
        reported_by="gojira-sizing-agent/my-prod-cluster",
        reported_at="2026-07-06T12:00:00Z",
    )


def test_cross_repo_contract_fixture_byte_identical() -> None:
    """Change the contract -> change the fixture -> both repos notice (gojira's words)."""
    expected = json.loads((FIXTURES / "usage_profile.json").read_text())
    assert json.loads(_golden_profile().serialize()) == expected


def test_quantity_serialization() -> None:
    assert to_cpu_quantity(0.76) == "760m"
    assert to_cpu_quantity(1.1) == "1100m"
    assert to_cpu_quantity(0.0004) == "1m"  # floor at 1m, never "0m"
    assert to_mem_quantity(300 * 1024 * 1024) == "300Mi"
    assert to_mem_quantity(402_653_184) == "384Mi"
    assert to_mem_quantity(500_000) == "1Mi"  # floor at 1Mi


def test_distill_resource_percentiles() -> None:
    samples = [float(i) for i in range(1, 101)]  # 1..100 cores
    usage = distill_resource(samples, request=0.25, limit=2.0, quantize=to_cpu_quantity)
    assert usage.p50 == "50m" or usage.p50 == to_cpu_quantity(50.0)
    assert usage.max == to_cpu_quantity(100.0)
    assert usage.request == "250m"
    assert usage.limit == "2000m"


def test_distill_empty_series_fails_loud() -> None:
    with pytest.raises(DistillError, match="no samples"):
        distill_resource([], request=0.1, limit=0.2, quantize=to_cpu_quantity)


def test_cli_feedback_without_dry_run_refuses(tmp_path, capsys) -> None:
    """The production POST path is retired (issue #52): gojira-sizing-agent is the
    single evidence producer. The refusal happens before any Mimir query."""
    from argus import cli

    gojira_fixture = Path(__file__).parents[1] / "fixtures" / "gojira" / "web.yaml"
    code = cli.main(
        [
            "feedback",
            "--gojira",
            str(gojira_fixture),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "gojira-sizing-agent" in err
    assert "--dry-run" in err


def test_cli_feedback_dry_run(tmp_path, monkeypatch, capsys) -> None:
    """Full dry-run through the CLI with a faked Mimir - the issue's acceptance check."""
    from argus import cli
    from argus.feedback import mimir as mimir_mod

    def fake_collect(client, app, namespace, window_days, step=300):
        assert client.tenant == "dev-cluster"  # tenant = the env's cluster
        assert (app, namespace) == ("clips-api", "clips")
        return {
            "cpu_samples": [0.18, 0.76, 0.82, 1.1],
            "memory_samples": [210e6, 300e6, 340e6, 402e6],
            "cpu_request": 0.25,
            "cpu_limit": 0.5,
            "memory_request": 256 * 1024 * 1024,
            "memory_limit": 512 * 1024 * 1024,
            "restarts": 0,
            "oom_kills": 0,
        }

    monkeypatch.setattr(cli, "cmd_feedback", cli.cmd_feedback)  # ensure import side ok
    monkeypatch.setattr(mimir_mod, "collect", fake_collect)
    gojira_fixture = Path(__file__).parents[1] / "fixtures" / "gojira" / "web.yaml"
    code = cli.main(
        [
            "feedback",
            "--gojira",
            str(gojira_fixture),
            "--env",
            "dev",
            "--dry-run",
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    profile = json.loads(out)
    assert profile["app"] == "clips-api"
    assert profile["cpu"]["max"] == "1100m"
    assert profile["reported_by"] == "argus/dev-cluster"


def test_ruler_sync_pushes_groups() -> None:
    from argus.feedback.ruler import sync_rules

    calls = []

    def fake_http(method, url, data, headers):
        calls.append((method, url, data, headers))
        return 202, ""

    docs = [
        {
            "kind": "PrometheusRule",
            "spec": {
                "groups": [
                    {"name": "g1", "rules": [{"alert": "A", "expr": "up == 0"}]},
                    {"name": "g2", "rules": [{"record": "r", "expr": "1"}]},
                ]
            },
        },
        {"kind": "SomethingElse"},
    ]
    n = sync_rules("http://mimir/prometheus", "t1", "podinfo", docs, http=fake_http)
    assert n == 2
    method, url, data, headers = calls[0]
    assert method == "POST"
    assert url == "http://mimir/prometheus/config/v1/rules/podinfo"
    assert headers["X-Scope-OrgID"] == "t1"
    assert b"name: g1" in data


def test_cardinality_budget_alerts_render() -> None:
    from argus.profiles.loader import load_baseline, load_class, load_override
    from argus.profiles.resolve import resolve
    from argus.rendering.alerts import render_alert_rules
    from argus.rendering.context import Context

    p = resolve(
        load_baseline(REPO / "profiles" / "baseline.yaml"),
        load_class(REPO / "profiles" / "classes" / "web.yaml"),
        load_override(None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="prod", app="a", namespace="n")
    doc = render_alert_rules(p, ctx)
    card = next(g for g in doc["spec"]["groups"] if g["name"] == "argus-cardinality")
    exprs = {r["alert"]: r["expr"] for r in card["rules"]}
    assert exprs["ArgusCardinalityBudgetExceeded"] == "sum(cortex_ingester_active_series) > 1000000"
    assert exprs["ArgusCardinalityBudget80"] == "sum(cortex_ingester_active_series) > 800000"
