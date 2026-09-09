"""Profile schema - the three layers (baseline, class, override) and the resolved result.

Baseline/class files are ours: unknown fields are errors. The override block rides in
gojira.yaml and evolves with gojira's contract: unknown fields warn and are retained.
"""

import warnings
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Stage = Literal["agent", "gateway", "ingest"]
Signal = Literal["metrics", "logs", "traces"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DropRule(_Frozen):
    name: str
    stage: Stage
    enabled: bool = True
    log_level_below: str | None = None
    path_matches: list[str] | None = None
    metric_names: list[str] | None = None
    metric_labels_strip: list[str] | None = None
    namespace_exclude: list[str] | None = None
    raw_ottl: dict[Literal["logs", "metrics", "traces"], list[str]] | None = None
    justification: str | None = None

    @model_validator(mode="after")
    def _raw_ottl_needs_justification(self) -> DropRule:
        if self.raw_ottl is not None and not self.justification:
            raise ValueError("raw_ottl requires a justification (same contract as overrides)")
        return self


class SLO(_Frozen):
    name: str
    objective: float
    signal: Literal["availability", "latency"]
    latency_threshold: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _latency_needs_threshold(self) -> SLO:
        if self.signal == "latency" and not self.latency_threshold:
            raise ValueError("latency SLO requires latency_threshold (e.g. '500ms')")
        return self


class Dashboard(_Frozen):
    name: str
    panels: list[str]
    enabled: bool = True


class Budgets(_Frozen):
    retention: dict[str, str]
    cardinality: dict[str, int]


class BaselineProfile(_Frozen):
    budgets: Budgets
    drop: list[DropRule] = []
    dashboards: list[Dashboard] = []
    log_collection: Literal["filelog"] = "filelog"


class ClassProfile(_Frozen):
    class_name: Literal["web", "worker", "cron", "heavy", "gpu", "agent", "gateway"] = Field(
        alias="class"
    )
    signals: list[Signal]
    slos: list[SLO] = []
    dashboards: list[Dashboard] = []
    drop: list[DropRule] = []


class Override(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    profile: str | None = None
    traces: bool | None = None
    retention: dict[str, str] | None = None
    slos: list[SLO] | None = None
    drop: list[DropRule] | None = None
    cardinality_budget: int | None = None
    justification: str | None = None

    @model_validator(mode="after")
    def _warn_unknown_and_check_justification(self) -> Override:
        for key in self.model_extra or {}:
            warnings.warn(
                f"unknown override field {key!r} (retained; gojira contract may be newer)",
                UserWarning,
                stacklevel=2,
            )
        if self.cardinality_budget is not None and not self.justification:
            raise ValueError("cardinality_budget override requires a justification")
        return self


class ResolvedProfile(_Frozen):
    class_name: str
    signals: list[Signal]
    budgets: Budgets
    slos: list[SLO]
    dashboards: list[Dashboard]
    drop: list[DropRule]
    log_collection: str
