"""Contract fingerprint, job-mode env inputs, and the ARGUS_SUMMARY line."""

import hashlib
import json
from pathlib import Path

import pytest

from noto import cli
from noto.engine.ledger import Ledger
from noto.job import (
    SUMMARY_MAX,
    JobInputError,
    contract_fingerprint,
    failure_summary,
    in_cluster_kubeconfig,
    job_inputs,
    success_summary,
    summary_line,
)
from noto.steps import StepContext

A = "app: demo\nclass: web\nenvironments:\n  dev: {cluster: c1, namespace: demo}\n"

FIXTURES = Path(__file__).parent / "fixtures" / "gojira"
REPO = Path(__file__).parents[2]


def test_fingerprint_matches_the_provisioners_algorithm():
    doc = {
        "app": "demo",
        "class": "web",
        "environments": {"dev": {"cluster": "c1", "namespace": "demo"}},
    }
    want = hashlib.sha256(
        json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    assert contract_fingerprint(A) == want


def test_fingerprint_ignores_format_but_not_meaning():
    reordered = (
        "# a comment\nclass: web\napp: demo\nenvironments:\n"
        "  dev:\n    namespace: demo\n    cluster: c1\n"
    )
    assert contract_fingerprint(reordered) == contract_fingerprint(A)
    assert contract_fingerprint(A.replace("c1", "c2")) != contract_fingerprint(A)


def test_job_inputs_absent_means_operator_mode():
    assert job_inputs({}) is None


def test_job_inputs_happy_path_and_defaults():
    sha = contract_fingerprint(A)
    got = job_inputs({"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": sha, "ARGUS_ENV": "dev"})
    assert (got.env, got.cluster, got.namespace, got.contract_sha256) == ("dev", None, "noto", sha)
    got = job_inputs(
        {
            "GOJIRA_CONTRACT_YAML": A,
            "GOJIRA_CONTRACT_SHA256": sha.upper(),
            "ARGUS_ENV": "dev",
            "NOTO_CLUSTER": " real-name ",
            "NOTO_NAMESPACE": "obs",
        }
    )
    assert (got.cluster, got.namespace, got.contract_sha256) == ("real-name", "obs", sha)


@pytest.mark.parametrize(
    "env,needle",
    [
        ({"GOJIRA_CONTRACT_YAML": A, "ARGUS_ENV": "dev"}, "GOJIRA_CONTRACT_SHA256"),
        (
            {"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": "0" * 64, "ARGUS_ENV": "dev"},
            "does not match",
        ),
        (
            {"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": "xyz", "ARGUS_ENV": "dev"},
            "64 hex",
        ),
        (
            {
                "GOJIRA_CONTRACT_YAML": ": : not yaml [",
                "GOJIRA_CONTRACT_SHA256": "0" * 64,
                "ARGUS_ENV": "dev",
            },
            "not valid YAML",
        ),
        (
            {
                "GOJIRA_CONTRACT_YAML": "- a list\n",
                "GOJIRA_CONTRACT_SHA256": "0" * 64,
                "ARGUS_ENV": "dev",
            },
            "mapping",
        ),
    ],
)
def test_job_inputs_rejects_unusable_inputs(env, needle):
    with pytest.raises(JobInputError, match=needle):
        job_inputs(env)


def test_job_inputs_requires_a_known_env():
    sha = contract_fingerprint(A)
    with pytest.raises(JobInputError, match="ARGUS_ENV"):
        job_inputs({"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": sha})
    with pytest.raises(JobInputError, match="ARGUS_ENV"):
        job_inputs(
            {"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": sha, "ARGUS_ENV": "staging"}
        )


def test_summary_line_is_one_bounded_line():
    line = summary_line("first\n\nsecond\tthird " + "x" * 500)
    assert line.startswith("ARGUS_SUMMARY first second third ")
    assert "\n" not in line and len(line) == SUMMARY_MAX


def test_success_and_failure_summaries():
    """The counts are STEPS, not objects: "applied" is how many steps ran and "skipped"
    how many the engine skipped. The old wording said "unchanged", which reads as a count
    of objects that did not move - and ApplyRendered.check() never skips, so it could
    only ever have meant steps anyway."""
    assert (
        success_summary("demo", "dev", 3, 2, "ab" * 32)
        == "ARGUS_SUMMARY observed demo/dev: 3 steps applied, 2 skipped, contract abababababab"
    )
    assert (
        failure_summary("apply-alerts", "kubectl: forbidden\nmore")
        == "ARGUS_SUMMARY observe failed: apply-alerts: kubectl: forbidden more"
    )
    assert failure_summary(None, "bad inputs") == "ARGUS_SUMMARY observe failed: bad inputs"


def test_a_success_summary_can_carry_a_note():
    assert success_summary("demo", "dev", 4, 1, "ab" * 32, note="no platform record").endswith(
        "contract abababababab (no platform record)"
    )


# --- the contract's strings are validated at the boundary ---


def _contract(app: str = "demo", namespace: str = "demo", cluster: str = "c1") -> str:
    return (
        f"app: {app}\nclass: web\n"
        f"environments:\n  - name: dev\n    cluster: {cluster}\n    namespace: {namespace}\n"
    )


@pytest.mark.parametrize(
    "kwargs,field",
    [
        ({"app": "de/mo"}, "app"),
        ({"app": ".."}, "app"),
        ({"app": 'de"mo'}, "app"),
        ({"app": "a" * 64}, "app"),
        ({"app": "Demo"}, "app"),
        ({"namespace": "de/mo"}, "namespace"),
        ({"namespace": ".."}, "namespace"),
        ({"namespace": 'de"mo'}, "namespace"),
        ({"namespace": "n" * 64}, "namespace"),
        ({"cluster": "c/1"}, "cluster"),
        ({"cluster": ".."}, "cluster"),
        ({"cluster": 'c"1'}, "cluster"),
    ],
)
def test_job_inputs_rejects_contract_strings_that_are_not_names(kwargs, field):
    """The contract arrives as a string in an environment variable the job does not
    control, and these fields become object names, `-n` arguments and a tenant header.
    A name with a slash, a quote, a `..` or 64 characters in it is not a name at all."""
    text = _contract(**kwargs)
    env = _job_env_text(text)
    with pytest.raises(JobInputError) as exc_info:
        job_inputs(env)
    assert field in str(exc_info.value)


def test_job_inputs_accepts_ordinary_names():
    text = _contract(app="demo-api", namespace="demo-api", cluster="dev-cluster-1.example")
    assert job_inputs(_job_env_text(text)) is not None
    longest = "a" * 63
    assert job_inputs(_job_env_text(_contract(app=longest, namespace=longest))) is not None


def _job_env_text(text: str) -> dict:
    return {
        "GOJIRA_CONTRACT_YAML": text,
        "GOJIRA_CONTRACT_SHA256": contract_fingerprint(text),
        "ARGUS_ENV": "dev",
    }


# --- CLI wiring: job mode dispatch through main(), fake runner, no real cluster ---


class FakeRunner:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.fail_on = fail_on

    def __call__(self, cmd: list[str], stdin_text: str | None) -> tuple[int, str]:
        self.calls.append((cmd, stdin_text))
        joined = " ".join(cmd)
        if self.fail_on and self.fail_on in joined:
            return 1, "boom"
        if " get namespace" in joined or "helm status" in joined:
            return 1, "not found"
        return 0, "{}" if "-o json" not in joined else '{"items": []}'


def _fake_perses_opener(req) -> tuple[int, bytes]:
    """Every GET 404s (nothing provisioned yet), every POST/PUT succeeds - enough for
    tests where ProvisionPerses only rides along with a job-mode `observe` run; its
    own behavior is covered by test_perses_step.py."""
    return (404, b"") if req.get_method() == "GET" else (200, b"{}")


def _job_env(gojira_text: str, **overrides) -> dict:
    env = {
        "GOJIRA_CONTRACT_YAML": gojira_text,
        "GOJIRA_CONTRACT_SHA256": contract_fingerprint(gojira_text),
        "ARGUS_ENV": "dev",
    }
    env.update(overrides)
    return env


def test_job_mode_success_ends_stdout_with_success_summary(tmp_path, monkeypatch, capsys):
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out
    assert out[-1].startswith("ARGUS_SUMMARY observed ")
    assert len(runner.calls) > 0


def test_job_mode_failing_step_ends_with_failure_summary_naming_the_step(
    tmp_path, monkeypatch, capsys
):
    runner = FakeRunner(fail_on="apply")
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("ARGUS_SUMMARY observe failed: ")
    assert "apply" in out[-1] or "scrape" in out[-1] or "alerts" in out[-1]


def test_job_mode_fingerprint_mismatch_applies_nothing(monkeypatch, capsys):
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    gojira_text = (FIXTURES / "web.yaml").read_text()
    env = _job_env(gojira_text, GOJIRA_CONTRACT_SHA256="0" * 64)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 1
    assert runner.calls == []
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("ARGUS_SUMMARY observe failed: ")
    assert "does not match" in out[-1]


def test_job_mode_ledger_lives_under_temp_dir_not_dot_noto(monkeypatch, capsys):
    seen_ledgers: list[Path] = []
    orig_init = Ledger.__init__

    def spying_init(self, path, *a, **kw):
        seen_ledgers.append(Path(path))
        return orig_init(self, path, *a, **kw)

    monkeypatch.setattr(Ledger, "__init__", spying_init)
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0
    assert seen_ledgers
    for p in seen_ledgers:
        assert ".noto" not in p.parts
        assert "noto-job-" in str(p)


def test_operator_mode_still_requires_gojira_and_env(monkeypatch, capsys):
    monkeypatch.delenv("GOJIRA_CONTRACT_YAML", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["observe"])
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "--gojira" in err or "--env" in err or "required" in err
    out = capsys.readouterr().out
    assert "ARGUS_SUMMARY" not in out


def test_job_mode_progress_lines_go_to_stderr_summary_is_last_stdout_line(monkeypatch, capsys):
    """Job mode must NOT pollute stdout with progress - the launcher reads exactly
    one stdout line. Progress (skipped/applied/done) still needs to show up
    somewhere for a human reading the pod log, so it goes to stderr instead."""
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0
    captured = capsys.readouterr()
    out_lines = captured.out.strip().splitlines()
    assert out_lines == [out_lines[-1]]  # nothing but the summary on stdout
    assert out_lines[-1].startswith("ARGUS_SUMMARY observed ")
    assert "applied" in captured.err
    assert "done" in captured.err


def test_job_mode_bad_flag_still_ends_stdout_with_summary(monkeypatch, capsys):
    """An argparse failure (parse_args runs before job-mode dispatch today) must not
    escape as a bare SystemExit with no summary line - the launcher always needs the
    last stdout line to be ARGUS_SUMMARY."""
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe", "--this-flag-does-not-exist"])

    assert code == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("ARGUS_SUMMARY observe failed: bad invocation")


def test_job_mode_never_silently_becomes_operator_mode(monkeypatch, capsys):
    """Job mode is decided from the environment before parsing and confirmed after it.
    If the variable disappears in between, the answer is a summary line and exit 1 - not
    a quiet slide into operator mode, which would write a `.noto/` ledger into whatever
    directory the container started in and report nothing the launcher can read."""
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cli, "job_inputs", lambda environ: None)

    code = cli.main(["observe"])

    assert code == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("ARGUS_SUMMARY observe failed: ")
    assert "GOJIRA_CONTRACT_YAML" in out[-1]


