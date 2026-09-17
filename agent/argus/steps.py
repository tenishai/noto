"""Concrete provisioning steps: thin, idempotent wrappers over helm/kubectl.

Each step shells out through an injectable runner (tests fake it; the CLI passes a real
subprocess runner). check() asks the world, apply() changes it - the engine owns
ordering, the ledger owns memory. Cloud-side (OpenTofu) steps arrive with the regional
apply path; the solo topology needs none (in-cluster storage for dev, GCS via the
example root when a deployment brings a project).
"""

import copy
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Injectable clock/sleep (module-level, monkeypatched in tests) so WaitForCollectors
# is testable without ever really sleeping.
_sleep = time.sleep
_now = time.monotonic

Runner = Callable[[list[str], str | None], tuple[int, str]]
"""Run argv (with optional stdin text), return (exit_code, stdout+stderr)."""


@dataclass
class StepContext:
    runner: Runner
    kube_context: str | None = None
    namespace: str = "argus"
    pins: dict = field(default_factory=dict)  # helm/pins.yaml, loaded
    repo_root: Path = Path(".")
    rendered: dict[str, list[dict]] = field(default_factory=dict)  # renderer output by family
    dry_run: bool = False
    # Job mode's fingerprint of the contract argus is observing (empty in operator
    # mode). ApplyRendered stamps these onto every document it applies, so the
    # cluster itself can answer "has the app changed since argus looked".
    annotations: dict[str, str] = field(default_factory=dict)

    def kubectl(self, *args: str) -> list[str]:
        cmd = ["kubectl"]
        if self.kube_context:
            cmd += ["--context", self.kube_context]
        return [*cmd, *args]


@dataclass
class EnsureNamespace:
    name: str = "namespace"
    tooling: str = "cli"

    def check(self, ctx: StepContext) -> str | None:
        code, _ = ctx.runner(ctx.kubectl("get", "namespace", ctx.namespace), None)
        return f"namespace/{ctx.namespace}" if code == 0 else None

    def apply(self, ctx: StepContext) -> str:
        code, out = ctx.runner(ctx.kubectl("create", "namespace", ctx.namespace), None)
        if code != 0:
            raise RuntimeError(f"create namespace failed: {out}")
        return f"namespace/{ctx.namespace}"


@dataclass
class HelmInstall:
    """Install one helm/pins.yaml component at its pinned version with its values file.

    Drift-aware (issue #46): a fingerprint of (chart version + values file content +
    extra_sets) is stored in the helm release description; check() re-verifies it every
    run (ephemeral), so a pin bump or values edit re-applies instead of ledger-skipping.
    The fingerprint lives in helm's own state - it survives ledger loss.
    """

    component: str
    tooling: str = "helm"
    ephemeral: bool = True  # DONE never skips; drift is re-decided against helm state
    extra_sets: dict[str, str] = field(default_factory=dict)  # seam outputs at apply time

    @property
    def name(self) -> str:
        return f"helm_{self.component}"

    def _pin(self, ctx: StepContext) -> dict:
        try:
            return ctx.pins["components"][self.component]
        except KeyError as e:
            raise RuntimeError(f"component {self.component!r} not in helm/pins.yaml") from e

    def fingerprint(self, ctx: StepContext) -> str:
        pin = self._pin(ctx)
        values = (ctx.repo_root / "helm" / pin["values"]).read_bytes()
        material = json.dumps(
            {
                "chart": pin["chart"],
                "version": str(pin["version"]),
                "values_sha": hashlib.sha256(values).hexdigest(),
                "sets": dict(sorted(self.extra_sets.items())),
            },
            sort_keys=True,
        )
        return "argus-fp:" + hashlib.sha256(material.encode()).hexdigest()[:16]

    def check(self, ctx: StepContext) -> str | None:
        code, out = ctx.runner(
            [
                "helm",
                "status",
                f"argus-{self.component}",
                "--namespace",
                ctx.namespace,
                "-o",
                "json",
            ],
            None,
        )
        if code != 0:
            return None  # not installed
        try:
            info = json.loads(out).get("info", {})
        # Bare multi-exception except is PEP 758 (valid on our pinned 3.14; ruff format
        # strips the parens). Pre-3.14 interpreters and tools mis-read it as a
        # SyntaxError - it is not one; test with `uv run python`, never system python3.
        except json.JSONDecodeError, AttributeError:
            return None  # unreadable state -> re-apply (idempotent)
        if info.get("status") != "deployed":
            # A prior --wait timeout (or any interrupted install/upgrade) leaves the
            # release "failed"/"pending-*" even though --description already stamped
            # the fingerprint on it. Matching description alone would wrongly mark this
            # step done; only a release that actually reached "deployed" counts.
            return None  # stuck/failed release -> re-apply (idempotent)
        if info.get("description", "") == self.fingerprint(ctx):
            return f"helmrelease/argus-{self.component}"
        return None  # installed but drifted -> upgrade

    def apply(self, ctx: StepContext) -> str:
        pin = self._pin(ctx)
        cmd = [
            "helm",
            "upgrade",
            "--install",
            f"argus-{self.component}",
            pin["chart"],
            "--repo",
            pin["repo"],
            "--version",
            str(pin["version"]),
            "-f",
            str(ctx.repo_root / "helm" / pin["values"]),
            "--namespace",
            ctx.namespace,
            "--create-namespace",
            "--wait",
            "--description",
            self.fingerprint(ctx),
        ]
        for key, value in self.extra_sets.items():
            cmd += ["--set", f"{key}={value}"]
        if ctx.dry_run:
            cmd.append("--dry-run=server")
        code, out = ctx.runner(cmd, None)
        if code != 0:
            raise RuntimeError(f"helm install {self.component} failed: {out[-2000:]}")
        return f"helmrelease/argus-{self.component}"


