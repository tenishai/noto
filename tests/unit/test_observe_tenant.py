"""Which tenant an app's dashboards, rules and cardinality reports READ with.

The gateway WRITES telemetry with the tenant `argus platform --cluster` was given, and the
platform records it (test_platform_info.py). When the tenant `observe` resolved differed
from that one, every panel was empty and nothing errored. These tests pin the resolution
order that closes the gap, in both operator and job mode.
"""

from pathlib import Path

from tests.support.fake_cluster import CONTRACT_CLUSTER, RECORDED, InfoRunner, fake_perses_opener

from argus import cli

FIXTURES = Path(__file__).parent / "fixtures" / "gojira"


def _observe(
    tmp_path: Path, monkeypatch, runner: InfoRunner, *extra: str, captured: dict | None = None
) -> int:
    monkeypatch.delenv("GOJIRA_CONTRACT_YAML", raising=False)
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    if captured is not None:
        render_families = cli._render_families

        def spy(p, ctx):
            captured["ctx"] = ctx
            return render_families(p, ctx)

        monkeypatch.setattr(cli, "_render_families", spy)
    return cli.main(
        [
            "observe",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            *extra,
        ]
    )


def test_the_recorded_tenant_wins_over_the_contract_with_one_info_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The case the temporary ARGUS_CLUSTER bridge was invented for: gojira's contract
    calls the cluster one thing, the platform writes as another. The platform wins."""
    runner = InfoRunner(dict(RECORDED))
    captured: dict = {}
    assert _observe(tmp_path, monkeypatch, runner, captured=captured) == 0
    err = capsys.readouterr().err
    assert "real-name" in err
    assert CONTRACT_CLUSTER in err
    # what this app's dashboards will READ with is now what the gateway WRITES with
    assert captured["ctx"].tenant == "real-name"


def test_an_explicit_cluster_that_agrees_with_the_record_is_accepted(
    tmp_path: Path, monkeypatch
) -> None:
    runner = InfoRunner(dict(RECORDED))
    captured: dict = {}
    assert _observe(tmp_path, monkeypatch, runner, "--cluster", "real-name", captured=captured) == 0
    assert captured["ctx"].tenant == "real-name"
    assert runner.applies


def test_an_explicit_cluster_that_disagrees_fails_before_anything_is_applied(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    runner = InfoRunner(dict(RECORDED))
    assert _observe(tmp_path, monkeypatch, runner, "--cluster", "wrong-name") == 1
    assert runner.applies == []
    err = capsys.readouterr().err
    assert (
        "tenant mismatch: platform writes as 'real-name' but observe was told 'wrong-name'; "
        "dashboards would read an empty tenant" in err
    )


def test_argus_cluster_is_an_explicit_override_and_must_match(tmp_path: Path, monkeypatch) -> None:
    """The environment variable is the same explicit override the flag is, so it fails the
    same way. It is no longer a bridge around a contract that disagrees."""
    monkeypatch.setenv("ARGUS_CLUSTER", "wrong-name")
    runner = InfoRunner(dict(RECORDED))
    assert _observe(tmp_path, monkeypatch, runner) == 1
    assert runner.applies == []


def test_a_missing_record_warns_and_falls_back_to_the_contract(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    runner = InfoRunner(None)
    captured: dict = {}
    assert _observe(tmp_path, monkeypatch, runner, captured=captured) == 0
    err = capsys.readouterr().err
    assert "argus platform" in err
    assert CONTRACT_CLUSTER in err
    assert captured["ctx"].tenant == CONTRACT_CLUSTER


def test_a_missing_record_still_honors_an_explicit_cluster(tmp_path: Path, monkeypatch) -> None:
    runner = InfoRunner(None)
    captured: dict = {}
    assert _observe(tmp_path, monkeypatch, runner, "--cluster", "chosen", captured=captured) == 0
    assert captured["ctx"].tenant == "chosen"


def _render(tmp_path: Path, monkeypatch, runner: InfoRunner, *extra: str) -> tuple[int, dict]:
    monkeypatch.setattr(cli, "real_runner", runner)
    captured: dict = {}
    render_families = cli._render_families

    def spy(p, ctx):
        captured["ctx"] = ctx
        return render_families(p, ctx)

    monkeypatch.setattr(cli, "_render_families", spy)
    code = cli.main(
        [
            "render",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--out",
            str(tmp_path / "out"),
            "--ledger",
            str(tmp_path / "l.db"),
            *extra,
        ]
    )
    return code, captured


def test_render_previews_the_recorded_tenant(tmp_path: Path, monkeypatch, capsys) -> None:
    """A preview whose tenant is not the one `observe` would resolve is a preview of a
    run that will not happen."""
    code, captured = _render(tmp_path, monkeypatch, InfoRunner(dict(RECORDED)))
    assert code == 0
    assert captured["ctx"].tenant == "real-name"


def test_render_never_warns_and_never_fails_over_the_record(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Silent on every branch `observe` is loud on: a disagreeing override, a missing
    record. `render` applies nothing, so it has nothing to refuse."""
    code, _ = _render(tmp_path, monkeypatch, InfoRunner(dict(RECORDED)), "--cluster", "wrong-name")
    assert code == 0
    assert capsys.readouterr().err == ""

    code, captured = _render(tmp_path, monkeypatch, InfoRunner(None))
    assert code == 0
    assert capsys.readouterr().err == ""
    assert captured["ctx"].tenant == CONTRACT_CLUSTER


