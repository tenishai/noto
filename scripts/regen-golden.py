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

from argus.cli import _platform_profile, _render_platform_families  # noqa: E402
from argus.integrations.catalog import load_catalog  # noqa: E402
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
CLASSES = ["web", "worker", "cron", "heavy", "gpu", "agent", "gateway"]
# Goldens are the HUMAN-review artifact: one representative class, both topologies.
# All-class coverage lives in the contract invariants (cheap, no churn).
GOLDEN_CLASSES = ["web"]
# The shared collector tier is platform-owned, not per-app: it gets its own case, rendered
# from the platform profile (baseline, all signals) and a Context with no app.
PLATFORM_CASE = "platform"
GOLDEN_CASES = [*GOLDEN_CLASSES, PLATFORM_CASE]
TOPOLOGIES = ["solo", "regional"]

# The platform's birth instant, fixed for the goldens only: the retention alert counts its
# age gate from it (born + the promise), so a wall clock here would rewrite the file on
# every run and the diff would stop meaning anything.
GOLDEN_BORN = "2026-01-01T00:00:00Z"

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


def platform_context(topology: str) -> Context:
    """The shared tier's Context: no app, no namespace (nothing in it may depend on one)."""
    kw: dict = dict(topology=topology, cluster="golden-cluster", environment="prod")
    if topology == "regional":
        kw["region"] = "us-east4"
        kw["endpoints"] = dict(REGIONAL_ENDPOINTS)
    return Context(**kw)


def render_everything(class_name: str, topology: str) -> dict[str, object]:
    """One app's artifacts. No collectors: the tier is shared, so it is rendered once per
    platform (render_platform), never from the app being observed."""
    baseline = load_baseline(PROFILES / "baseline.yaml")
    cls = load_class(PROFILES / "classes" / f"{class_name}.yaml")
    p = resolve(baseline, cls, load_override(None))
    ctx = fixture_context(topology, class_name)

    artifacts: dict[str, object] = {
        "servicemonitor": render_servicemonitor(p, ctx),
        "podmonitor": render_podmonitor(p, ctx),
        "perses": render_perses(p, ctx),
        "alerts": render_alert_rules(p, ctx),
    }
    sloth = render_sloth(p, ctx)
    if sloth is not None:
        artifacts["sloth"] = sloth
    return artifacts


_IDENTITY_DOC_NAMES = [
    "identity-serviceaccount",
    "identity-clusterrole",
    "identity-clusterrolebinding",
    "identity-role-platform-info",
    "identity-rolebinding-platform-info",
]


def render_platform(topology: str) -> dict[str, object]:
    """Everything `argus platform` puts in a cluster, taken from the CLI's own
    `_render_platform_families` so a golden can never show something a real run would not
    apply. Nothing here is re-derived by hand.

    The families are split back into one document per file because that is what a golden
    is for: a reviewable diff per object. `identity-*` and `collector-targetallocator`
    carry no topology dependence and so render byte-identical in both cases. The regional
    ingest collector is the one document `_render_platform_families` does not carry (it
    belongs to the regional topology, which `argus platform` does not stand up yet), so it
    is still rendered directly.
    """
    p = _platform_profile()
    ctx = platform_context(topology)
    families = _render_platform_families(ctx, GOLDEN_BORN)
    artifacts: dict[str, object] = {
        "collector-agent": render_agent(p, ctx, catalog=load_catalog(REPO / "integrations")),
        "collector-gateway": render_gateway(p, ctx),
        "collector-targetallocator": render_targetallocator(p, ctx),
    }
    if topology == "regional":
        artifacts["collector-ingest"] = render_ingest(p, ctx)
    for name, doc in zip(_IDENTITY_DOC_NAMES, families["identity"], strict=True):
        artifacts[name] = doc
    # The platform's own record and its own alert rules, both applied by `argus platform`
    # and belonging to no app. A real run uses the birth instant it finds (or records now);
    # a golden needs a fixed one, or the file would differ on every run.
    (artifacts["platform-info"],) = families["platform-info"]
    (artifacts["platform-rules"],) = families["platform-rules"]
    return artifacts


def render_case(case: str, topology: str) -> dict[str, object]:
    """A golden case is a workload class, or PLATFORM_CASE for the shared tier."""
    if case == PLATFORM_CASE:
        return render_platform(topology)
    return render_everything(case, topology)


def main() -> None:
    if GOLDEN.exists():
        shutil.rmtree(GOLDEN)
    for case in GOLDEN_CASES:
        for topology in TOPOLOGIES:
            outdir = GOLDEN / f"{case}-{topology}"
            outdir.mkdir(parents=True)
            for name, doc in render_case(case, topology).items():
                (outdir / f"{name}.yaml").write_text(
                    yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
                )
    count = sum(1 for _ in GOLDEN.rglob("*.yaml"))
    print(f"regenerated {count} golden files under {GOLDEN}")


if __name__ == "__main__":
    main()
