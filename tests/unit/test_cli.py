"""CLI + concrete steps against a fake runner: every flow end to end, no cluster.

The fake runner records every command; assertions check the real command lines the
steps would execute (pins honored, dry-run threaded, stdin manifests applied).
"""

import json
from pathlib import Path

import yaml

from argus import cli
from argus.engine.engine import ProvisionEngine
from argus.engine.ledger import Ledger
from argus.steps import ApplyRendered, EnsureNamespace, HelmInstall, StepContext

FIXTURES = Path(__file__).parent / "fixtures" / "gojira"
REPO = Path(__file__).parents[2]


class FakeRunner:
    def __init__(
        self,
        fail_on: str | None = None,
        missing: bool = True,
        release_description: str = "",
        release_status: str = "deployed",
    ) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.fail_on = fail_on
        self.missing = missing  # get/status probes report absent
        self.release_description = release_description  # helm status description when present
        self.release_status = release_status  # helm status .info.status when present

    def __call__(self, cmd: list[str], stdin_text: str | None) -> tuple[int, str]:
        self.calls.append((cmd, stdin_text))
        joined = " ".join(cmd)
        if self.fail_on and self.fail_on in joined:
            return 1, "boom"
        if self.missing and (" get namespace" in joined or "helm status" in joined):
            return 1, "not found"
        if "helm status" in joined:
            info = {"description": self.release_description, "status": self.release_status}
            return 0, json.dumps({"info": info})
        return 0, "{}" if "-o json" not in joined else '{"items": []}'


def _fake_perses_opener(req) -> tuple[int, bytes]:
    """Every GET 404s (nothing provisioned yet), every POST/PUT succeeds - enough for
    tests where ProvisionPerses only rides along with an `observe` run; its own
    behavior (create/conflict/unchanged/errors) is covered by test_perses_step.py."""
    return (404, b"") if req.get_method() == "GET" else (200, b"{}")


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


def test_helm_matching_fingerprint_skips_install(tmp_path: Path) -> None:
    step = HelmInstall(component="mimir")
    probe = FakeRunner()
    fp = step.fingerprint(_ctx(probe))
    runner = FakeRunner(missing=False, release_description=fp)
    ledger = Ledger(tmp_path / "l.db")
    result = ProvisionEngine([step]).run(ledger, "platform@c1", _ctx(runner))
    assert result.steps_skipped == ["helm_mimir"]
    assert not any(c[1] == "upgrade" for c, _ in runner.calls if c[0] == "helm")


def test_helm_timed_out_release_reapplies_despite_matching_fingerprint(tmp_path: Path) -> None:
    """A --wait timeout (or any interrupted apply) leaves helm status "failed"/"pending-*"
    even though --description already stamped the fingerprint on the release. Matching
    description alone must not be read as done - only a "deployed" release counts - or a
    resumed run silently ledger-skips a mimir install that never actually came up, and the
    only signal left is a confusing failure later in the unrelated `validate` step."""
    step = HelmInstall(component="mimir")
    probe = FakeRunner()
    fp = step.fingerprint(_ctx(probe))
    runner = FakeRunner(missing=False, release_description=fp, release_status="failed")
    ledger = Ledger(tmp_path / "l.db")
    result = ProvisionEngine([step]).run(ledger, "platform@c1", _ctx(runner))
    assert result.steps_run == ["helm_mimir"]
    assert any(c[1] == "upgrade" for c, _ in runner.calls if c[0] == "helm")


def test_helm_drift_triggers_upgrade(tmp_path: Path) -> None:
    """Issue #46: an installed release with a stale fingerprint (values/pin/sets changed)
    re-applies instead of ledger-skipping - the gap that cost two manual upgrades live."""
    step = HelmInstall(component="mimir")
    runner = FakeRunner(missing=False, release_description="argus-fp:stale00000000000")
    ledger = Ledger(tmp_path / "l.db")
    result = ProvisionEngine([step]).run(ledger, "platform@c1", _ctx(runner))
    assert result.steps_run == ["helm_mimir"]
    upgrade = next(c for c, _ in runner.calls if c[0] == "helm" and c[1] == "upgrade")
    idx = upgrade.index("--description")
    assert upgrade[idx + 1].startswith("argus-fp:")