def _job_observe(tmp_path: Path, monkeypatch, runner: InfoRunner, *extra: str, **env: str) -> int:
    from argus.job import contract_fingerprint

    contract = (FIXTURES / "web.yaml").read_text()
    monkeypatch.setenv("GOJIRA_CONTRACT_YAML", contract)
    monkeypatch.setenv("GOJIRA_CONTRACT_SHA256", contract_fingerprint(contract))
    monkeypatch.setenv("ARGUS_ENV", "dev")
    monkeypatch.delenv("ARGUS_CLUSTER", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    return cli.main(["observe", "--ledger", str(tmp_path / "l.db"), *extra])


def test_job_mode_says_so_in_the_summary_when_there_is_no_record(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The fallback is invisible in a green Job otherwise: the run succeeds, the
    dashboards read whatever the contract said, and the only hint is a stderr line
    nobody reads once the exit code is 0. The summary the provisioner DOES read now
    carries it."""
    assert _job_observe(tmp_path, monkeypatch, InfoRunner(None)) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("ARGUS_SUMMARY observed ")
    assert last.endswith("(no platform record: tenant from contract)")


def test_job_mode_says_nothing_extra_when_the_record_answered(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    assert _job_observe(tmp_path, monkeypatch, InfoRunner(dict(RECORDED))) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("ARGUS_SUMMARY observed ")
    assert "no platform record" not in last


def test_job_mode_fails_when_the_record_is_present_but_unreadable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A Forbidden is not an absent record: it means the job's own identity was not
    rolled out, so `argus platform` has not been re-run in the right order. Falling back
    quietly there would wire an app to a tenant nothing writes to and report success."""
    runner = InfoRunner(readable=False)
    assert _job_observe(tmp_path, monkeypatch, runner) == 1
    assert runner.applies == []
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("ARGUS_SUMMARY observe failed: platform record unreadable: ")
    assert "Forbidden" in last


def test_job_mode_dry_run_never_requires_a_cluster_even_with_an_unreadable_record(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """`--dry-run` must never require a cluster, in every mode (README, "The platform's
    own record"). Job mode's strict read exists for a REAL run, where a job's own
    identity not being rolled out must fail loudly; a dry run touches nothing and must
    fall back exactly like operator mode does, carrying the fallback note in its
    summary."""
    runner = InfoRunner(readable=False)
    assert _job_observe(tmp_path, monkeypatch, runner, "--dry-run") == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("ARGUS_SUMMARY observed ")
    assert last.endswith("(no platform record: tenant from contract)")


def test_job_mode_without_dry_run_still_fails_on_an_unreadable_record(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The existing strict behavior, pinned so the dry-run carve-out above cannot widen
    into a real job run: a Forbidden record still fails the job when it is not a
    `--dry-run`."""
    runner = InfoRunner(readable=False)
    assert _job_observe(tmp_path, monkeypatch, runner) == 1
    assert runner.applies == []
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("ARGUS_SUMMARY observe failed: platform record unreadable: ")
    assert "Forbidden" in last


def test_operator_mode_still_warns_and_falls_back_on_an_unreadable_record(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The operator is standing at a terminal reading stderr, and can be told rather than
    refused - the strictness belongs to the job, which has nobody watching."""
    runner = InfoRunner(readable=False)
    captured: dict = {}
    assert _observe(tmp_path, monkeypatch, runner, captured=captured) == 0
    assert captured["ctx"].tenant == CONTRACT_CLUSTER
    assert "argus platform" in capsys.readouterr().err


def test_job_mode_reports_a_tenant_mismatch_as_the_summary_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from argus.job import contract_fingerprint

    contract = (FIXTURES / "web.yaml").read_text()
    monkeypatch.setenv("GOJIRA_CONTRACT_YAML", contract)
    monkeypatch.setenv("GOJIRA_CONTRACT_SHA256", contract_fingerprint(contract))
    monkeypatch.setenv("ARGUS_ENV", "dev")
    monkeypatch.setenv("ARGUS_CLUSTER", "wrong-name")
    runner = InfoRunner(dict(RECORDED))
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "default_perses_opener", fake_perses_opener)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    code = cli.main(["observe", "--ledger", str(tmp_path / "l.db")])
    assert code == 1
    last_line = capsys.readouterr().out.strip().splitlines()[-1]
    assert last_line.startswith("ARGUS_SUMMARY observe failed: tenant mismatch: ")
    assert "real-name" in last_line and "wrong-name" in last_line
    assert runner.applies == []
