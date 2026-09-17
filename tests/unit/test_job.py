"""Contract fingerprint, job-mode env inputs, and the ARGUS_SUMMARY line."""

import hashlib
import json
from pathlib import Path

import pytest

from argus import cli
from argus.engine.ledger import Ledger
from argus.job import (
    SUMMARY_MAX,
    JobInputError,
    contract_fingerprint,
    failure_summary,
    job_inputs,
    success_summary,
    summary_line,
)

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
    assert (got.env, got.cluster, got.namespace, got.contract_sha256) == ("dev", None, "argus", sha)
    got = job_inputs(
        {
            "GOJIRA_CONTRACT_YAML": A,
            "GOJIRA_CONTRACT_SHA256": sha.upper(),
            "ARGUS_ENV": "dev",
            "ARGUS_CLUSTER": " real-name ",
            "ARGUS_NAMESPACE": "obs",
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
    assert (
        success_summary("demo", "dev", 3, 2, "ab" * 32)
        == "ARGUS_SUMMARY observed demo/dev: 3 applied, 2 unchanged, contract abababababab"
    )
    assert (
        failure_summary("apply-alerts", "kubectl: forbidden\nmore")
        == "ARGUS_SUMMARY observe failed: apply-alerts: kubectl: forbidden more"
    )
    assert failure_summary(None, "bad inputs") == "ARGUS_SUMMARY observe failed: bad inputs"


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
    assert (
        "apply" in out[-1] or "collectors" in out[-1] or "scrape" in out[-1] or "alerts" in out[-1]
    )


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


def test_job_mode_ledger_lives_under_temp_dir_not_dot_argus(monkeypatch, capsys):
    seen_ledgers: list[Path] = []
    orig_init = Ledger.__init__

    def spying_init(self, path, *a, **kw):
        seen_ledgers.append(Path(path))
        return orig_init(self, path, *a, **kw)

    monkeypatch.setattr(Ledger, "__init__", spying_init)
    runner = FakeRunner()
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    gojira_text = (FIXTURES / "web.yaml").read_text()
    for k, v in _job_env(gojira_text).items():
        monkeypatch.setenv(k, v)

    code = cli.main(["observe"])

    assert code == 0
    assert seen_ledgers
    for p in seen_ledgers:
        assert ".argus" not in p.parts
        assert "argus-job-" in str(p)


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
    """GOJIRA_CONTRACT_YAML being set must not change `argus platform` at all - job
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