def test_helm_rerun_after_values_edit_reapplies(tmp_path: Path) -> None:
    """Even a DONE ledger row does not mask drift (ephemeral): second engine run with a
    changed fingerprint upgrades."""
    step = HelmInstall(component="mimir")
    probe = FakeRunner()
    fp = step.fingerprint(_ctx(probe))
    ledger = Ledger(tmp_path / "l.db")
    r1 = ProvisionEngine([step]).run(
        ledger, "platform@c1", _ctx(FakeRunner(missing=False, release_description=fp))
    )
    assert r1.steps_skipped == ["helm_mimir"]
    # simulate a values edit: live release still carries the OLD fingerprint
    r2 = ProvisionEngine([step]).run(
        ledger,
        "platform@c1",
        _ctx(FakeRunner(missing=False, release_description="argus-fp:oldoldoldoldold1")),
    )
    assert r2.steps_run == ["helm_mimir"]


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


def test_apply_rendered_with_empty_annotations_is_byte_identical_to_today(
    tmp_path: Path,
) -> None:
    """StepContext.annotations defaults to {}; job mode is the only caller that ever
    sets it. Operator mode (and the golden-file suite) must see the exact same
    stdin manifest as before this field existed."""
    runner = FakeRunner()
    ledger = Ledger(tmp_path / "l.db")
    docs = [
        {"kind": "ServiceMonitor", "metadata": {"name": "x"}},
        {"kind": "PodMonitor", "metadata": {"name": "y", "annotations": {"keep": "me"}}},
    ]
    rendered = {"scrape": docs}
    ProvisionEngine([ApplyRendered(family="scrape")]).run(
        ledger, "wire@x", _ctx(runner, rendered=rendered)
    )
    _, stdin_text = next((c, s) for c, s in runner.calls if "apply" in c)
    assert stdin_text == yaml.safe_dump_all(docs)