def test_job_mode_help_passes_through_untouched(monkeypatch, capsys):
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["observe", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "ARGUS_SUMMARY" not in out


def test_job_mode_applies_only_to_observe_platform_stays_untouched(monkeypatch, capsys):
    """GOJIRA_CONTRACT_YAML being set must not change `noto platform` at all - job
    mode is an `observe`-only concept."""
    monkeypatch.setenv("GOJIRA_CONTRACT_YAML", "app: demo\nclass: web\nenvironments: []\n")
    monkeypatch.setenv("GOJIRA_CONTRACT_SHA256", "0" * 64)
    monkeypatch.setenv("ARGUS_ENV", "dev")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["platform"])  # missing required --cluster: today's argparse error
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "--cluster" in err or "required" in err
    out = capsys.readouterr().out
    assert "ARGUS_SUMMARY" not in out


def test_job_mode_applied_documents_carry_the_observed_contract_annotation(monkeypatch, capsys):
    """Every object noto applies in job mode must record which contract was
    observed (issue: born-observed), so the cluster can answer 'has the app
    changed since noto looked' on its own."""
    import yaml

    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (FIXTURES / "web.yaml").read_text()
    sha = contract_fingerprint(gojira_text)
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0
    applies = [(c, s) for c, s in runner.calls if "apply" in c]
    assert applies
    for _cmd, stdin_text in applies:
        for doc in yaml.safe_load_all(stdin_text):
            assert doc["metadata"]["annotations"]["noto.dev/contract-sha256"] == sha
            assert doc["kind"] != "OpenTelemetryCollector"


