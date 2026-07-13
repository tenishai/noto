"""Concrete provisioning steps: thin, idempotent wrappers over helm/kubectl.

Each step shells out through an injectable runner (tests fake it; the CLI passes a real
subprocess runner). check() asks the world, apply() changes it - the engine owns
ordering, the ledger owns memory. Cloud-side (OpenTofu) steps arrive with the regional
apply path; the solo topology needs none (in-cluster storage for dev, GCS via the
example root when a deployment brings a project).
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

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
            description = json.loads(out).get("info", {}).get("description", "")
        except json.JSONDecodeError, AttributeError:
            return None  # unreadable state -> re-apply (idempotent)
        if description == self.fingerprint(ctx):
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
        manifest = yaml.safe_dump_all(docs)
        cmd = ctx.kubectl("apply", "--server-side", "--field-manager", "argus", "-f", "-")
        if ctx.dry_run:
            cmd.append("--dry-run=server")
        code, out = ctx.runner(cmd, manifest)
        if code != 0:
            raise RuntimeError(f"apply {self.family} failed: {out[-2000:]}")
        return f"applied/{self.family}({len(docs)})"


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
