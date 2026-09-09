"""The shipped opinions load and resolve. This is the smoke over profiles/ itself."""

from pathlib import Path

import pytest

from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.resolve import resolve

REPO = Path(__file__).parents[2]
PROFILES = REPO / "profiles"
CLASSES = ["web", "worker", "cron", "heavy", "gpu", "agent", "gateway"]


@pytest.mark.parametrize("class_name", CLASSES)
def test_every_class_loads_and_resolves(class_name: str) -> None:
    baseline = load_baseline(PROFILES / "baseline.yaml")
    cls = load_class(PROFILES / "classes" / f"{class_name}.yaml")
    p = resolve(baseline, cls, load_override(None))
    assert p.class_name == class_name
    assert "metrics" in p.signals
    assert p.dashboards, "every class ships at least the baseline dashboard"


def test_web_resolved_content() -> None:
    baseline = load_baseline(PROFILES / "baseline.yaml")
    cls = load_class(PROFILES / "classes" / "web.yaml")
    p = resolve(baseline, cls, load_override(None))
    slo_names = {s.name for s in p.slos}
    assert {"availability-99.9", "latency-p99-500ms"} <= slo_names
    drop_names = {r.name for r in p.drop}
    assert {
        "health-endpoints",
        "debug-logs",
        "ksm-churn",
        "unbounded-labels",
        "kube-system-noise",
    } <= drop_names


def _resolved(class_name: str):
    baseline = load_baseline(PROFILES / "baseline.yaml")
    cls = load_class(PROFILES / "classes" / f"{class_name}.yaml")
    return resolve(baseline, cls, load_override(None))


@pytest.mark.parametrize("class_name", ["agent", "gateway"])
def test_no_metrics_contract_classes_suppress_app_scrape(class_name: str) -> None:
    assert _resolved(class_name).app_scrape is False


@pytest.mark.parametrize("class_name", ["web", "worker", "cron", "heavy", "gpu"])
def test_scraping_classes_keep_app_scrape(class_name: str) -> None:
    assert _resolved(class_name).app_scrape is True
