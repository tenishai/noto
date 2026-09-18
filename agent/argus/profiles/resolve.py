"""Resolve the three profile layers into the frozen shape renderers consume.

Semantics (spec): scalars last-wins; named collections (slos, dashboards, drop) merge
by name with later layers replacing same-name items; enabled: false filters an item
out of the resolved lists; inputs are never mutated (all models are frozen).
"""

from argus.profiles.models import (
    BaselineProfile,
    Budgets,
    ClassProfile,
    Override,
    ResolvedProfile,
)


def _merge_named(*layers: list) -> list:
    merged: dict[str, object] = {}
    for layer in layers:
        for item in layer or []:
            merged[item.name] = item
    return [item for item in merged.values() if item.enabled]


def resolve(baseline: BaselineProfile, cls: ClassProfile, override: Override) -> ResolvedProfile:
    signals = list(cls.signals)
    if override.traces and "traces" not in signals:
        signals.append("traces")

    retention = dict(baseline.budgets.retention)
    if override.retention:
        retention.update(override.retention)

    cardinality = dict(baseline.budgets.cardinality)
    if override.cardinality_budget is not None:
        cardinality = {env: override.cardinality_budget for env in cardinality}

    return ResolvedProfile(
        class_name=cls.class_name,
        signals=signals,
        app_scrape=cls.app_scrape,
        budgets=Budgets(retention=retention, cardinality=cardinality),
        slos=_merge_named(cls.slos, override.slos),
        dashboards=_merge_named(baseline.dashboards, cls.dashboards),
        drop=_merge_named(baseline.drop, cls.drop, override.drop),
        log_collection=baseline.log_collection,
    )
