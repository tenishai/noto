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


class Row(_Frozen):
    """A titled, collapsible group of panels on one dashboard (a Perses grid with a
    display title). `open` is the group's state when the dashboard loads."""

    title: str
    panels: list[str]
    open: bool = True


class Dashboard(_Frozen):
    name: str
    # Either a flat list of panels (one untitled grid, two columns) or titled rows;
    # a dashboard declares one or the other, never both, never neither.
    panels: list[str] = []
    rows: list[Row] = []
    # "app" dashboards render per app (name-prefixed, queries app-scoped); "cluster"
    # dashboards render once, app-less, with cluster-wide queries.
    scope: Literal["app", "cluster"] = "app"
    enabled: bool = True
    # The time range the dashboard opens on, and how often it refreshes itself (Perses
    # duration strings; None leaves auto-refresh off).
    duration: str = "1h"
    refresh: str | None = None

    @model_validator(mode="after")
    def _panels_or_rows(self) -> Dashboard:
        if bool(self.panels) == bool(self.rows):
            raise ValueError("a dashboard declares either panels or rows, and at least one")
        return self

    @property
    def all_panels(self) -> list[str]:
        """Every panel name in layout order, whichever form declared them."""
        if self.rows:
            return [name for row in self.rows for name in row.panels]
        return list(self.panels)


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
    # False for classes whose workload contract has no /metrics endpoint (agent, and
    # gateway until it grows one): suppresses the per-app ServiceMonitor/PodMonitor so
    # the platform never alerts on a scrape the app was never going to answer. The
    # metrics signal stays on - kubeletstats and kube-state-metrics are app-agnostic.
    app_scrape: bool = True
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
    # Named links the app's dashboards carry (name -> URL): where the app is managed,
    # its repository, its chat surface. The provisioner that writes gojira.yaml knows
    # these; noto only shows them, so none is ever hardcoded here.
    links: dict[str, str] | None = None

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
    app_scrape: bool
    budgets: Budgets
    slos: list[SLO]
    dashboards: list[Dashboard]
    drop: list[DropRule]
    log_collection: str
    links: dict[str, str] = {}
