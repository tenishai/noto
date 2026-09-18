"""The thin Argus CLI - the daedalus-bundle fallback surface (design decision).

Flows:
  argus render    profile + gojira.yaml -> artifacts on disk (no cluster touched);
                  --platform renders the shared collector tier instead
  argus platform  stand up the solo platform on the current kube context, including the
                  shared collector tier (one agent/gateway/allocator per cluster)
  argus observe   wire one gojira app into the platform (per-app artifacts only)
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
from argus.perses_client import Opener
from argus.profiles.loader import load_baseline, load_class, load_override
from argus.profiles.models import ClassProfile, DropRule, ResolvedProfile
from argus.profiles.resolve import resolve
from argus.rendering.alerts import render_alert_rules, render_platform_rules
from argus.rendering.collectors import (
    render_agent,
    render_gateway,
    render_priorityclass,
    render_targetallocator,
    render_targetallocator_rbac,
)
from argus.rendering.context import Context, solo_endpoints
from argus.rendering.perses import render_perses
from argus.rendering.rbac import PLATFORM_INFO_CONFIGMAP, render_observe_rbac
from argus.rendering.scrape import render_podmonitor, render_servicemonitor
from argus.rendering.slo import render_sloth
from argus.steps import (
    ApplyRendered,
    EnsureNamespace,
    EnsurePlatformInfo,
    HelmInstall,
    PlatformInfoUnreadable,
    ProvisionPerses,
    StepContext,
    ValidateRoundTrip,
    WaitForCollectors,
    helm_set_values,
    read_platform_info,
    read_platform_info_strict,
    retention_helm_sets,
    utc_now_rfc3339,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every object argus applies in job mode carries this annotation with the verified
# contract fingerprint, so the cluster itself can answer "has the app changed since
# argus looked". Operator mode sets no annotation at all (there is no job contract).
CONTRACT_SHA_ANNOTATION = "argus.dev/contract-sha256"

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


# Injectable seam for ProvisionPerses's PersesClient - the HTTP analogue of
# `real_runner` above, monkeypatched by tests the same way. Production leaves it
# None, so PersesClient falls back to real `urllib`.
default_perses_opener: Opener | None = None


def _resolved(class_name: str, override: dict | None):
    return resolve(
        load_baseline(REPO_ROOT / "profiles" / "baseline.yaml"),
        load_class(REPO_ROOT / "profiles" / "classes" / f"{class_name}.yaml"),
        load_override(override),
    )


ALL_SIGNALS = ["metrics", "logs", "traces"]


def _platform_profile() -> ResolvedProfile:
    """The profile the SHARED collector tier is rendered from: the baseline, no class
    deltas, every signal on.

    The tier (agent DaemonSet, gateway, target allocator) is one set of objects per
    cluster serving every app, so it can never be rendered from one app's profile -
    doing that meant observing an app whose class has no traces signal re-rendered the
    shared gateway without a traces pipeline and applied it over everyone else's. Every
    signal is on because the tier has to carry whatever the next app turns on, and the
    tier cannot know which apps will exist.

    ClassProfile's class name is a Literal over the shipped classes, so the empty class
    has to borrow one of the names; nothing of it survives - its signals, slos,
    dashboards and drop rules are all empty, and class_name is replaced below. We do NOT
    ship a fake class file for this: the platform tier is not a workload class.
    """
    empty_class = ClassProfile.model_validate({"class": "web", "signals": []})
    p = resolve(
        load_baseline(REPO_ROOT / "profiles" / "baseline.yaml"),
        empty_class,
        load_override(None),
    )
    return p.model_copy(update={"class_name": "platform", "signals": list(ALL_SIGNALS)})


def _render_platform_families(ctx: Context) -> dict[str, list[dict]]:
    """The platform-owned families. `ctx` carries no app and no namespace: nothing in
    the shared tier may depend on one app (the collector CRDs live in the argus
    namespace and are named by role, never by app).

    `identity` is a separate family from `collectors` on purpose: it is the job
    identity a provisioner's Job runs `argus observe` as, an unrelated concern from the
    collector tier (RBAC objects have no CRD dependency, so it does not need the
    collectors' `opentelemetry-operator`-after-helm ordering either) - keeping it
    separate keeps both families' names meaningful. `cmd_platform` applies it right
    after `collectors`, which keeps `ApplyRendered` ordering a plain list append."""
    p = _platform_profile()
    return {
        "collectors": [
            render_priorityclass(),
            *render_targetallocator_rbac(ctx),
            render_agent(p, ctx, catalog=load_catalog(REPO_ROOT / "integrations")),
            render_gateway(p, ctx),
            render_targetallocator(p, ctx),
        ],
        "identity": render_observe_rbac(ctx.platform_namespace),
    }


def _render_families(p, ctx: Context) -> dict[str, list[dict]]:
    """Per-app families only. The collector tier is NOT here by design: see
    `_render_platform_families`."""
    families: dict[str, list[dict]] = {
        "alerts": [render_alert_rules(p, ctx)],
        "perses": render_perses(p, ctx),
    }
    if p.app_scrape:
        families["scrape"] = [render_servicemonitor(p, ctx), render_podmonitor(p, ctx)]
    sloth = render_sloth(p, ctx)
    if sloth is not None:
        families["slo"] = [sloth]
    return families


def _ignored_drop_rule_names(app_drop: list[DropRule], platform_drop: list[DropRule]) -> list[str]:
    """Names of drop rules that differ between an app's resolved profile and the
    platform profile's - added by a class/override, changed, or disabled (an
    `enabled: false` baseline override drops the rule from the resolved list
    entirely). `p.drop` is consumed only by the collector renderers, and those now
    render from the platform profile alone (see `_platform_profile`), so any of
    these differences is now a silent no-op - this makes it loud instead. Sorted,
    for a warning whose wording never depends on dict iteration order."""
    by_name_app = {r.name: r for r in app_drop}
    by_name_platform = {r.name: r for r in platform_drop}
    ignored = {
        name
        for name in {*by_name_app, *by_name_platform}
        if by_name_app.get(name) != by_name_platform.get(name)
    }
    return sorted(ignored)


def _warn_if_drop_rules_ignored(p: ResolvedProfile, app: str | None) -> None:
    """Never raises, never touches stdout (job mode's last stdout line is a
    machine-read summary) - a comparison against the profile the platform actually
    renders from, printed as one stderr line when it finds a difference."""
    ignored = _ignored_drop_rule_names(p.drop, _platform_profile().drop)
    if not ignored:
        return
    print(
        f"warning: drop rules are platform-owned now; ignored for {app}: "
        f'{", ".join(ignored)} (see README, "The collector tier")',
        file=sys.stderr,
    )


class TenantMismatch(RuntimeError):
    """An explicit --cluster/ARGUS_CLUSTER that disagrees with the tenant the platform
    records as its own. Raised before anything is applied."""


def _resolve_tenant(args: argparse.Namespace, contract_cluster: str) -> str:
    """Which tenant this app's dashboards, rules and cardinality reports READ with.

    The gateway WRITES with the tenant `argus platform --cluster` was given and records it
    (steps.EnsurePlatformInfo). If the value resolved here ever differs from that one, every
    panel is empty and nothing errors, so the platform's own record is the authority:

      1. an explicit --cluster / ARGUS_CLUSTER, which must AGREE with the record;
      2. otherwise the recorded tenant, even when the contract disagrees (one stderr line);
      3. otherwise (no record: a platform older than this, or no cluster at all in a dry
         run) the contract's own cluster string, with one stderr warning.

    Reading the record is a plain `kubectl get` through the runner and never raises, so
    `render` and `--dry-run` keep working with no cluster in reach.
    """
    explicit = getattr(args, "cluster", None)
    recorded = read_platform_info(_step_context(args)).get("tenant")
    if recorded:
        if explicit and explicit != recorded:
            raise TenantMismatch(
                f"tenant mismatch: platform writes as '{recorded}' but observe was told "
                f"'{explicit}'; dashboards would read an empty tenant"
            )
        if not explicit and contract_cluster != recorded:
            print(
                f"info: the platform writes as tenant '{recorded}'; using it instead of the "
                f"contract's cluster '{contract_cluster}'",
                file=sys.stderr,
            )
        return recorded
    if explicit:
        return explicit
    print(
        f"warning: no {PLATFORM_INFO_CONFIGMAP} record in namespace {args.namespace}; "
        f"falling back to the contract's cluster '{contract_cluster}'. Re-run `argus "
        "platform` so the tenant the gateway writes with is the one dashboards read with.",
        file=sys.stderr,
    )
    return contract_cluster


def _app_context(
    args: argparse.Namespace, *, tenant_from_platform: bool = False
) -> tuple[object, Context]:
    spec = read_gojira_yaml(Path(args.gojira))
    env = spec.environment(args.env)
    p = _resolved(spec.class_name, spec.observability)
    _warn_if_drop_rules_ignored(p, spec.app)
    cluster = (
        _resolve_tenant(args, env.cluster)
        if tenant_from_platform
        else (getattr(args, "cluster", None) or env.cluster)
    )
    ctx = Context(
        topology="solo",
        cluster=cluster,
        environment=args.env,
        app=spec.app,
        namespace=env.namespace,
        platform_namespace=args.namespace,
    )
    return p, ctx


def _platform_context(args: argparse.Namespace) -> Context:
    """The Context the shared tier renders against: cluster and environment (the gateway
    stamps `environment` as a resource attribute on everything it forwards), and no app
    or namespace at all."""
    return Context(
        topology="solo",
        cluster=args.cluster,
        environment=args.env,
        platform_namespace=args.namespace,
    )


def cmd_render(args: argparse.Namespace) -> int:
    # --out has no single sane default across two very different outputs, so a
    # missing --out picks the right default per mode instead of one dir for both.
    if args.out:
        out = Path(args.out)
    else:
        out = Path(".argus/rendered-platform" if args.platform else ".argus/rendered-app")
    out.mkdir(parents=True, exist_ok=True)
    if args.platform:
        ctx = _platform_context(args)
        families = _render_platform_families(ctx)
        label = f"platform tier for {ctx.cluster} ({ctx.environment})"
    else:
        p, ctx = _app_context(args)
        families = _render_families(p, ctx)
        label = f"{ctx.app} ({p.class_name})"
    for family, docs in families.items():
        (out / f"{family}.yaml").write_text(yaml.safe_dump_all(docs))
    print(f"rendered {label} into {out}")
    return 0


def _add_namespace_flag(p: argparse.ArgumentParser) -> None:
    """The platform's own namespace (argus-mimir, argus-loki, ... live here) - NOT an
    app's namespace. Feeds the in-cluster endpoint derivation (`solo_endpoints`) for
    `--mimir-url`/`--perses-url` and is where `argus platform` installs the stack.
    `ARGUS_NAMESPACE` matches job mode's own env input (agent/argus/job.py) so an
    operator invocation and a launched Job agree on the default without either side
    hardcoding the other's convention."""
    p.add_argument(
        "--namespace",
        default=os.environ.get("ARGUS_NAMESPACE", "argus"),
        help="the observability platform's own namespace (env ARGUS_NAMESPACE, default argus)",
    )


