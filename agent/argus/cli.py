"""The thin Argus CLI - the daedalus-bundle fallback surface (design decision).

Three flows, mirroring the Phase 1 skills:
  argus render    profile + gojira.yaml -> artifacts on disk (no cluster touched)
  argus platform  stand up the solo platform on the current kube context
  argus observe   wire one gojira app into the platform

Everything runs through the step engine and its ledger; re-runs are safe by
construction. `--dry-run` threads through to helm/kubectl server-side dry runs.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from argus.engine.engine import ProvisionEngine
from argus.engine.ledger import Ledger
from argus.gojira.reader import read_gojira_yaml
from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.resolve import resolve
from argus.rendering.alerts import render_alert_rules
from argus.rendering.collectors import (
    render_agent,
    render_gateway,
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
            *render_targetallocator_rbac(ctx),
            render_agent(p, ctx),
            render_gateway(p, ctx),
            render_targetallocator(p, ctx),
        ],
        "scrape": [render_servicemonitor(p, ctx), render_podmonitor(p, ctx)],
        "alerts": [render_alert_rules(p, ctx)],
        "perses": render_perses(p, ctx),
    }
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
        cluster=env.cluster,
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
    steps = [
        EnsureNamespace(),
        *[HelmInstall(component=c) for c in PLATFORM_ORDER],
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


def cmd_observe(args: argparse.Namespace) -> int:
    p, ctx = _app_context(args)
    rendered = _render_families(p, ctx)
    ledger = Ledger(Path(args.ledger))
    steps = [ApplyRendered(family=f) for f in ("collectors", "scrape", "alerts")]
    if "slo" in rendered:
        assert ctx.namespace is not None
        rendered["slo"] = _generate_slo_rules(rendered["slo"][0], ctx.namespace)
        steps.append(ApplyRendered(family="slo"))
    result = ProvisionEngine(steps).run(ledger, f"wire@{ctx.app}", _step_context(args, rendered))
    _report(result)
    return 0 if result.completed else 1


def _report(result) -> None:
    for name in result.steps_skipped:
        print(f"  skipped  {name}")
    for name in result.steps_run:
        print(f"  applied  {name}")
    if result.completed:
        print("done")
    else:
        print(f"FAILED at {result.failed_step}: {result.error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="argus", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, needs_app: bool) -> None:
        p.add_argument("--ledger", default=".argus/argus.db")
        p.add_argument("--kube-context", default=None)
        p.add_argument("--dry-run", action="store_true")
        if needs_app:
            p.add_argument("--gojira", required=True, help="path to the app's gojira.yaml")
            p.add_argument("--env", required=True, choices=["dev", "qa", "prod"])

    p_render = sub.add_parser("render", help="render artifacts, touch nothing")
    common(p_render, needs_app=True)
    p_render.add_argument("--out", default=".argus/rendered-app")
    p_render.set_defaults(func=cmd_render)

    p_platform = sub.add_parser("platform", help="stand up the solo platform")
    common(p_platform, needs_app=False)
    p_platform.add_argument("--cluster", required=True)
    p_platform.set_defaults(func=cmd_platform)

    p_observe = sub.add_parser("observe", help="wire a gojira app into the platform")
    common(p_observe, needs_app=True)
    p_observe.set_defaults(func=cmd_observe)

    args = parser.parse_args(argv)
    Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
