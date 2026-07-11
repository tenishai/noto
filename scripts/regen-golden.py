#!/usr/bin/env python3
"""Regenerate the contract golden files from the REAL shipped profiles.

Run via `make regen-golden`. The resulting diff is reviewed in the PR - that review
IS the mechanism by which opinion changes become deliberate. Never regenerate
automatically.
"""

import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agent"))

from argus.profiles.loader import load_baseline, load_class, load_override  # noqa: E402
from argus.profiles.resolve import resolve  # noqa: E402
from argus.rendering.alerts import render_alert_rules  # noqa: E402
from argus.rendering.collectors import (  # noqa: E402
    render_agent,
    render_gateway,
    render_ingest,
    render_targetallocator,
)
from argus.rendering.context import Context  # noqa: E402
from argus.rendering.perses import render_perses  # noqa: E402
from argus.rendering.scrape import render_podmonitor, render_servicemonitor  # noqa: E402
from argus.rendering.slo import render_sloth  # noqa: E402

GOLDEN = REPO / "tests" / "contract" / "golden"
PROFILES = REPO / "profiles"
CLASSES = ["web", "worker", "cron", "heavy", "gpu"]
# Goldens are the HUMAN-review artifact: one representative class, both topologies.
# All-class coverage lives in the contract invariants (cheap, no churn).
GOLDEN_CLASSES = ["web"]
TOPOLOGIES = ["solo", "regional"]

REGIONAL_ENDPOINTS = {
    "mimir_otlp_http": "http://mimir.us-east4.argus.internal:9009/otlp",
    "loki_otlp_http": "http://loki.us-east4.argus.internal:3100/otlp",
    "tempo_otlp_grpc": "tempo.us-east4.argus.internal:4317",
    "kafka_bootstrap": "redpanda.us-east4.argus.internal:9092",
}


def fixture_context(topology: str, class_name: str) -> Context:
    kw: dict = dict(
        topology=topology,
        cluster="golden-cluster",
        environment="prod",
        app=f"sample-{class_name}",
        namespace="sample",
    )
    if topology == "regional":
        kw["region"] = "us-east4"
        kw["endpoints"] = dict(REGIONAL_ENDPOINTS)
    return Context(**kw)


def render_everything(class_name: str, topology: str) -> dict[str, object]:
    baseline = load_baseline(PROFILES / "baseline.yaml")
    cls = load_class(PROFILES / "classes" / f"{class_name}.yaml")
    p = resolve(baseline, cls, load_override(None))
    ctx = fixture_context(topology, class_name)

    artifacts: dict[str, object] = {
        "collector-agent": render_agent(p, ctx),
        "collector-gateway": render_gateway(p, ctx),
        "collector-targetallocator": render_targetallocator(p, ctx),
        "servicemonitor": render_servicemonitor(p, ctx),
        "podmonitor": render_podmonitor(p, ctx),
        "perses": render_perses(p, ctx),
        "alerts": render_alert_rules(p, ctx),
    }
    if topology == "regional":
        artifacts["collector-ingest"] = render_ingest(p, ctx)
    sloth = render_sloth(p, ctx)
    if sloth is not None:
        artifacts["sloth"] = sloth
    return artifacts


def main() -> None:
    if GOLDEN.exists():
        shutil.rmtree(GOLDEN)
    for class_name in GOLDEN_CLASSES:
        for topology in TOPOLOGIES:
            outdir = GOLDEN / f"{class_name}-{topology}"
            outdir.mkdir(parents=True)
            for name, doc in render_everything(class_name, topology).items():
                (outdir / f"{name}.yaml").write_text(
                    yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
                )
    count = sum(1 for _ in GOLDEN.rglob("*.yaml"))
    print(f"regenerated {count} golden files under {GOLDEN}")


if __name__ == "__main__":
    main()