def _resolve_mimir_url(args: argparse.Namespace) -> str:
    """An explicit --mimir-url always wins; otherwise the in-cluster query endpoint
    is derived from --namespace. For an operator's port-forward habit, that is
    `--mimir-url http://localhost:9009/prometheus` (see README)."""
    return args.mimir_url or solo_endpoints(args.namespace)["mimir_query_url"]


def _step_context(
    args: argparse.Namespace,
    rendered: dict | None = None,
    annotations: dict[str, str] | None = None,
) -> StepContext:
    pins = yaml.safe_load((REPO_ROOT / "helm" / "pins.yaml").read_text())
    perses_url = (
        getattr(args, "perses_url", None)
        or os.environ.get("ARGUS_PERSES_URL")
        or solo_endpoints(args.namespace)["perses_url"]
    )
    return StepContext(
        runner=real_runner,
        kube_context=args.kube_context,
        namespace=args.namespace,
        pins=pins,
        repo_root=REPO_ROOT,
        rendered=rendered or {},
        dry_run=args.dry_run,
        annotations=annotations or {},
        perses_url=perses_url,
        perses_opener=default_perses_opener,
    )


_RULER_NOTE = (
    "note: PrometheusRule objects are inert in this stack (kube-prometheus-stack ships the\n"
    "operator, not a Prometheus) - rules are only evaluated once they reach Mimir's ruler.\n"
    "`argus rules-sync --gojira <app> --env <env>` pushes an app's groups; the platform's own\n"
    'group (argus-platform) has no push of its own yet (README, "Rules reach Mimir\'s ruler").'
)


