"""The thin Argus CLI - the daedalus-bundle fallback surface (design decision).

Flows:
  argus render    profile + gojira.yaml -> artifacts on disk (no cluster touched)
  argus platform  stand up the solo platform on the current kube context
  argus observe   wire one gojira app into the platform
  argus feedback  distill usage evidence from Mimir, print it (diagnostic only -
                  gojira-sizing-agent is the production evidence producer, issue #52)

Everything runs through the step engine and its ledger; re-runs are safe by
construction. `--dry-run` threads through to helm/kubectl server-side dry runs.
"""

import argparse
import datetime
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from argus.engine.engine import ProvisionEngine, RunResult
from argus.engine.ledger import Ledger
from argus.gojira.reader import read_gojira_yaml
from argus.integrations.catalog import load_catalog
from argus.job import JobInputError, JobInputs, failure_summary, job_inputs, success_summary
from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.resolve import resolve
from argus.rendering.alerts import render_alert_rules
from argus.rendering.collectors import (
    render_agent,
    render_gateway,
    render_priorityclass,
    render_targetallocator,
    render_targetallocator_rbac,
)
from argus.rendering.context import Context
from argus.rendering.perses import render_perses
from argus.rendering.scrape import render_podmonitor, render_servicemonitor
from argus.rendering.slo import render_sloth
from argus.steps import (
    ApplyRendered,
    EnsureNamespace,
    HelmInstall,
    StepContext,
    ValidateRoundTrip,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Dependency-ordered platform components (helm/pins.yaml holds the pins).
PLATFORM_ORDER = [
    "cert-manager",
    "kube-prometheus-stack",
    "opentelemetry-operator",
    "kube-state-metrics",
    "node-exporter",
    "mimir",
    "loki",
    "tempo",
    "perses",
]


def real_runner(cmd: list[str], stdin_text: str | None) -> tuple[int, str]:
    proc = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _resolved(class_name: str, override: dict | None):
    return resolve(
        load_baseline(REPO_ROOT / "profiles" / "baseline.yaml"),
        load_class(REPO_ROOT / "profiles" / "classes" / f"{class_name}.yaml"),
        load_override(override),
    )


def _render_families(p, ctx: Context) -> dict[str, list[dict]]:
    families: dict[str, list[dict]] = {
        "collectors": [
            render_priorityclass(),
            *render_targetallocator_rbac(ctx),
            render_agent(p, ctx, catalog=load_catalog(REPO_ROOT / "integrations")),
            render_gateway(p, ctx),
            render_targetallocator(p, ctx),
        ],
        "alerts": [render_alert_rules(p, ctx)],
        "perses": render_perses(p, ctx),
    }
    if p.app_scrape:
        families["scrape"] = [render_servicemonitor(p, ctx), render_podmonitor(p, ctx)]
    sloth = render_sloth(p, ctx)
    if sloth is not None:
        families["slo"] = [sloth]
    return families


def _app_context(args: argparse.Namespace) -> tuple[object, Context]:
    spec = read_gojira_yaml(Path(args.gojira))
    env = spec.environment(args.env)
    p = _resolved(spec.class_name, spec.observability)
    ctx = Context(
        topology="solo",
        cluster=getattr(args, "cluster", None) or env.cluster,
        environment=args.env,
        app=spec.app,
        namespace=env.namespace,
    )
    return p, ctx


def cmd_render(args: argparse.Namespace) -> int:
    p, ctx = _app_context(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for family, docs in _render_families(p, ctx).items():
        (out / f"{family}.yaml").write_text(yaml.safe_dump_all(docs))
    print(f"rendered {ctx.app} ({p.class_name}) into {out}")
    return 0


def _step_context(args: argparse.Namespace, rendered: dict | None = None) -> StepContext:
    pins = yaml.safe_load((REPO_ROOT / "helm" / "pins.yaml").read_text())
    return StepContext(
        runner=real_runner,
        kube_context=args.kube_context,
        pins=pins,
        repo_root=REPO_ROOT,
        rendered=rendered or {},
        dry_run=args.dry_run,
    )


def cmd_platform(args: argparse.Namespace) -> int:
    ledger = Ledger(Path(args.ledger))
    baseline = load_baseline(REPO_ROOT / "profiles" / "baseline.yaml")
    budget = baseline.budgets.cardinality[args.env]
    steps = [
        EnsureNamespace(),
        *[
            HelmInstall(
                component=c,
                extra_sets=(
                    # The profile budget becomes the tenant series limit (soft
                    # enforcement, issue #44); numbers are inputs, never constants.
                    {"mimir.structuredConfig.limits.max_global_series_per_user": str(budget)}
                    if c == "mimir"
                    else {}
                ),
            )
            for c in PLATFORM_ORDER
        ],
        ValidateRoundTrip(),
    ]
    result = ProvisionEngine(steps).run(ledger, f"platform@{args.cluster}", _step_context(args))
    _report(result)
    return 0 if result.completed else 1


def _generate_slo_rules(sloth_doc: dict, namespace: str) -> list[dict]:
    """Sloth is a GENERATOR, not an operator (design decision): spec in,
    PrometheusRule out. Requires the pinned sloth binary (make validate fetches it)."""
    sloth_bin = REPO_ROOT / ".argus" / "tools" / "sloth-0.16.0"
    if not sloth_bin.exists():
        raise RuntimeError("sloth binary missing; run `make validate` once to fetch pinned tools")
    with tempfile.TemporaryDirectory() as td:
        spec = Path(td) / "slo.yaml"
        out = Path(td) / "rules.yaml"
        spec.write_text(yaml.safe_dump(sloth_doc))
        proc = subprocess.run(
            [str(sloth_bin), "generate", "-i", str(spec), "-o", str(out)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"sloth generate failed: {proc.stderr[-1000:]}")
        docs = [d for d in yaml.safe_load_all(out.read_text()) if d]
    for doc in docs:
        doc.setdefault("metadata", {})["namespace"] = namespace
    return docs


def _run_observe(args: argparse.Namespace) -> tuple[RunResult, Context]:
    """The one observe step sequence, shared by the operator command and job mode."""
    p, ctx = _app_context(args)
    rendered = _render_families(p, ctx)
    ledger = Ledger(Path(args.ledger))
    steps = [ApplyRendered(family=f) for f in ("collectors", "scrape", "alerts") if f in rendered]
    if "slo" in rendered:
        assert ctx.namespace is not None
        rendered["slo"] = _generate_slo_rules(rendered["slo"][0], ctx.namespace)
        steps.append(ApplyRendered(family="slo"))
    result = ProvisionEngine(steps).run(ledger, f"wire@{ctx.app}", _step_context(args, rendered))
    return result, ctx


def cmd_observe(args: argparse.Namespace) -> int:
    result, _ctx = _run_observe(args)
    _report(result)
    return 0 if result.completed else 1


def _observe_job(inputs: JobInputs, args: argparse.Namespace) -> int:
    """The job entry path: env-driven inputs, a throwaway ledger, and one
    ARGUS_SUMMARY line on stdout no matter how it ends."""
    tmpdir = Path(tempfile.mkdtemp(prefix="argus-job-"))
    gojira_path = tmpdir / "gojira.yaml"
    gojira_path.write_text(inputs.contract_yaml)
    args.gojira = str(gojira_path)
    args.env = inputs.env
    args.cluster = inputs.cluster or args.cluster
    args.ledger = str(tmpdir / "argus.db")

    try:
        result, ctx = _run_observe(args)
    except Exception as exc:  # job mode must always end with a summary line, not a traceback
        print(failure_summary(None, str(exc)), flush=True)
        return 1

    _report(result)
    if result.completed:
        print(
            success_summary(
                ctx.app or "unknown",
                inputs.env,
                len(result.steps_run),
                len(result.steps_skipped),
                inputs.contract_sha256,
            ),
            flush=True,
        )
        return 0
    print(failure_summary(result.failed_step, result.error or "unknown error"), flush=True)
    return 1


def cmd_feedback(args: argparse.Namespace) -> int:
    from argus.feedback.distill import distill_resource, to_cpu_quantity, to_mem_quantity
    from argus.feedback.mimir import MimirClient, collect
    from argus.feedback.models import UsageProfile

    if not args.dry_run:
        print(
            "argus feedback is diagnostic-only: gojira-sizing-agent is the single evidence\n"
            "producer (issue #52, Option A - measurements converged live 2026-07-12).\n"
            "Re-run with --dry-run to print the distilled profile without posting.",
            file=sys.stderr,
        )
        return 2

    spec = read_gojira_yaml(Path(args.gojira))
    env = spec.environment(args.env)
    client = MimirClient(base_url=args.mimir_url, tenant=env.cluster)
    raw = collect(client, spec.app, env.namespace, args.window_days)

    profile = UsageProfile(
        app=spec.app,
        environment=args.env,
        cluster=env.cluster,
        window_days=args.window_days,
        samples=len(raw["cpu_samples"]),
        cpu=distill_resource(
            raw["cpu_samples"], raw["cpu_request"], raw["cpu_limit"], to_cpu_quantity
        ),
        memory=distill_resource(
            raw["memory_samples"], raw["memory_request"], raw["memory_limit"], to_mem_quantity
        ),
        restarts=raw["restarts"],
        oom_kills=raw["oom_kills"],
        reported_by=f"argus/{env.cluster}",
        reported_at=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    print(profile.serialize())
    print("dry-run: nothing posted", file=sys.stderr)
    return 0


def cmd_rules_sync(args: argparse.Namespace) -> int:
    from argus.feedback.ruler import sync_rules
    from argus.rendering.slo import render_sloth

    p, ctx = _app_context(args)
    docs = [render_alert_rules(p, ctx)]
    sloth_doc = render_sloth(p, ctx)
    if sloth_doc is not None:
        docs.extend(_generate_slo_rules(sloth_doc, ctx.namespace or "default"))
    count = sync_rules(args.mimir_url, ctx.tenant, ctx.app or "argus", docs)
    print(f"synced {count} rule groups to the ruler (tenant {ctx.tenant})")
    return 0


def cmd_cardinality(args: argparse.Namespace) -> int:
    from argus.feedback.cardinality import top_metric_names

    rows = top_metric_names(args.mimir_url, args.tenant, args.limit)
    if not rows:
        print("no series (or cardinality API empty)")
        return 0
    width = max(len(name) for name, _ in rows)
    print(f"top {len(rows)} metric names by series count (tenant {args.tenant}):")
    for name, count in rows:
        print(f"  {name:<{width}}  {count}")
    return 0


def _report(result) -> None:
    """Human-readable progress, always on stderr - stdout is reserved for the job
    mode ARGUS_SUMMARY line (design decision, task 1)."""
    for name in result.steps_skipped:
        print(f"  skipped  {name}", file=sys.stderr)
    for name in result.steps_run:
        print(f"  applied  {name}", file=sys.stderr)
    if result.completed:
        print("done", file=sys.stderr)
    else:
        print(f"FAILED at {result.failed_step}: {result.error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="argus", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, needs_app: bool, app_required: bool = True) -> None:
        p.add_argument("--ledger", default=".argus/argus.db")
        p.add_argument("--kube-context", default=None)
        p.add_argument("--dry-run", action="store_true")
        if needs_app:
            p.add_argument("--gojira", required=app_required, help="path to the app's gojira.yaml")
            p.add_argument("--env", required=app_required, choices=["dev", "qa", "prod"])
            # The observability identity (Perses project, tenant, gateway exporter
            # headers) must be ONE value per cluster. gojira.yaml cluster strings
            # drift across apps (live-found: 'acme-dev' vs the GKE cluster name),
            # and each observe re-renders the shared collector tier - last writer
            # wins. Pin it here; the per-app file stays the fallback.
            p.add_argument("--cluster", default=None, help="canonical cluster/tenant name override")

    p_render = sub.add_parser("render", help="render artifacts, touch nothing")
    common(p_render, needs_app=True)
    p_render.add_argument("--out", default=".argus/rendered-app")
    p_render.set_defaults(func=cmd_render)

    p_platform = sub.add_parser("platform", help="stand up the solo platform")
    common(p_platform, needs_app=False)
    p_platform.add_argument("--cluster", required=True)
    p_platform.add_argument("--env", default="dev", choices=["dev", "qa", "prod"])
    p_platform.set_defaults(func=cmd_platform)

    p_observe = sub.add_parser("observe", help="wire a gojira app into the platform")
    # Job mode (GOJIRA_CONTRACT_YAML present) supplies --gojira/--env from the
    # environment instead - relax the parser and enforce them ourselves below so
    # operator mode keeps its exact today's-behavior error text.
    common(p_observe, needs_app=True, app_required=False)
    p_observe.set_defaults(func=cmd_observe)

    p_rules = sub.add_parser("rules-sync", help="push rendered rule groups into Mimir's ruler")
    common(p_rules, needs_app=True)
    p_rules.add_argument("--mimir-url", default="http://localhost:9009/prometheus")
    p_rules.set_defaults(func=cmd_rules_sync)

    p_card = sub.add_parser("cardinality", help="top metric names by series count, per tenant")
    p_card.add_argument("--ledger", default=".argus/argus.db")
    p_card.add_argument("--mimir-url", default="http://localhost:9009/prometheus")
    p_card.add_argument("--tenant", required=True)
    p_card.add_argument("--limit", type=int, default=15)
    p_card.set_defaults(func=cmd_cardinality)

    p_feedback = sub.add_parser(
        "feedback",
        help="distill usage evidence from Mimir, diagnostic only "
        "(the producer is gojira-sizing-agent; requires --dry-run)",
    )
    common(p_feedback, needs_app=True)
    p_feedback.add_argument("--window-days", type=int, default=14)
    p_feedback.add_argument(
        "--mimir-url",
        default="http://localhost:9009/prometheus",
        help="Mimir Prometheus API base (port-forward the gateway in solo)",
    )
    p_feedback.set_defaults(func=cmd_feedback)

    args = parser.parse_args(argv)

    if args.command == "observe":
        try:
            inputs = job_inputs(os.environ)
        except JobInputError as exc:
            print(failure_summary(None, str(exc)), flush=True)
            return 1
        if inputs is not None:
            return _observe_job(inputs, args)
        if args.gojira is None or args.env is None:
            # No job-mode env vars and no operator flags either: keep operator
            # mode's exact today's-behavior (exit 2, argparse-style error on
            # stderr, no summary line - there are no job inputs to report on).
            p_observe.error("the following arguments are required: --gojira, --env")

    Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
