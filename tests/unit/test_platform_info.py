"""The platform writes down what it is, and enforces the retention promise it makes.

`noto platform` records `born` (once, forever), `tenant` and `environment` in the
`noto-platform-info` ConfigMap, passes the profile's retention promise to each backend,
and applies the platform-level alert that catches a window shorter than the promise.
What `observe` then DOES with that record lives in test_observe_tenant.py.
"""

from pathlib import Path

import yaml
from tests.support.fake_cluster import InfoRunner

from noto import cli
from noto.engine.engine import ProvisionEngine
from noto.engine.ledger import Ledger
from noto.steps import (
    EnsurePlatformInfo,
    StepContext,
    read_platform_info,
    read_platform_info_strict,
)

REPO = Path(__file__).parents[2]


def _step_ctx(runner: InfoRunner, **kw) -> StepContext:
    pins = yaml.safe_load((REPO / "helm" / "pins.yaml").read_text())
    return StepContext(runner=runner, pins=pins, repo_root=REPO, **kw)


def _patched_data(runner: InfoRunner) -> dict:
    """The `data` object of the last merge patch the runner was asked to make."""
    import json

    return json.loads(runner.patches[-1][runner.patches[-1].index("-p") + 1])["data"]


def _platform(
    tmp_path: Path, monkeypatch, runner: InfoRunner, *extra: str, cluster: str = "c1"
) -> int:
    monkeypatch.setattr(cli, "real_runner", runner)
    return cli.main(
        [
            "platform",
            "--cluster",
            cluster,
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


def test_the_strict_read_separates_an_absent_record_from_an_unreadable_one() -> None:
    """The write path's read. Absent is a fact worth acting on; anything else is an
    unknown, and an unknown must never be mistaken for an unborn platform."""
    import pytest as _pytest

    from noto.steps import PlatformInfoUnreadable

    assert read_platform_info_strict(_step_ctx(InfoRunner(None))) == {}
    with _pytest.raises(PlatformInfoUnreadable) as exc_info:
        read_platform_info_strict(_step_ctx(InfoRunner(readable=False)))
    assert str(exc_info.value).startswith(
        "refusing to rewrite the platform's birth instant: could not read the record: "
    )
    assert "Forbidden" in str(exc_info.value)


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
    # An already-born platform is never created again, and the one write it does get
    # carries no `born` key at all.
    assert runner.creates == []
    assert _patched_data(runner) == {"tenant": "new", "environment": "prod"}
    assert runner.info == {
        "born": "2020-02-02T02:02:02Z",
        "tenant": "new",
        "environment": "prod",
    }


def test_a_first_run_creates_the_birth_instant_and_then_patches_the_rest(tmp_path: Path) -> None:
    """Create-then-patch, in that order: `kubectl create` cannot overwrite, so the birth
    instant can only ever be written by a platform that had none."""
    runner = InfoRunner(None)
    step = EnsurePlatformInfo(tenant="c1", environment="dev", born="2026-09-17T12:00:00Z")
    ProvisionEngine([step]).run(Ledger(tmp_path / "l.db"), "platform@c1", _step_ctx(runner))
    assert runner.applies == []
    (create,) = runner.creates
    assert create[1:3] == ["create", "configmap"]
    assert create[3] == "noto-platform-info"
    assert "-n" in create and create[create.index("-n") + 1] == "noto"
    assert "--from-literal=born=2026-09-17T12:00:00Z" in create
    # nothing but born rides along in the create call
    assert [a for a in create if a.startswith("--from-literal=")] == [
        "--from-literal=born=2026-09-17T12:00:00Z"
    ]
    (patch,) = runner.patches
    assert "--type" in patch and patch[patch.index("--type") + 1] == "merge"
    assert "born" not in patch[patch.index("-p") + 1]
    assert runner.calls.index((create, None)) < runner.calls.index((patch, None))
    assert runner.info == {
        "born": "2026-09-17T12:00:00Z",
        "tenant": "c1",
        "environment": "dev",
    }


def test_a_create_that_lost_the_race_is_success_not_failure(tmp_path: Path) -> None:
    """Two platform runs at once: the loser's create says AlreadyExists, which is exactly
    the outcome wanted - somebody recorded the birth instant, and it was not overwritten."""

    class RacingRunner(InfoRunner):
        def __call__(self, cmd, stdin_text):
            if cmd[:3] == ["kubectl", "create", "configmap"]:
                self.calls.append((cmd, stdin_text))
                self.info = {"born": "2020-01-01T00:00:00Z"}
                return 1, 'Error from server (AlreadyExists): configmaps "x" already exists'
            return super().__call__(cmd, stdin_text)

    runner = RacingRunner(None)
    step = EnsurePlatformInfo(tenant="c1", environment="dev", born="2099-09-09T09:09:09Z")
    result = ProvisionEngine([step]).run(
        Ledger(tmp_path / "l.db"), "platform@c1", _step_ctx(runner)
    )
    assert result.completed
    assert runner.info["born"] == "2020-01-01T00:00:00Z"


def test_the_step_refuses_to_write_when_the_record_cannot_be_read(tmp_path: Path) -> None:
    runner = InfoRunner(readable=False)
    step = EnsurePlatformInfo(tenant="c1", environment="dev")
    result = ProvisionEngine([step]).run(
        Ledger(tmp_path / "l.db"), "platform@c1", _step_ctx(runner)
    )
    assert not result.completed
    assert "refusing to rewrite the platform's birth instant" in (result.error or "")
    assert runner.creates == [] and runner.patches == [] and runner.applies == []


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
    assert runner.applies == [] and runner.creates == [] and runner.patches == []


def test_the_default_born_is_an_rfc3339_utc_instant() -> None:
    import re

    step = EnsurePlatformInfo(tenant="c1", environment="dev")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", step.born)


# --- the platform command ----------------------------------------------------


def test_platform_records_its_info_before_it_applies_the_platform_rules(
    tmp_path: Path, monkeypatch
) -> None:
    from noto.engine.engine import ProvisionEngine as RealEngine

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
    assert runner.info["tenant"] == "c1"
    assert runner.info["environment"] == "qa"


def test_two_runs_with_different_clusters_keep_born_and_update_the_tenant(
    tmp_path: Path, monkeypatch
) -> None:
    """The regression this whole shape exists for: a second `platform` run (a renamed
    cluster, a re-run after an outage) must move `tenant` and leave the birth instant
    exactly where it was, or the retention alert goes quiet for another window."""
    runner = InfoRunner(None)
    assert _platform(tmp_path, monkeypatch, runner) == 0
    born = runner.info["born"]
    assert born

    assert _platform(tmp_path, monkeypatch, runner, cluster="c2") == 0
    assert runner.info["born"] == born
    assert runner.info["tenant"] == "c2"
    # exactly one create in two runs, and it belonged to the first
    assert len(runner.creates) == 1
    # and the second run's alert still counts its age gate from that first birth instant
    import datetime as _dt

    rules = [
        d for d in runner.applied_docs() if d.get("metadata", {}).get("name") == "noto-platform"
    ]
    expected = int(_dt.datetime.fromisoformat(born).timestamp()) + 30 * 86400
    assert f"vector(time()) > {expected}" in rules[-1]["spec"]["groups"][0]["rules"][0]["expr"]


def test_an_unreadable_record_fails_the_platform_run_before_any_write(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A Forbidden or an apiserver blip is not an unborn platform. The run stops rather
    than stamping a fresh birth instant over a living one."""
    runner = InfoRunner(readable=False)
    assert _platform(tmp_path, monkeypatch, runner) == 1
    err = capsys.readouterr().err
    assert "refusing to rewrite the platform's birth instant: could not read the record" in err
    assert runner.creates == [] and runner.patches == [] and runner.applies == []


def test_platform_applies_the_retention_promise_to_the_pinned_charts(
    tmp_path: Path, monkeypatch
) -> None:
    runner = InfoRunner(None)
    assert _platform(tmp_path, monkeypatch, runner) == 0
    upgrades = {
        c[3]: c for c, _ in runner.calls if c[0] == "helm" and c[1] == "upgrade"
    }  # noto-<component> -> argv
    mimir = " ".join(upgrades["noto-mimir"])
    assert "mimir.structuredConfig.limits.compactor_blocks_retention_period=30d" in mimir
    assert "loki.limits_config.retention_period=7d" in " ".join(upgrades["noto-loki"])
    assert "loki.compactor.retention_enabled=true" in " ".join(upgrades["noto-loki"])
    assert "tempo.retention=168h" in " ".join(upgrades["noto-tempo"])
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
        if d.get("kind") == "PrometheusRule" and d["metadata"]["name"] == "noto-platform"
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