def _born_epoch(born: str) -> int:
    """The recorded birth instant as a unix timestamp. A hand-edited or truncated `born`
    must not stop a platform run: warn, and count the age gate from now instead (the alert
    then simply stays quiet for one more retention window)."""
    try:
        return int(datetime.datetime.fromisoformat(born).timestamp())
    except ValueError:
        print(
            f"warning: the platform record's born value {born!r} is not an RFC 3339 "
            "timestamp; the retention alert's age gate counts from now instead",
            file=sys.stderr,
        )
        return int(datetime.datetime.now(datetime.UTC).timestamp())


def cmd_platform(args: argparse.Namespace) -> int:
    ledger = Ledger(Path(args.ledger))
    baseline = load_baseline(REPO_ROOT / "profiles" / "baseline.yaml")
    budget = baseline.budgets.cardinality[args.env]
    retention = dict(baseline.budgets.retention)
    retention_sets = retention_helm_sets(retention)
    step_context = _step_context(args, {})
    # The birth instant is needed BEFORE the engine runs: the platform rules are rendered
    # (not generated at apply time), and EnsurePlatformInfo needs the same value, so the
    # alert's age gate and the record can never disagree by a second.
    #
    # This is the WRITE path, so the read is the strict one: a record that cannot be read
    # is NOT an unborn platform, and treating it as one would stamp a fresh `born` over a
    # living one. Only a dry run (which writes nothing, and may have no cluster in reach at
    # all) is allowed the lenient read.
    try:
        recorded = (
            read_platform_info(step_context)
            if args.dry_run
            else read_platform_info_strict(step_context)
        )
    except PlatformInfoUnreadable as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    born = recorded.get("born") or utc_now_rfc3339()
    rendered = _render_platform_families(_platform_context(args))
    rendered["platform-rules"] = [
        render_platform_rules(retention, _born_epoch(born), namespace=args.namespace)
    ]
    step_context.rendered = rendered
    steps = [
        EnsureNamespace(),
        # Before the platform rules, which count their age gate from the `born` it records,
        # and before anything else has a chance to fail: the record is what `observe` reads
        # to learn which tenant this platform writes as.
        EnsurePlatformInfo(tenant=args.cluster, environment=args.env, born=born),
        *[
            HelmInstall(
                component=c,
                extra_sets=(
                    # The profile budget becomes the tenant series limit (soft
                    # enforcement, issue #44); numbers are inputs, never constants.
                    {"mimir.structuredConfig.limits.max_global_series_per_user": str(budget)}
                    if c == "mimir"
                    else {}
                )
                # ... and the profile's retention promise becomes each backend's own
                # retention setting, the same way (see retention_helm_sets).
                | helm_set_values(retention_sets.get(c, [])),
            )
            for c in PLATFORM_ORDER
        ],
        # The collector tier is platform-owned (one shared agent/gateway/allocator per
        # cluster), so it is applied here and never by `observe`. It MUST come after the
        # helm steps: these are OpenTelemetryCollector custom resources, and the CRDs and
        # admission webhook that accept them arrive with `opentelemetry-operator` in
        # PLATFORM_ORDER. Keep that ordering.
        ApplyRendered(family="collectors"),
        # The job's own identity (ServiceAccount + RBAC a provisioner's Job runs
        # `argus observe` as): plain RBAC objects, no CRD dependency, so it needs no
        # particular position relative to the helm installs - applying it right after
        # `collectors` keeps this list a simple append.
        ApplyRendered(family="identity"),
        # The platform's own alert rules (retention). A PrometheusRule needs the CRD that
        # arrives with kube-prometheus-stack, so this must stay after the helm steps.
        ApplyRendered(family="platform-rules"),
        # The apply above has no --wait (unlike HelmInstall): the OpenTelemetry
        # operator still has to turn the CRs into workloads and pull images, so a
        # fresh install (or an upgrade rolling the DaemonSet) sees ordinary
        # Pending/ContainerCreating right after. ValidateRoundTrip fails on any pod
        # that isn't Running/Succeeded, so it MUST come after this wait, not before.
        WaitForCollectors(),
        ValidateRoundTrip(),
    ]
    result = ProvisionEngine(steps).run(ledger, f"platform@{args.cluster}", step_context)
    _report(result)
    if result.completed:
        print(_RULER_NOTE)
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