def test_job_mode_stdout_still_ends_with_summary_when_drop_warning_fires(monkeypatch, capsys):
    """A drop-rule warning goes to stderr (job.py: never pollute stdout); the launcher
    still reads exactly one stdout line, the ARGUS_SUMMARY line, last."""
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (
        "app: demo\nclass: web\n"
        "environments:\n  - name: dev\n    cluster: c1\n    namespace: demo\n"
        "observability:\n  drop:\n    - name: extra-noise\n      stage: gateway\n"
        "      metric_names: ['foo_.*']\n"
    )
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0
    captured = capsys.readouterr()
    out_lines = captured.out.strip().splitlines()
    assert out_lines == [out_lines[-1]]
    assert out_lines[-1].startswith("ARGUS_SUMMARY observed ")
    assert "warning: drop rules are platform-owned now" in captured.err


def test_job_mode_namespace_from_job_inputs_reaches_the_step_context(monkeypatch, capsys):
    """`NOTO_NAMESPACE` (carried in JobInputs.namespace) must drive the same
    in-cluster namespace job mode applies against - mocking job_inputs (rather than
    relying on both sides reading the same env var) proves the value is actually
    threaded through, not just coincidentally identical."""
    from noto.engine.engine import ProvisionEngine as RealEngine
    from noto.job import JobInputs

    captured: dict = {}

    class SpyEngine(RealEngine):
        def run(self, ledger, name, ctx):
            captured["step_ctx"] = ctx
            return super().run(ledger, name, ctx)

    gojira_text = (FIXTURES / "web.yaml").read_text()
    fake_inputs = JobInputs(
        contract_yaml=gojira_text,
        contract_sha256="0" * 64,
        env="dev",
        cluster=None,
        namespace="mocked-ns",
    )
    monkeypatch.setenv("GOJIRA_CONTRACT_YAML", gojira_text)
    monkeypatch.setattr(cli, "job_inputs", lambda environ: fake_inputs)
    monkeypatch.setattr(cli, "ProvisionEngine", SpyEngine)
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])

    code = cli.main(["observe"])

    assert code == 0
    assert captured["step_ctx"].namespace == "mocked-ns"
    assert captured["step_ctx"].perses_url == "http://noto-perses.mocked-ns.svc:8080"


