"""CLI + concrete steps against a fake runner: every flow end to end, no cluster.

The fake runner records every command; assertions check the real command lines the
steps would execute (pins honored, dry-run threaded, stdin manifests applied).
"""

from pathlib import Path

import yaml

from argus import cli
from argus.engine.engine import ProvisionEngine
from argus.engine.ledger import Ledger
from argus.steps import ApplyRendered, EnsureNamespace, HelmInstall, StepContext

FIXTURES = Path(__file__).parent / "fixtures" / "gojira"
REPO = Path(__file__).parents[2]


class FakeRunner:
    def __init__(self, fail_on: str | None = None, missing: bool = True) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.fail_on = fail_on
        self.missing = missing  # get/status probes report absent

    def __call__(self, cmd: list[str], stdin_text: str | None) -> tuple[int, str]:
        self.calls.append((cmd, stdin_text))
        joined = " ".join(cmd)
        if self.fail_on and self.fail_on in joined:
            return 1, "boom"
        if self.missing and (" get namespace" in joined or "helm status" in joined):
            return 1, "not found"
        return 0, "{}" if "-o json" not in joined else '{"items": []}'


def _ctx(runner: FakeRunner, **kw) -> StepContext:
    pins = yaml.safe_load((REPO / "helm" / "pins.yaml").read_text())
    return StepContext(runner=runner, pins=pins, repo_root=REPO, **kw)


def test_platform_steps_honor_pins_and_order(tmp_path: Path) -> None:
    runner = FakeRunner()
    ledger = Ledger(tmp_path / "l.db")
    steps = [
        EnsureNamespace(),
        HelmInstall(component="cert-manager"),
        HelmInstall(component="mimir"),
    ]
    result = ProvisionEngine(steps).run(ledger, "platform@c1", _ctx(runner))
    assert result.completed
    helm_cmds = [c for c, _ in runner.calls if c[0] == "helm" and c[1] == "upgrade"]
    assert "--version" in helm_cmds[0]
    assert helm_cmds[0][helm_cmds[0].index("--version") + 1] == "v1.21.0"  # cert-manager pin
    assert helm_cmds[1][helm_cmds[1].index("--version") + 1] == "6.1.0"  # mimir pin


def test_helm_status_hit_skips_install(tmp_path: Path) -> None:
    runner = FakeRunner(missing=False)  # status reports the release exists
    ledger = Ledger(tmp_path / "l.db")
    result = ProvisionEngine([HelmInstall(component="mimir")]).run(
        ledger, "platform@c1", _ctx(runner)
    )
    assert result.steps_skipped == ["helm_mimir"]
    assert not any(c[1] == "upgrade" for c, _ in runner.calls if c[0] == "helm")


def test_apply_rendered_pipes_manifest_via_stdin(tmp_path: Path) -> None:
    runner = FakeRunner()
    ledger = Ledger(tmp_path / "l.db")
    rendered = {"scrape": [{"kind": "ServiceMonitor", "metadata": {"name": "x"}}]}
    result = ProvisionEngine([ApplyRendered(family="scrape")]).run(
        ledger, "wire@x", _ctx(runner, rendered=rendered)
    )
    assert result.completed
    cmd, stdin_text = next((c, s) for c, s in runner.calls if "apply" in c)
    assert "--server-side" in cmd
    assert stdin_text is not None and "ServiceMonitor" in stdin_text


def test_dry_run_threads_through(tmp_path: Path) -> None:
    runner = FakeRunner()
    ledger = Ledger(tmp_path / "l.db")
    rendered = {"scrape": [{"kind": "ServiceMonitor"}]}
    ProvisionEngine([ApplyRendered(family="scrape")]).run(
        ledger, "wire@x", _ctx(runner, rendered=rendered, dry_run=True)
    )
    cmd, _ = next((c, s) for c, s in runner.calls if "apply" in c)
    assert "--dry-run=server" in cmd


def test_cli_render_writes_all_families(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    code = cli.main(
        [
            "render",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--out",
            str(out),
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 0
    names = {p.name for p in out.glob("*.yaml")}
    assert {"collectors.yaml", "scrape.yaml", "alerts.yaml", "perses.yaml", "slo.yaml"} <= names
    docs = list(yaml.safe_load_all((out / "collectors.yaml").read_text()))
    assert {d["metadata"]["name"] for d in docs} == {
        "argus-agent",
        "argus-gateway",
        "argus-scraper",
    }


def test_cli_observe_flow_with_fake_runner(tmp_path: Path, monkeypatch) -> None:
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    code = cli.main(
        [
            "observe",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 0
    applies = [c for c, _ in runner.calls if "apply" in c]
    assert len(applies) == 4  # collectors, scrape, alerts, slo