def _run_observe(
    args: argparse.Namespace, annotations: dict[str, str] | None = None
) -> tuple[RunResult, Context]:
    """The one observe step sequence, shared by the operator command and job mode.

    The tenant is resolved from the platform's own record here, in this one shared place,
    before anything is rendered or applied (see `_resolve_tenant`)."""
    p, ctx = _app_context(args, tenant_from_platform=True)
    rendered = _render_families(p, ctx)
    ledger = Ledger(Path(args.ledger))
    # Per-app families only. The shared collector tier belongs to `argus platform`:
    # rendering it from one app's profile let one observe reconfigure the gateway for
    # the whole cluster.
    steps = [ApplyRendered(family=f) for f in ("scrape", "alerts") if f in rendered]
    if "slo" in rendered:
        assert ctx.namespace is not None
        rendered["slo"] = _generate_slo_rules(rendered["slo"][0], ctx.namespace)
        steps.append(ApplyRendered(family="slo"))
    # Dashboards land last: they chart the alerts/SLOs applied above, so they must
    # exist first, or a dashboard-provisioning failure would still leave rules and
    # alerts wired.
    steps.append(ProvisionPerses())
    result = ProvisionEngine(steps).run(
        ledger, f"wire@{ctx.app}", _step_context(args, rendered, annotations)
    )
    return result, ctx