def test_apply_rendered_annotates_documents_with_the_observed_contract(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    ledger = Ledger(tmp_path / "l.db")
    docs = [{"kind": "ServiceMonitor", "metadata": {"name": "x"}}]
    rendered = {"scrape": docs}
    sha = "a" * 64
    ProvisionEngine([ApplyRendered(family="scrape")]).run(
        ledger,
        "wire@x",
        _ctx(runner, rendered=rendered, annotations={"argus.dev/contract-sha256": sha}),
    )
    _, stdin_text = next((c, s) for c, s in runner.calls if "apply" in c)
    applied = list(yaml.safe_load_all(stdin_text))
    assert applied[0]["metadata"]["annotations"] == {"argus.dev/contract-sha256": sha}


def test_apply_rendered_annotation_survives_alongside_unrelated_annotations(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    ledger = Ledger(tmp_path / "l.db")
    docs = [
        {
            "kind": "ServiceMonitor",
            "metadata": {
                "name": "x",
                "annotations": {
                    "unrelated/keep": "yes",
                    "argus.dev/contract-sha256": "stale" * 12 + "0000",
                },
            },
        }
    ]
    rendered = {"scrape": docs}
    sha = "b" * 64
    ProvisionEngine([ApplyRendered(family="scrape")]).run(
        ledger,
        "wire@x",
        _ctx(runner, rendered=rendered, annotations={"argus.dev/contract-sha256": sha}),
    )
    _, stdin_text = next((c, s) for c, s in runner.calls if "apply" in c)
    applied = list(yaml.safe_load_all(stdin_text))
    annotations = applied[0]["metadata"]["annotations"]
    assert annotations["unrelated/keep"] == "yes"
    assert annotations["argus.dev/contract-sha256"] == sha


def test_apply_rendered_never_mutates_the_callers_rendered_documents(
    tmp_path: Path,
) -> None:
    """Rendered families are reused across steps/runs - apply() must copy, not touch,
    the caller's objects, even for a document that starts with no metadata at all."""
    runner = FakeRunner()
    ledger = Ledger(tmp_path / "l.db")
    original_doc = {"kind": "ServiceMonitor"}
    docs = [original_doc]
    rendered = {"scrape": docs}
    ProvisionEngine([ApplyRendered(family="scrape")]).run(
        ledger,
        "wire@x",
        _ctx(runner, rendered=rendered, annotations={"argus.dev/contract-sha256": "c" * 64}),
    )
    assert original_doc == {"kind": "ServiceMonitor"}
    assert "metadata" not in original_doc


def test_cli_render_writes_the_per_app_families(tmp_path: Path) -> None:
    """Per-app render is per-app only: the shared collector tier is the platform's."""
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
    assert {"scrape.yaml", "alerts.yaml", "perses.yaml", "slo.yaml"} <= names
    assert "collectors.yaml" not in names
    for path in out.glob("*.yaml"):
        for doc in yaml.safe_load_all(path.read_text()):
            assert doc["kind"] != "OpenTelemetryCollector", path.name


def test_cli_render_defaults_out_dir_per_mode(tmp_path: Path, monkeypatch) -> None:
    """--out is shared by both render modes but one default cannot fit both: a
    per-app render and a --platform render must not collide on the same directory
    when neither passes --out."""
    monkeypatch.chdir(tmp_path)
    code = cli.main(
        [
            "render",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l1.db"),
        ]
    )
    assert code == 0
    assert (tmp_path / ".argus" / "rendered-app" / "alerts.yaml").exists()

    code = cli.main(
        [
            "render",
            "--platform",
            "--cluster",
            "c1",
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l2.db"),
        ]
    )
    assert code == 0
    assert (tmp_path / ".argus" / "rendered-platform" / "collectors.yaml").exists()
    assert not (tmp_path / ".argus" / "rendered-app" / "collectors.yaml").exists()


def test_cli_render_platform_writes_the_collector_tier(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    code = cli.main(
        [
            "render",
            "--platform",
            "--cluster",
            "c1",
            "--env",
            "dev",
            "--out",
            str(out),
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 0
    assert {p.name for p in out.glob("*.yaml")} == {"collectors.yaml"}
    docs = list(yaml.safe_load_all((out / "collectors.yaml").read_text()))
    names = {d["metadata"]["name"] for d in docs}
    assert {"argus-agent", "argus-gateway", "argus-scraper", "argus-targetallocator"} <= names
    kinds = {d["kind"] for d in docs}
    assert {"ServiceAccount", "ClusterRole", "ClusterRoleBinding", "PriorityClass"} <= kinds
    gateway = next(d for d in docs if d["metadata"]["name"] == "argus-gateway")
    assert set(gateway["spec"]["config"]["service"]["pipelines"]) == {
        "metrics",
        "logs",
        "traces",
        "metrics/self",
    }


def test_cli_platform_applies_the_collector_tier_after_the_helm_installs(
    tmp_path: Path, monkeypatch
) -> None:
    """The collector CRs need the operator's CRDs and webhook, which arrive with the
    opentelemetry-operator helm step - so the apply must land after every helm step."""
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    code = cli.main(
        [
            "platform",
            "--cluster",
            "c1",
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 0
    verbs = [
        (c[0], c[1])
        for c, _ in runner.calls
        if (c[0] == "helm" and c[1] == "upgrade") or "apply" in c
    ]
    applies = [i for i, v in enumerate(verbs) if v[0] == "kubectl"]
    helms = [i for i, v in enumerate(verbs) if v[0] == "helm"]
    assert len(applies) == 1
    assert applies[0] > max(helms)
    _cmd, stdin_text = next((c, s) for c, s in runner.calls if "apply" in c)
    kinds = {d["kind"] for d in yaml.safe_load_all(stdin_text)}
    assert "OpenTelemetryCollector" in kinds


def test_cli_platform_step_order_waits_before_validating(tmp_path: Path, monkeypatch) -> None:
    """collectors apply -> wait_collectors -> validate, in that order: the collector
    apply has no --wait, so validate must not race the operator standing up pods."""
    from argus.engine.engine import ProvisionEngine as RealEngine

    captured: list[list[str]] = []

    class SpyEngine(RealEngine):
        def __init__(self, steps):
            captured.append([s.name for s in steps])
            super().__init__(steps)

    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "ProvisionEngine", SpyEngine)
    code = cli.main(
        ["platform", "--cluster", "c1", "--env", "dev", "--ledger", str(tmp_path / "l.db")]
    )
    assert code == 0
    assert captured
    names = captured[0]
    assert (
        names.index("apply_collectors") < names.index("wait_collectors") < names.index("validate")
    )


def test_cli_platform_requires_an_environment(tmp_path: Path) -> None:
    """The gateway stamps `environment` on everything it forwards; a silent default
    would mislabel a whole cluster's telemetry."""
    import pytest as _pytest

    with _pytest.raises(SystemExit) as exc_info:
        cli.main(["platform", "--cluster", "c1", "--ledger", str(tmp_path / "l.db")])
    assert exc_info.value.code == 2


def test_ignored_drop_rule_names_covers_added_changed_and_disabled() -> None:
    from argus.profiles.models import DropRule

    platform = [
        DropRule(name="health-endpoints", stage="agent", path_matches=["/healthz"]),
        DropRule(name="ksm-churn", stage="gateway", metric_names=["a"]),
    ]
    app = [
        DropRule(name="ksm-churn", stage="gateway", metric_names=["b"]),  # changed
        DropRule(name="app-only", stage="gateway", metric_names=["c"]),  # added
        # "health-endpoints" is simply absent: disabled by the app's class/override
    ]
    ignored = cli._ignored_drop_rule_names(app, platform)
    assert ignored == ["app-only", "health-endpoints", "ksm-churn"]


def test_ignored_drop_rule_names_empty_when_identical() -> None:
    from argus.profiles.models import DropRule

    rules = [DropRule(name="x", stage="agent", path_matches=["/y"])]
    assert cli._ignored_drop_rule_names(list(rules), list(rules)) == []


def test_no_drop_deltas_warns_nothing(tmp_path: Path, capsys) -> None:
    """The stock web fixture carries no observability.drop override, so its resolved
    drop rules are exactly the platform's (baseline-only) - nothing to warn about."""
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
    assert "platform-owned" not in capsys.readouterr().err


def test_drop_override_adding_a_rule_warns_and_names_it(tmp_path: Path, capsys) -> None:
    gojira = tmp_path / "gojira.yaml"
    gojira.write_text(
        "app: demo\nclass: web\n"
        "environments:\n  - name: dev\n    cluster: c1\n    namespace: demo\n"
        "observability:\n  drop:\n    - name: extra-noise\n      stage: gateway\n"
        "      metric_names: ['foo_.*']\n"
    )
    code = cli.main(
        [
            "render",
            "--gojira",
            str(gojira),
            "--env",
            "dev",
            "--out",
            str(tmp_path / "rendered"),
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 0
    err = capsys.readouterr().err
    assert "warning: drop rules are platform-owned now" in err
    assert "ignored for demo" in err
    assert "extra-noise" in err
    assert "\n" not in err.strip()  # one line


def test_drop_override_disabling_a_baseline_rule_warns_and_names_it(tmp_path: Path, capsys) -> None:
    gojira = tmp_path / "gojira.yaml"
    gojira.write_text(
        "app: demo\nclass: web\n"
        "environments:\n  - name: dev\n    cluster: c1\n    namespace: demo\n"
        "observability:\n  drop:\n    - name: health-endpoints\n      stage: agent\n"
        "      enabled: false\n"
    )
    code = cli.main(
        [
            "render",
            "--gojira",
            str(gojira),
            "--env",
            "dev",
            "--out",
            str(tmp_path / "rendered"),
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 0
    err = capsys.readouterr().err
    assert "warning: drop rules are platform-owned now" in err
    assert "health-endpoints" in err


def test_cli_observe_flow_with_fake_runner(tmp_path: Path, monkeypatch) -> None:
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    # sloth generation has its own binary-gated test below
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
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
    assert len(applies) == 3  # scrape, alerts, slo - never the shared collector tier


def test_cli_observe_operator_mode_applies_no_contract_annotation(
    tmp_path: Path, monkeypatch
) -> None:
    """Operator mode has no contract to record - it must set no annotation at all."""
    monkeypatch.delenv("GOJIRA_CONTRACT_YAML", raising=False)
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
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
    applies = [(c, s) for c, s in runner.calls if "apply" in c]
    assert applies
    for _cmd, stdin_text in applies:
        for doc in yaml.safe_load_all(stdin_text):
            assert "argus.dev/contract-sha256" not in doc.get("metadata", {}).get("annotations", {})


def test_cli_observe_operator_mode_progress_on_stdout_no_summary_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Operator mode must behave exactly as before job mode existed: progress lines
    (skipped/applied/done) go to stdout, and there is never an ARGUS_SUMMARY line -
    that line belongs to job mode only."""
    monkeypatch.delenv("GOJIRA_CONTRACT_YAML", raising=False)
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
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
    captured = capsys.readouterr()
    assert "applied" in captured.out
    assert "done" in captured.out
    assert "ARGUS_SUMMARY" not in captured.out
    assert "ARGUS_SUMMARY" not in captured.err


import pytest  # noqa: E402

# --- in-cluster endpoint derivation: --namespace / ARGUS_NAMESPACE feed --mimir-url
# and --perses-url unless the flag is given explicitly ---


def test_cardinality_mimir_url_derives_from_default_namespace(monkeypatch) -> None:
    captured: dict = {}

    def fake_top_metric_names(mimir_base, tenant, limit=15):
        captured["mimir_base"] = mimir_base
        return [("foo", 5)]

    monkeypatch.setattr("argus.feedback.cardinality.top_metric_names", fake_top_metric_names)
    code = cli.main(["cardinality", "--tenant", "t1"])
    assert code == 0
    assert captured["mimir_base"] == "http://argus-mimir-gateway.argus.svc:80/prometheus"


def test_cardinality_explicit_mimir_url_wins_over_derived_default(monkeypatch) -> None:
    captured: dict = {}

    def fake_top_metric_names(mimir_base, tenant, limit=15):
        captured["mimir_base"] = mimir_base
        return []

    monkeypatch.setattr("argus.feedback.cardinality.top_metric_names", fake_top_metric_names)
    code = cli.main(
        ["cardinality", "--tenant", "t1", "--mimir-url", "http://localhost:9009/prometheus"]
    )
    assert code == 0
    assert captured["mimir_base"] == "http://localhost:9009/prometheus"


def test_cardinality_namespace_flag_changes_the_derived_default(monkeypatch) -> None:
    captured: dict = {}

    def fake_top_metric_names(mimir_base, tenant, limit=15):
        captured["mimir_base"] = mimir_base
        return []

    monkeypatch.setattr("argus.feedback.cardinality.top_metric_names", fake_top_metric_names)
    code = cli.main(["cardinality", "--tenant", "t1", "--namespace", "obs"])
    assert code == 0
    assert captured["mimir_base"] == "http://argus-mimir-gateway.obs.svc:80/prometheus"


def test_cardinality_argus_namespace_env_feeds_the_derived_default(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_NAMESPACE", "envns")
    captured: dict = {}

    def fake_top_metric_names(mimir_base, tenant, limit=15):
        captured["mimir_base"] = mimir_base
        return []

    monkeypatch.setattr("argus.feedback.cardinality.top_metric_names", fake_top_metric_names)
    code = cli.main(["cardinality", "--tenant", "t1"])
    assert code == 0
    assert captured["mimir_base"] == "http://argus-mimir-gateway.envns.svc:80/prometheus"


def test_rules_sync_mimir_url_derives_from_namespace(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_sync_rules(mimir_url, tenant, namespace, docs):
        captured["mimir_url"] = mimir_url
        return 0

    monkeypatch.setattr("argus.feedback.ruler.sync_rules", fake_sync_rules)
    code = cli.main(
        [
            "rules-sync",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 0
    assert captured["mimir_url"] == "http://argus-mimir-gateway.argus.svc:80/prometheus"


def test_rules_sync_explicit_mimir_url_wins(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_sync_rules(mimir_url, tenant, namespace, docs):
        captured["mimir_url"] = mimir_url
        return 0

    monkeypatch.setattr("argus.feedback.ruler.sync_rules", fake_sync_rules)
    code = cli.main(
        [
            "rules-sync",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            "--mimir-url",
            "http://localhost:9009/prometheus",
        ]
    )
    assert code == 0
    assert captured["mimir_url"] == "http://localhost:9009/prometheus"


def test_feedback_mimir_url_derives_from_namespace(monkeypatch, tmp_path: Path, capsys) -> None:
    captured: dict = {}

    def fake_collect(client, app, namespace, window_days):
        captured["base_url"] = client.base_url
        return {
            "cpu_samples": [0.1],
            "memory_samples": [1024.0],
            "cpu_request": 0.1,
            "cpu_limit": 0.2,
            "memory_request": 1024.0,
            "memory_limit": 2048.0,
            "restarts": 0,
            "oom_kills": 0,
        }

    monkeypatch.setattr("argus.feedback.mimir.collect", fake_collect)
    code = cli.main(
        [
            "feedback",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            "--dry-run",
        ]
    )
    assert code == 0
    assert captured["base_url"] == "http://argus-mimir-gateway.argus.svc:80/prometheus"


def test_feedback_explicit_mimir_url_wins(monkeypatch, tmp_path: Path, capsys) -> None:
    captured: dict = {}

    def fake_collect(client, app, namespace, window_days):
        captured["base_url"] = client.base_url
        return {
            "cpu_samples": [0.1],
            "memory_samples": [1024.0],
            "cpu_request": 0.1,
            "cpu_limit": 0.2,
            "memory_request": 1024.0,
            "memory_limit": 2048.0,
            "restarts": 0,
            "oom_kills": 0,
        }

    monkeypatch.setattr("argus.feedback.mimir.collect", fake_collect)
    code = cli.main(
        [
            "feedback",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            "--dry-run",
            "--mimir-url",
            "http://localhost:9009/prometheus",
        ]
    )
    assert code == 0
    assert captured["base_url"] == "http://localhost:9009/prometheus"


def test_observe_perses_url_flag_reaches_the_step_context(tmp_path: Path, monkeypatch) -> None:
    from argus.engine.engine import ProvisionEngine as RealEngine

    captured: dict = {}

    class SpyEngine(RealEngine):
        def run(self, ledger, name, ctx):
            captured["step_ctx"] = ctx
            return super().run(ledger, name, ctx)

    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "ProvisionEngine", SpyEngine)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    code = cli.main(
        [
            "observe",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            "--perses-url",
            "http://localhost:8080",
        ]
    )
    assert code == 0
    assert captured["step_ctx"].perses_url == "http://localhost:8080"


def test_observe_perses_url_derives_from_namespace_when_not_given(
    tmp_path: Path, monkeypatch
) -> None:
    from argus.engine.engine import ProvisionEngine as RealEngine

    captured: dict = {}

    class SpyEngine(RealEngine):
        def run(self, ledger, name, ctx):
            captured["step_ctx"] = ctx
            return super().run(ledger, name, ctx)

    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "ProvisionEngine", SpyEngine)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    code = cli.main(
        [
            "observe",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            "--namespace",
            "obs",
        ]
    )
    assert code == 0
    assert captured["step_ctx"].perses_url == "http://argus-perses.obs.svc:8080"


def test_observe_argus_perses_url_env_wins_over_derived_default(
    tmp_path: Path, monkeypatch
) -> None:
    from argus.engine.engine import ProvisionEngine as RealEngine

    captured: dict = {}

    class SpyEngine(RealEngine):
        def run(self, ledger, name, ctx):
            captured["step_ctx"] = ctx
            return super().run(ledger, name, ctx)

    monkeypatch.setenv("ARGUS_PERSES_URL", "http://argus-perses.envset:9999")
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "ProvisionEngine", SpyEngine)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
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
    assert captured["step_ctx"].perses_url == "http://argus-perses.envset:9999"


def test_observe_explicit_perses_url_flag_wins_over_env(tmp_path: Path, monkeypatch) -> None:
    from argus.engine.engine import ProvisionEngine as RealEngine

    captured: dict = {}

    class SpyEngine(RealEngine):
        def run(self, ledger, name, ctx):
            captured["step_ctx"] = ctx
            return super().run(ledger, name, ctx)

    monkeypatch.setenv("ARGUS_PERSES_URL", "http://argus-perses.envset:9999")
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "ProvisionEngine", SpyEngine)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    code = cli.main(
        [
            "observe",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            "--perses-url",
            "http://localhost:8080",
        ]
    )
    assert code == 0
    assert captured["step_ctx"].perses_url == "http://localhost:8080"


def test_observe_step_order_ends_with_provision_perses_operator_mode(
    tmp_path: Path, monkeypatch
) -> None:
    """Dashboards land last: they chart the alerts/SLOs applied earlier in the same
    run, so provisioning them first would leave dashboards pointing at rules that
    are not there yet."""
    from argus.engine.engine import ProvisionEngine as RealEngine

    captured: list[list[str]] = []

    class SpyEngine(RealEngine):
        def __init__(self, steps):
            captured.append([s.name for s in steps])
            super().__init__(steps)

    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "ProvisionEngine", SpyEngine)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
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
    assert captured
    assert captured[0][-1] == "provision-perses"


def test_platform_namespace_flag_threads_into_step_context(tmp_path: Path, monkeypatch) -> None:
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    code = cli.main(
        [
            "platform",
            "--cluster",
            "c1",
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            "--namespace",
            "obs",
        ]
    )
    assert code == 0
    ns_cmds = [c for c, _ in runner.calls if "namespace" in c]
    assert any("obs" in c for c in ns_cmds)


@pytest.mark.skipif(
    not (REPO / ".argus" / "tools" / "sloth-0.16.0").exists(),
    reason="pinned sloth binary not fetched (run `make validate`)",
)
def test_generate_slo_rules_via_real_sloth() -> None:
    """Sloth-as-generator: our rendered spec in, PrometheusRule out (design decision)."""
    from argus.profiles.loader import load_baseline, load_class, load_override
    from argus.profiles.resolve import resolve
    from argus.rendering.context import Context
    from argus.rendering.slo import render_sloth

    p = resolve(
        load_baseline(REPO / "profiles" / "baseline.yaml"),
        load_class(REPO / "profiles" / "classes" / "web.yaml"),
        load_override(None),
    )
    ctx = Context(topology="solo", cluster="c1", environment="dev", app="web1", namespace="s")
    doc = render_sloth(p, ctx)
    assert doc is not None
    rules = cli._generate_slo_rules(doc, "s")
    assert rules and rules[0]["kind"] == "PrometheusRule"
    assert rules[0]["metadata"]["namespace"] == "s"
