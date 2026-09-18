"""The platform writes down what it is, and enforces the retention promise it makes.

`argus platform` records `born` (once, forever), `tenant` and `environment` in the
`argus-platform-info` ConfigMap, passes the profile's retention promise to each backend,
and applies the platform-level alert that catches a window shorter than the promise.
What `observe` then DOES with that record lives in test_observe_tenant.py.
"""

from pathlib import Path

import yaml
from tests.support.fake_cluster import InfoRunner

from argus import cli
from argus.engine.engine import ProvisionEngine
from argus.engine.ledger import Ledger
from argus.steps import EnsurePlatformInfo, StepContext, read_platform_info

REPO = Path(__file__).parents[2]


def _step_ctx(runner: InfoRunner, **kw) -> StepContext:
    pins = yaml.safe_load((REPO / "helm" / "pins.yaml").read_text())
    return StepContext(runner=runner, pins=pins, repo_root=REPO, **kw)


def _platform(tmp_path: Path, monkeypatch, runner: InfoRunner, *extra: str) -> int:
    monkeypatch.setattr(cli, "real_runner", runner)
    return cli.main(
        [
            "platform",
            "--cluster",
            "c1",
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            *extra,
        ]
    )


# --- reading the record ------------------------------------------------------


def test_read_platform_info_returns_the_recorded_data() -> None:
    runner = InfoRunner({"born": "2026-01-01T00:00:00Z", "tenant": "real", "environment": "dev"})
    assert read_platform_info(_step_ctx(runner))["tenant"] == "real"
    cmd = runner.calls[0][0]
    assert cmd[:2] == ["kubectl", "get"]
    assert "-o" in cmd and "json" in cmd


def test_read_platform_info_is_empty_when_the_record_cannot_be_read() -> None:
    """An older platform (no ConfigMap), a dry run with no cluster, a job whose RBAC was
    not rolled out: all the same answer, and never an exception."""
    assert read_platform_info(_step_ctx(InfoRunner(None))) == {}
    assert read_platform_info(_step_ctx(InfoRunner(readable=False))) == {}


# --- writing the record ------------------------------------------------------


def test_born_is_preserved_while_tenant_and_environment_update(tmp_path: Path) -> None:
    """`born` describes the birth and is written once; tenant and environment describe
    the CURRENT platform, so re-running `platform` with new values updates them."""
    runner = InfoRunner({"born": "2020-02-02T02:02:02Z", "tenant": "old", "environment": "qa"})
    step = EnsurePlatformInfo(tenant="new", environment="prod", born="2099-09-09T09:09:09Z")
    result = ProvisionEngine([step]).run(
        Ledger(tmp_path / "l.db"), "platform@new", _step_ctx(runner)
    )
    assert result.completed
    doc = runner.applied_docs()[0]
    assert doc["data"] == {
        "born": "2020-02-02T02:02:02Z",
        "tenant": "new",
        "environment": "prod",
    }


def test_a_first_run_records_the_birth_instant_it_was_given(tmp_path: Path) -> None:
    runner = InfoRunner(None)
    step = EnsurePlatformInfo(tenant="c1", environment="dev", born="2026-09-17T12:00:00Z")
    ProvisionEngine([step]).run(Ledger(tmp_path / "l.db"), "platform@c1", _step_ctx(runner))
    doc = runner.applied_docs()[0]
    assert doc["kind"] == "ConfigMap"
    assert doc["metadata"]["name"] == "argus-platform-info"
    assert doc["metadata"]["namespace"] == "argus"
    assert doc["data"]["born"] == "2026-09-17T12:00:00Z"


def test_an_unchanged_record_is_skipped(tmp_path: Path) -> None:
    runner = InfoRunner({"born": "2026-01-01T00:00:00Z", "tenant": "c1", "environment": "dev"})
    step = EnsurePlatformInfo(tenant="c1", environment="dev")
    result = ProvisionEngine([step]).run(
        Ledger(tmp_path / "l.db"), "platform@c1", _step_ctx(runner)
    )
    assert result.steps_skipped == ["ensure-platform-info"]
    assert runner.applies == []


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    runner = InfoRunner(None)
    step = EnsurePlatformInfo(tenant="c1", environment="dev")
    result = ProvisionEngine([step]).run(
        Ledger(tmp_path / "l.db"), "platform@c1", _step_ctx(runner, dry_run=True)
    )
    assert result.completed
    assert runner.applies == []