@dataclass
class ApplyRendered:
    """kubectl-apply one rendered artifact family (collectors, scrape, alerts, slo)."""

    family: str
    tooling: str = "crd"
    ephemeral: bool = True  # rendered output must reconcile every run, never ledger-skip

    @property
    def name(self) -> str:
        return f"apply_{self.family}"

    def check(self, ctx: StepContext) -> str | None:
        return None  # apply is the reconcile; server-side apply is idempotent

    def apply(self, ctx: StepContext) -> str:
        docs = ctx.rendered.get(self.family)
        if not docs:
            raise RuntimeError(f"nothing rendered for family {self.family!r}")
        to_apply = [_annotated(doc, ctx.annotations) for doc in docs]
        manifest = yaml.safe_dump_all(to_apply)
        cmd = ctx.kubectl("apply", "--server-side", "--field-manager", "argus", "-f", "-")
        if ctx.dry_run:
            cmd.append("--dry-run=server")
        code, out = ctx.runner(cmd, manifest)
        if code != 0:
            raise RuntimeError(f"apply {self.family} failed: {out[-2000:]}")
        return f"applied/{self.family}({len(docs)})"


def _annotated(doc: dict, annotations: dict[str, str]) -> dict:
    """A copy of doc with annotations merged into metadata.annotations.

    Never mutates doc - rendered families are reused across steps and runs. With no
    annotations to add, the copy is returned untouched, so the YAML this produces
    stays byte-identical to a document that was never passed through here at all.
    Our keys always win on conflict; every other existing annotation survives.
    """
    doc = copy.deepcopy(doc)
    if not annotations:
        return doc
    metadata = doc.setdefault("metadata", {})
    existing = metadata.get("annotations") or {}
    metadata["annotations"] = {**existing, **annotations}
    return doc


_OPERATOR_MODE_KIND = {
    "daemonset": "DaemonSet",
    "deployment": "Deployment",
    "statefulset": "StatefulSet",
}