def test_job_mode_step_order_ends_with_provision_perses(monkeypatch, capsys):
    """Job mode shares `_run_observe` with the operator command - dashboards must
    land last there too."""
    from noto.engine.engine import ProvisionEngine as RealEngine

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
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0
    assert captured
    assert captured[0][-1] == "provision-perses"


def test_job_mode_pushes_the_apps_rules_and_counts_the_step(monkeypatch, capsys):
    """A job-provisioned app's alerts used to reach nothing that evaluates them: the
    PrometheusRule objects were applied and no engine ever read them. The push is a step
    of the run like any other, so it is counted in the line the launcher reads."""
    calls: list[tuple[str, dict]] = []

    def ruler(method: str, url: str, data: bytes | None, headers: dict):
        calls.append((method, headers.get("X-Scope-OrgID", "")))
        return (404, "") if method == "GET" else (202, "")

    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "default_ruler_http", ruler)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0
    assert any(method == "POST" for method, _tenant in calls)
    assert {tenant for _m, tenant in calls} == {"dev-cluster"}
    out = capsys.readouterr().out.strip().splitlines()
    # scrape, alerts, slo, sync-rules, dashboards
    assert out[-1].startswith("ARGUS_SUMMARY observed clips-api/dev: 5 steps applied, 0 skipped")