def cmd_observe(args: argparse.Namespace) -> int:
    try:
        result, _ctx = _run_observe(args)
    except TenantMismatch as exc:
        # Loud, and before a single object is applied: the alternative is dashboards that
        # query a tenant nothing writes to, which look like a quiet, working install.
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
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
    args.namespace = inputs.namespace
    args.ledger = str(tmpdir / "argus.db")

    try:
        result, ctx = _run_observe(
            args, annotations={CONTRACT_SHA_ANNOTATION: inputs.contract_sha256}
        )
    except Exception as exc:  # job mode must always end with a summary line, not a traceback
        print(failure_summary(None, _exception_reason(exc)), flush=True)
        return 1

    _report(result, progress_stream=sys.stderr)
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
    client = MimirClient(base_url=_resolve_mimir_url(args), tenant=env.cluster)
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
    count = sync_rules(_resolve_mimir_url(args), ctx.tenant, ctx.app or "argus", docs)
    print(f"synced {count} rule groups to the ruler (tenant {ctx.tenant})")
    return 0


def cmd_cardinality(args: argparse.Namespace) -> int:
    from argus.feedback.cardinality import top_metric_names

    rows = top_metric_names(_resolve_mimir_url(args), args.tenant, args.limit)
    if not rows:
        print("no series (or cardinality API empty)")
        return 0
    width = max(len(name) for name, _ in rows)
    print(f"top {len(rows)} metric names by series count (tenant {args.tenant}):")
    for name, count in rows:
        print(f"  {name:<{width}}  {count}")
    return 0


