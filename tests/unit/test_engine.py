"""Step engine + ledger: the load-bearing guarantees (testing.md layer 2).

Idempotency (re-run skips done), resume-from-failure, one ledger row per step per
target_ref, scope independence, ephemeral re-checking. Deterministic: fake clock, tmp DB.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from argus.engine.engine import ProvisionEngine, StepStatus
from argus.engine.ledger import Ledger


def _ledger(tmp_path: Path) -> Ledger:
    ticks = iter(f"2026-07-11T00:00:{i:02d}Z" for i in range(100))
    return Ledger(tmp_path / "argus.db", now_fn=lambda: next(ticks))


@dataclass
class FakeStep:
    name: str
    tooling: str = "cli"
    ephemeral: bool = False
    exists: str | None = None  # check() result
    fail: bool = False
    applied: list[str] = field(default_factory=list)
    checked: int = 0

    def check(self, ctx: object) -> str | None:
        self.checked += 1
        return self.exists

    def apply(self, ctx: object) -> str:
        if self.fail:
            raise RuntimeError(f"{self.name} exploded")
        self.applied.append(self.name)
        return f"ref-{self.name}"


def test_fresh_run_applies_all_and_records(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    steps = [FakeStep("a"), FakeStep("b")]
    result = ProvisionEngine(steps).run(ledger, "bootstrap@c1", ctx=None)
    assert result.completed and result.steps_run == ["a", "b"]
    row = ledger.step_status("bootstrap@c1", "a")
    assert row is not None
    assert row.status == StepStatus.DONE
    assert row.resource_ref == "ref-a"
    assert row.started_at and row.finished_at


def test_second_run_skips_done(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    steps = [FakeStep("a"), FakeStep("b")]
    engine = ProvisionEngine(steps)
    engine.run(ledger, "bootstrap@c1", ctx=None)
    result = engine.run(ledger, "bootstrap@c1", ctx=None)
    assert result.completed and result.steps_run == []
    assert steps[0].applied == ["a"]  # applied exactly once


def test_failure_halts_records_and_resumes(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    a, b, c = FakeStep("a"), FakeStep("b", fail=True), FakeStep("c")
    engine = ProvisionEngine([a, b, c])
    result = engine.run(ledger, "wire@clips", ctx=None)
    assert not result.completed
    assert result.failed_step == "b" and "exploded" in (result.error or "")
    assert ledger.step_status("wire@clips", "b").status == StepStatus.FAILED
    assert ledger.step_status("wire@clips", "c") is None  # never reached
    events = ledger.events("wire@clips")
    assert any(e.type == "step_failed" for e in events)

    b.fail = False
    result = engine.run(ledger, "wire@clips", ctx=None)
    assert result.completed
    assert result.steps_run == ["b", "c"]  # a stayed done; only failed-onward ran
    assert a.applied == ["a"]


def test_check_existing_skips_with_ref(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    step = FakeStep("bucket", exists="gs://already-there")
    result = ProvisionEngine([step]).run(ledger, "storage@us-east4", ctx=None)
    assert result.completed and result.steps_skipped == ["bucket"]
    row = ledger.step_status("storage@us-east4", "bucket")
    assert row.status == StepStatus.SKIPPED
    assert row.resource_ref == "gs://already-there"
    assert step.applied == []


def test_ephemeral_rechecks_despite_done(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    step = FakeStep("workdir", ephemeral=True)
    engine = ProvisionEngine([step])
    engine.run(ledger, "wire@clips", ctx=None)
    engine.run(ledger, "wire@clips", ctx=None)
    assert step.checked == 2  # DONE did not short-circuit the ephemeral step
    assert step.applied == ["workdir", "workdir"]


def test_scopes_are_independent(tmp_path: Path) -> None:
    """The region scope's DONE rows are shared: a second cluster's region run skips them,
    while its own cluster-scope rows start fresh."""
    ledger = _ledger(tmp_path)
    region_step = FakeStep("buckets")
    cluster_step = FakeStep("operators")
    region = ProvisionEngine([region_step])
    cluster = ProvisionEngine([cluster_step])

    region.run(ledger, "storage@us-east4", ctx=None)
    cluster.run(ledger, "bootstrap@cluster-1", ctx=None)
    # second cluster joins the same region
    r = region.run(ledger, "storage@us-east4", ctx=None)
    c = cluster.run(ledger, "bootstrap@cluster-2", ctx=None)

    assert r.steps_run == []  # region work not repeated
    assert c.steps_run == ["operators"]  # cluster work runs for the new cluster
    assert region_step.applied == ["buckets"]


def test_duplicate_step_names_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ProvisionEngine([FakeStep("a"), FakeStep("a")])
