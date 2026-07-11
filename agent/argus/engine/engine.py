"""The step engine: check() -> apply() -> record, resumable, scope-aware.

Ported from gojira's proven provisioner shape. The engine is stateless with respect
to run lifecycle - it walks one ordered step sequence for one target_ref, skipping
rows the ledger says are done (unless the step is ephemeral), and halting on the
first failure so a re-run resumes exactly there.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from argus.engine.ledger import Ledger


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@runtime_checkable
class Step(Protocol):
    """An idempotent provisioning step.

    `ephemeral = True` means the resource does not outlive the run (a temp worktree,
    rendered files): a DONE ledger row never skips it - check() re-decides against the
    real world every run. Cloud/cluster steps stay ledger-skipped.
    """

    name: str
    tooling: str

    def check(self, ctx: object) -> str | None: ...

    def apply(self, ctx: object) -> str: ...


@dataclass
class RunResult:
    completed: bool
    steps_run: list[str] = field(default_factory=list)
    steps_skipped: list[str] = field(default_factory=list)
    failed_step: str | None = None
    error: str | None = None


class ProvisionEngine:
    def __init__(self, steps: list[Step]) -> None:
        names = [s.name for s in steps]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate step names in sequence: {names}")
        self.steps = list(steps)

    def run(self, ledger: Ledger, target_ref: str, ctx: object) -> RunResult:
        result = RunResult(completed=False)

        for step in self.steps:
            row = ledger.step_status(target_ref, step.name)
            ephemeral = getattr(step, "ephemeral", False)
            if (
                row is not None
                and row.status in (StepStatus.DONE, StepStatus.SKIPPED)
                and not ephemeral
            ):
                continue

            ledger.start_step(target_ref, step.name, step.tooling)
            try:
                existing = step.check(ctx)
                if existing is not None:
                    ledger.finish_step(
                        target_ref, step.name, StepStatus.SKIPPED, resource_ref=existing
                    )
                    result.steps_skipped.append(step.name)
                    continue
                ref = step.apply(ctx)
                ledger.finish_step(target_ref, step.name, StepStatus.DONE, resource_ref=ref)
                result.steps_run.append(step.name)
            except Exception as e:
                ledger.finish_step(target_ref, step.name, StepStatus.FAILED, error=str(e))
                ledger.record_event(target_ref, "step_failed", {"step": step.name, "error": str(e)})
                result.failed_step = step.name
                result.error = str(e)
                return result

        result.completed = True
        return result