def _report(result, *, progress_stream=None) -> None:
    """Human-readable progress. Operator mode (the default, unchanged since before
    job mode existed) prints skipped/applied/done to stdout and only the FAILED
    line to stderr. Job mode passes progress_stream=sys.stderr so stdout carries
    nothing but the ARGUS_SUMMARY line (design decision, task 1).

    progress_stream defaults to sys.stdout looked up at CALL time, not def time -
    a bare `=sys.stdout` default would bind the stream object module import
    captures, which stays stale under test fixtures (capsys) that swap sys.stdout
    per test."""
    if progress_stream is None:
        progress_stream = sys.stdout
    for name in result.steps_skipped:
        print(f"  skipped  {name}", file=progress_stream)
    for name in result.steps_run:
        print(f"  applied  {name}", file=progress_stream)
    if result.completed:
        print("done", file=progress_stream)
    else:
        print(f"FAILED at {result.failed_step}: {result.error}", file=sys.stderr)


def _exception_reason(exc: BaseException) -> str:
    """str(KeyError) quotes its message ('no environment ...' becomes a
    double-quoted string) - unwrap that so job-mode summaries stay legible."""
    if isinstance(exc, KeyError) and exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="argus", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, needs_app: bool, app_required: bool = True) -> None:
        p.add_argument("--ledger", default=".argus/argus.db")
        p.add_argument("--kube-context", default=None)
        p.add_argument("--dry-run", action="store_true")
        _add_namespace_flag(p)
        if needs_app:
            p.add_argument("--gojira", required=app_required, help="path to the app's gojira.yaml")
            p.add_argument("--env", required=app_required, choices=["dev", "qa", "prod"])
            # The observability identity (Perses project, tenant, gateway exporter
            # headers) must be ONE value per cluster. gojira.yaml cluster strings
            # drift across apps (live-found: one deployment's short name vs the real
            # cluster name), which would scatter one cluster's apps across tenants.
            #
            # The gateway that actually WRITES metrics/logs/traces gets its tenant from
            # `argus platform --cluster`, and the platform now records it (the
            # argus-platform-info ConfigMap). `observe` reads that record back, so the
            # read tenant follows the write tenant on its own and this flag is an
            # explicit override only: given, it must AGREE with the record or the run
            # fails before applying anything (see `_resolve_tenant`). ARGUS_CLUSTER is
            # the same override by another name, which is why it defaults the flag here
            # rather than being read separately in job mode.
            p.add_argument(
                "--cluster",
                default=os.environ.get("ARGUS_CLUSTER", "").strip() or None,
                help="explicit cluster/tenant override (env ARGUS_CLUSTER) - must match "
                "the tenant `argus platform` recorded, or the run fails",
            )

    p_render = sub.add_parser("render", help="render artifacts, touch nothing")
    # --platform renders the shared tier instead of one app, and needs no gojira.yaml:
    # relax the parser and enforce the right set below, per mode.
    common(p_render, needs_app=True, app_required=False)
    # No single default fits both modes (see cmd_render): None here, resolved per mode.
    p_render.add_argument("--out", default=None)
    p_render.add_argument(
        "--platform",
        action="store_true",
        help="render the shared platform collector tier (needs --cluster and --env) "
        "instead of one app's artifacts",
    )
    p_render.set_defaults(func=cmd_render)

    p_platform = sub.add_parser("platform", help="stand up the solo platform")
    common(p_platform, needs_app=False)
    p_platform.add_argument("--cluster", required=True)
    # Required, not defaulted: the platform now applies the collector tier, whose gateway
    # stamps `environment` on every signal it forwards. A silent default of dev would
    # mislabel a whole prod cluster's telemetry.
    p_platform.add_argument("--env", required=True, choices=["dev", "qa", "prod"])
    p_platform.set_defaults(func=cmd_platform)

    p_observe = sub.add_parser("observe", help="wire a gojira app into the platform")
    # Job mode (GOJIRA_CONTRACT_YAML present) supplies --gojira/--env from the
    # environment instead - relax the parser and enforce them ourselves below so
    # operator mode keeps its exact today's-behavior error text.
    common(p_observe, needs_app=True, app_required=False)
    p_observe.add_argument(
        "--perses-url",
        default=None,
        help="Perses API base for the dashboards step; env ARGUS_PERSES_URL, else "
        "derived from --namespace, unless given. Port-forward form: "
        "--perses-url http://localhost:8080",
    )
    p_observe.set_defaults(func=cmd_observe)

    p_rules = sub.add_parser("rules-sync", help="push rendered rule groups into Mimir's ruler")
    common(p_rules, needs_app=True)
    p_rules.add_argument(
        "--mimir-url",
        default=None,
        help="Mimir Prometheus API base; derived from --namespace unless given. "
        "Port-forward form: --mimir-url http://localhost:9009/prometheus",
    )
    p_rules.set_defaults(func=cmd_rules_sync)

    p_card = sub.add_parser("cardinality", help="top metric names by series count, per tenant")
    p_card.add_argument("--ledger", default=".argus/argus.db")
    _add_namespace_flag(p_card)
    p_card.add_argument(
        "--mimir-url",
        default=None,
        help="Mimir Prometheus API base; derived from --namespace unless given. "
        "Port-forward form: --mimir-url http://localhost:9009/prometheus",
    )
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
        default=None,
        help="Mimir Prometheus API base; derived from --namespace unless given. "
        "Port-forward form: --mimir-url http://localhost:9009/prometheus",
    )
    p_feedback.set_defaults(func=cmd_feedback)

    # Job mode applies only to `observe`, and only when the launcher's env vars are
    # present - decide that BEFORE parsing, so a parse failure (an unknown flag, a
    # bad choice) can still end with the summary line the launcher requires. Every
    # other subcommand (platform included) is untouched, parse failures included.
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    job_mode = bool(raw_argv) and raw_argv[0] == "observe" and "GOJIRA_CONTRACT_YAML" in os.environ

    if job_mode:
        try:
            args = parser.parse_args(argv)
            inputs = job_inputs(os.environ)
            if inputs is None:
                # GOJIRA_CONTRACT_YAML vanished between the check above and here
                # (a racy environ mutation) - fall back to operator's own error.
                if args.gojira is None or args.env is None:
                    p_observe.error("the following arguments are required: --gojira, --env")
                Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)
                return args.func(args)
            return _observe_job(inputs, args)
        except SystemExit as exc:
            if exc.code:  # argparse error (or p_observe.error above): code 2
                print(
                    failure_summary(None, "bad invocation: argument parsing failed (see stderr)"),
                    flush=True,
                )
                return 1
            raise  # --help exits 0 - not job mode's to report on, let it through
        except Exception as exc:  # KeyboardInterrupt is not caught here; it propagates
            print(failure_summary(None, _exception_reason(exc)), flush=True)
            return 1

    args = parser.parse_args(argv)

    if args.command == "render":
        if args.platform:
            if args.cluster is None or args.env is None:
                p_render.error("--platform requires --cluster and --env")
        elif args.gojira is None or args.env is None:
            p_render.error("the following arguments are required: --gojira, --env")

    if args.command == "observe":
        try:
            inputs = job_inputs(os.environ)
        except JobInputError as exc:
            print(failure_summary(None, _exception_reason(exc)), flush=True)
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