def test_job_mode_fails_when_the_ruler_cannot_be_reached(monkeypatch, capsys):
    """Operator mode tolerates an unreachable ruler (a laptop cannot route to the
    in-cluster URL). A job runs INSIDE the cluster, where that name resolves, and nobody
    is reading a note: it ends as the failure it is, through the same summary path as
    every other failed step."""
    from noto.feedback.ruler import RulerUnreachable

    def unreachable(*_args):
        raise RulerUnreachable("Connection refused")

    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "default_ruler_http", unreachable)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("ARGUS_SUMMARY observe failed: sync-rules: ")
    assert "Connection refused" in out[-1]
    # The summary line is the whole of a job's output that anyone reads, and a resolver
    # error on its own ("Name or service not known") does not say which name failed.
    assert "http://noto-mimir-gateway.noto.svc:80/prometheus" in out[-1]


def test_job_mode_key_error_summary_has_no_leading_quote(monkeypatch, capsys):
    """GojiraApp.environment() raises a bare KeyError, whose str() wraps the message
    in quotes; the summary must read the message plainly instead."""
    dev_only = (
        "app: demo\nclass: web\n"
        "environments:\n  - name: dev\n    cluster: c1\n    namespace: demo\n"
    )
    env = _job_env(dev_only, ARGUS_ENV="qa")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert "no environment 'qa'" in out[-1]
    assert 'observe failed: "' not in out[-1]


# --- the in-cluster kubeconfig: kubectl's fallback cannot be relied on with a bound ----


def _sa_dir(tmp_path: Path, *, token: bool = True, ca: bool = True) -> Path:
    d = tmp_path / "sa"
    d.mkdir(parents=True)
    if token:
        (d / "token").write_text("eyJ.fake")
    if ca:
        (d / "ca.crt").write_text("-----BEGIN CERTIFICATE-----\nfake\n")
    return d


def test_in_cluster_kubeconfig_names_the_server_and_the_token_by_path(tmp_path: Path):
    """The file says the cluster outright, and names the token FILE, never its value:
    the kubelet rotates the token in place and nothing about it may reach argv."""
    import yaml

    sa = _sa_dir(tmp_path)
    env = {"KUBERNETES_SERVICE_HOST": "10.140.4.1", "KUBERNETES_SERVICE_PORT": "443"}
    out = in_cluster_kubeconfig(tmp_path / "kubeconfig", env, sa_dir=sa)
    assert out == tmp_path / "kubeconfig"
    doc = yaml.safe_load(out.read_text())
    assert doc["current-context"] == "in-cluster"
    assert doc["clusters"][0]["cluster"] == {
        "server": "https://10.140.4.1:443",
        "certificate-authority": str(sa / "ca.crt"),
    }
    assert doc["users"][0]["user"] == {"tokenFile": str(sa / "token")}
    assert "eyJ.fake" not in out.read_text()


def test_in_cluster_kubeconfig_brackets_an_ipv6_host(tmp_path: Path):
    import yaml

    sa = _sa_dir(tmp_path)
    env = {"KUBERNETES_SERVICE_HOST": "fd00::1", "KUBERNETES_SERVICE_PORT": "443"}
    doc = yaml.safe_load(in_cluster_kubeconfig(tmp_path / "k", env, sa_dir=sa).read_text())
    assert doc["clusters"][0]["cluster"]["server"] == "https://[fd00::1]:443"