def collector_workloads(docs: list[dict]) -> list[tuple[str, str, str]]:
    """The k8s workloads the OpenTelemetry operator creates for a set of rendered
    documents - derived from the documents themselves, never hardcoded, so a renamed
    or added collector role is picked up automatically.

    Verified against a live cluster: an OpenTelemetryCollector named X with
    spec.mode: daemonset becomes DaemonSet X-collector; mode: deployment (the
    operator's own default when mode is absent) becomes Deployment X-collector;
    mode: statefulset becomes StatefulSet X-collector. When the CR turns on
    spec.targetAllocator.enabled, the operator additionally creates a Deployment
    named X-targetallocator (this repo renders the target allocator this way, inside
    the "scraper" collector CR, rather than as a separate TargetAllocator CR).

    Returns (kind, name, namespace) tuples, in document order.
    """
    workloads: list[tuple[str, str, str]] = []
    for doc in docs:
        if doc.get("kind") != "OpenTelemetryCollector":
            continue
        name = doc["metadata"]["name"]
        namespace = doc["metadata"].get("namespace", "argus")
        spec = doc.get("spec") or {}
        mode = spec.get("mode", "deployment")
        kind = _OPERATOR_MODE_KIND.get(mode, "Deployment")
        workloads.append((kind, f"{name}-collector", namespace))
        if (spec.get("targetAllocator") or {}).get("enabled"):
            workloads.append(("Deployment", f"{name}-targetallocator", namespace))
    return workloads


def _collapse(text: str) -> str:
    """Last 500 characters of `text`, collapsed to one line (a raised RuntimeError
    should never carry an embedded newline into the step engine's error field)."""
    return " ".join(text[-500:].split())


@dataclass
class WaitForCollectors:
    """Between applying the collector tier and ValidateRoundTrip: the collector CRs
    are applied with a bare `kubectl apply --server-side` (no --wait, unlike
    HelmInstall), so the moment they land the OpenTelemetry operator still has to
    turn them into workloads and pull images. ValidateRoundTrip fails on ANY pod in
    the namespace that isn't Running/Succeeded, so without this step a fresh install
    (or an upgrade that rolls the DaemonSet) fails its first run on ordinary
    Pending/ContainerCreating - not a real problem, just a race.

    The workloads to wait for come from the rendered "collectors" family itself
    (collector_workloads), never a hardcoded list.
    """

    name: str = "wait_collectors"
    tooling: str = "cli"
    ephemeral: bool = True
    timeout: float = 300.0  # overall deadline for the whole step, all workloads

    def check(self, ctx: StepContext) -> str | None:
        return None  # always re-decided; a re-run must re-wait for a rolling upgrade

    def apply(self, ctx: StepContext) -> str:
        workloads = collector_workloads(ctx.rendered.get("collectors", []))
        if ctx.dry_run:
            named = ", ".join(f"{kind}/{name}" for kind, name, _ns in workloads)
            return f"would wait for {len(workloads)} workloads: {named}"
        deadline = _now() + self.timeout
        for kind, name, namespace in workloads:
            self._wait_one(ctx, kind, name, namespace, deadline)
        return f"collectors ready: {len(workloads)} workloads"

    def _wait_one(
        self, ctx: StepContext, kind: str, name: str, namespace: str, deadline: float
    ) -> None:
        workload = f"{kind}/{name}"
        last_out = ""
        while True:
            remaining = deadline - _now()
            if remaining <= 0:
                raise RuntimeError(
                    f"timed out waiting for {workload} in {namespace}: {_collapse(last_out)}"
                )
            cmd = ctx.kubectl(
                "rollout",
                "status",
                workload,
                "-n",
                namespace,
                "--timeout",
                f"{max(1, int(remaining))}s",
            )
            code, out = ctx.runner(cmd, None)
            if code == 0:
                return
            last_out = out
            if "NotFound" in out or "not found" in out:
                _sleep(3)
                continue
            raise RuntimeError(f"waiting for {workload} in {namespace} failed: {_collapse(out)}")


@dataclass
class ValidateRoundTrip:
    """The DoD probe: backends healthy and the collector is scraping itself."""

    name: str = "validate"
    tooling: str = "cli"
    ephemeral: bool = True

    def check(self, ctx: StepContext) -> str | None:
        return None

    def apply(self, ctx: StepContext) -> str:
        code, out = ctx.runner(ctx.kubectl("get", "pods", "-n", ctx.namespace, "-o", "json"), None)
        if code != 0:
            raise RuntimeError(f"cluster unreachable: {out}")
        pods = json.loads(out)["items"]
        not_ready = [
            p["metadata"]["name"]
            for p in pods
            if p.get("status", {}).get("phase") not in ("Running", "Succeeded")
        ]
        if not_ready:
            raise RuntimeError(f"pods not ready: {not_ready}")
        return f"healthy/{len(pods)} pods"