def test_the_default_born_is_an_rfc3339_utc_instant() -> None:
    import re

    step = EnsurePlatformInfo(tenant="c1", environment="dev")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", step.born)


# --- the platform command ----------------------------------------------------


def test_platform_records_its_info_before_it_applies_the_platform_rules(
    tmp_path: Path, monkeypatch
) -> None:
    from argus.engine.engine import ProvisionEngine as RealEngine

    captured: list[list[str]] = []

    class SpyEngine(RealEngine):
        def __init__(self, steps):
            captured.append([s.name for s in steps])
            super().__init__(steps)

    monkeypatch.setattr(cli, "ProvisionEngine", SpyEngine)
    assert _platform(tmp_path, monkeypatch, InfoRunner(None)) == 0
    names = captured[0]
    assert names.index("namespace") < names.index("ensure-platform-info")
    assert names.index("ensure-platform-info") < names.index("apply_platform-rules")
    # the PrometheusRule needs the operator's CRD, which arrives with a helm step
    helm_steps = [i for i, n in enumerate(names) if n.startswith("helm_")]
    assert names.index("apply_platform-rules") > max(helm_steps)


def test_platform_records_the_cluster_it_was_given_as_the_tenant(
    tmp_path: Path, monkeypatch
) -> None:
    runner = InfoRunner(None)
    monkeypatch.setattr(cli, "real_runner", runner)
    code = cli.main(
        ["platform", "--cluster", "c1", "--env", "qa", "--ledger", str(tmp_path / "l.db")]
    )
    assert code == 0
    record = next(d for d in runner.applied_docs() if d.get("kind") == "ConfigMap")
    assert record["data"]["tenant"] == "c1"
    assert record["data"]["environment"] == "qa"


def test_platform_applies_the_retention_promise_to_the_pinned_charts(
    tmp_path: Path, monkeypatch
) -> None:
    runner = InfoRunner(None)
    assert _platform(tmp_path, monkeypatch, runner) == 0
    upgrades = {
        c[3]: c for c, _ in runner.calls if c[0] == "helm" and c[1] == "upgrade"
    }  # argus-<component> -> argv
    mimir = " ".join(upgrades["argus-mimir"])
    assert "mimir.structuredConfig.limits.compactor_blocks_retention_period=30d" in mimir
    assert "loki.limits_config.retention_period=7d" in " ".join(upgrades["argus-loki"])
    assert "loki.compactor.retention_enabled=true" in " ".join(upgrades["argus-loki"])
    assert "tempo.retention=168h" in " ".join(upgrades["argus-tempo"])
    # the cardinality budget still reaches mimir alongside it
    assert "mimir.structuredConfig.limits.max_global_series_per_user=200000" in mimir


def test_platform_says_platform_rules_need_the_ruler(tmp_path: Path, monkeypatch, capsys) -> None:
    """PrometheusRule objects are inert in this stack (the operator ships without a
    Prometheus); the run must say so rather than leave a rule nobody evaluates."""
    assert _platform(tmp_path, monkeypatch, InfoRunner(None)) == 0
    out = capsys.readouterr().out
    assert "ruler" in out
    assert "rules-sync" in out


def test_platform_rules_carry_the_recorded_born(tmp_path: Path, monkeypatch) -> None:
    """The alert's age gate is born + the promise, so it must count from the SAME birth
    instant the record holds, not from a fresh one on every run."""
    runner = InfoRunner({"born": "2026-01-01T00:00:00Z", "tenant": "c1", "environment": "dev"})
    assert _platform(tmp_path, monkeypatch, runner) == 0
    rule_doc = next(
        d
        for d in runner.applied_docs()
        if d.get("kind") == "PrometheusRule" and d["metadata"]["name"] == "argus-platform"
    )
    expr = rule_doc["spec"]["groups"][0]["rules"][0]["expr"]
    # 2026-01-01T00:00:00Z = 1767225600; + 30d = 1769817600
    assert "vector(time()) > 1769817600" in expr


def test_a_corrupt_born_does_not_stop_the_platform(tmp_path: Path, monkeypatch, capsys) -> None:
    runner = InfoRunner({"born": "not-a-timestamp", "tenant": "c1", "environment": "dev"})
    assert _platform(tmp_path, monkeypatch, runner) == 0
    assert "born" in capsys.readouterr().err


def test_platform_dry_run_completes_with_no_record_and_no_cluster(
    tmp_path: Path, monkeypatch
) -> None:
    assert _platform(tmp_path, monkeypatch, InfoRunner(None), "--dry-run") == 0