def test_in_cluster_kubeconfig_is_none_outside_a_pod(tmp_path: Path):
    """No env, or env but no mounted token: nothing is written and the caller leaves
    kubectl to the operator's own kubeconfig, exactly as before this existed."""
    sa = _sa_dir(tmp_path)
    assert in_cluster_kubeconfig(tmp_path / "k1", {}, sa_dir=sa) is None
    assert (
        in_cluster_kubeconfig(tmp_path / "k2", {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}, sa_dir=sa)
        is None
    )  # port missing
    no_token = _sa_dir(tmp_path / "other", token=False)
    env = {"KUBERNETES_SERVICE_HOST": "10.0.0.1", "KUBERNETES_SERVICE_PORT": "443"}
    assert in_cluster_kubeconfig(tmp_path / "k3", env, sa_dir=no_token) is None
    assert not (tmp_path / "k1").exists() and not (tmp_path / "k3").exists()


def test_step_context_puts_the_kubeconfig_on_kubectl_and_helm_alike():
    """Both tools must agree about which cluster a run targets (the #65 lesson, again):
    a kubeconfig set on the context reaches every argv, alongside the bound."""
    ctx = StepContext(runner=lambda c, s: (0, ""), kubeconfig="/tmp/kc", kube_context="dev")
    assert ctx.kubectl("get", "pods") == [
        "kubectl",
        "--kubeconfig",
        "/tmp/kc",
        "--context",
        "dev",
        "get",
        "pods",
        "--request-timeout=30s",
    ]
    assert ctx.helm("status", "x")[:5] == [
        "helm",
        "--kubeconfig",
        "/tmp/kc",
        "--kube-context",
        "dev",
    ]
    bare = StepContext(runner=lambda c, s: (0, ""))
    assert "--kubeconfig" not in bare.kubectl("get", "pods")


def test_job_mode_every_kubectl_call_carries_the_in_cluster_kubeconfig(
    monkeypatch, tmp_path: Path, capsys
):
    """The regression the first live run found: --request-timeout is on every argv (the
    bound stays), so the cluster has to be on every argv too, or kubectl dials
    localhost:8080 and the run ends in `platform record unreadable`."""
    from noto import job

    sa = _sa_dir(tmp_path)
    monkeypatch.setattr(job, "SERVICE_ACCOUNT_DIR", sa)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.140.4.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", _fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    for k, v in _job_env((FIXTURES / "web.yaml").read_text()).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0, capsys.readouterr().out
    kubectls = [c for c, _ in runner.calls if c[0] == "kubectl"]
    assert kubectls
    paths = set()
    for c in kubectls:
        assert "--kubeconfig" in c and any(a.startswith("--request-timeout=") for a in c)
        paths.add(c[c.index("--kubeconfig") + 1])
    (path,) = paths  # one file, written once, for the whole run
    assert path.endswith("/kubeconfig") and "noto-job-" in path
    assert "https://10.140.4.1:443" in Path(path).read_text()


def test_operator_mode_never_writes_or_passes_a_kubeconfig(monkeypatch, tmp_path: Path):
    """An operator on a laptop that happens to carry the in-cluster env (a devcontainer,
    a CI pod) keeps their own kubeconfig and --kube-context: job mode's file is job
    mode's alone."""
    from noto import job

    monkeypatch.delenv("GOJIRA_CONTRACT_YAML", raising=False)
    monkeypatch.setattr(job, "SERVICE_ACCOUNT_DIR", _sa_dir(tmp_path))
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.140.4.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
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
            "--kube-context",
            "laptop",
        ]
    )

    assert code == 0
    for c, _ in runner.calls:
        if c[0] == "kubectl":
            assert "--kubeconfig" not in c and c[1:3] == ["--context", "laptop"]
