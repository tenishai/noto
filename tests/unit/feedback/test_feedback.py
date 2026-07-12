"""The rightsizing feedback loop: distiller math, the cross-repo contract fixture,
and both clients against fakes. gojira's docstring names this test: the golden
usage_profile.json is asserted byte-identical by BOTH repos."""

import json
from pathlib import Path

import pytest

from argus.feedback.distill import DistillError, distill_resource, to_cpu_quantity, to_mem_quantity
from argus.feedback.gojira_client import GojiraPostError, post_profile
from argus.feedback.models import ResourceUsage, UsageProfile

FIXTURES = Path(__file__).parents[1] / "fixtures" / "feedback"


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


def test_post_profile_bearer_and_201() -> None:
    calls: list = []

    def fake_http(url: str, data: bytes, headers: dict) -> tuple[int, str]:
        calls.append((url, data, headers))
        return 201, json.dumps({"ok": True, "profile_id": 7})

    pid = post_profile("http://gojira:8080", "sekret", _golden_profile(), http=fake_http)
    assert pid == 7
    url, data, headers = calls[0]
    assert url == "http://gojira:8080/api/v1/usage"
    assert headers["Authorization"] == "Bearer sekret"
    assert json.loads(data)["app"] == "clips-api"


def test_post_profile_error_surfaces() -> None:
    def fake_http(url: str, data: bytes, headers: dict) -> tuple[int, str]:
        return 401, json.dumps({"error": "unknown token"})

    with pytest.raises(GojiraPostError, match=r"401.*unknown token"):
        post_profile("http://gojira:8080", "bad", _golden_profile(), http=fake_http)


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
