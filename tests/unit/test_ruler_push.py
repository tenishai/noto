"""Rules that are applied but never evaluated are an alarm nobody armed.

Nothing in this stack evaluates a PrometheusRule on its own (kube-prometheus-stack ships
the operator, not a Prometheus), so a group is live only once it reaches Mimir's ruler.
`argus platform` pushes its own group at the end of a successful run, and `rules-sync`
pushes each app's groups to the tenant the platform recorded as its own.
"""

from pathlib import Path

from tests.support.fake_cluster import RECORDED, InfoRunner

from argus import cli
from argus.feedback.ruler import RulerUnreachable

FIXTURES = Path(__file__).parent / "fixtures" / "gojira"


class SyncSpy:
    """Stands in for `argus.feedback.ruler.sync_rules`."""

    def __init__(self, raises: BaseException | None = None) -> None:
        self.calls: list[tuple] = []
        self.raises = raises

    def __call__(self, base: str, tenant: str, namespace: str, docs: list[dict]) -> int:
        self.calls.append((base, tenant, namespace, docs))
        if self.raises is not None:
            raise self.raises
        return len(docs)


class PruneSpy:
    """Stands in for `argus.feedback.ruler.prune_groups`; reports `deletes` as deleted."""

    def __init__(self, deletes: list[str] | None = None) -> None:
        self.calls: list[tuple] = []
        self.deletes = deletes or []

    def __call__(self, base: str, tenant: str, namespace: str, keep: set[str]) -> list[str]:
        self.calls.append((base, tenant, namespace, keep))
        return list(self.deletes)


def _spy(monkeypatch, raises: BaseException | None = None) -> SyncSpy:
    spy = SyncSpy(raises)
    monkeypatch.setattr("argus.feedback.ruler.sync_rules", spy)
    return spy


def _prune_spy(monkeypatch, deletes: list[str] | None = None) -> PruneSpy:
    spy = PruneSpy(deletes)
    monkeypatch.setattr("argus.feedback.ruler.prune_groups", spy)
    return spy


def _platform(tmp_path: Path, monkeypatch, runner: InfoRunner, *extra: str) -> int:
    monkeypatch.setattr(cli, "real_runner", runner)
    return cli.main(
        ["platform", "--cluster", "c1", "--env", "dev", "--ledger", str(tmp_path / "l.db"), *extra]
    )


# --- the platform pushes its own group ---------------------------------------


def test_the_platform_pushes_its_own_group_with_its_own_tenant(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    spy = _spy(monkeypatch)
    assert _platform(tmp_path, monkeypatch, InfoRunner(None)) == 0
    (base, tenant, namespace, docs) = spy.calls[0]
    assert tenant == "c1"
    assert namespace == "argus-platform"
    assert base == "http://argus-mimir-gateway.argus.svc:80/prometheus"
    assert [d["metadata"]["name"] for d in docs] == ["argus-platform"]
    assert docs[0]["kind"] == "PrometheusRule"
    out = capsys.readouterr().out
    assert "platform alert group pushed to the ruler" in out


def test_an_explicit_mimir_url_wins_for_the_platform_push(tmp_path: Path, monkeypatch) -> None:
    spy = _spy(monkeypatch)
    url = "http://localhost:9009/prometheus"
    assert _platform(tmp_path, monkeypatch, InfoRunner(None), "--mimir-url", url) == 0
    assert spy.calls[0][0] == url


def test_an_unreachable_ruler_is_a_note_not_a_failed_platform_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The laptop case: everything in the cluster succeeded, and the in-cluster ruler URL
    simply is not routable from here. Saying so is right; failing the run is not."""
    spy = _spy(monkeypatch, RulerUnreachable("Connection refused"))
    assert _platform(tmp_path, monkeypatch, InfoRunner(None)) == 0
    assert spy.calls  # it did try
    captured = capsys.readouterr()
    assert "platform alert group not pushed" in captured.out
    assert "http://argus-mimir-gateway.argus.svc:80/prometheus" in captured.err
    assert "port-forward" in captured.err
    assert "--mimir-url http://localhost:9009/prometheus" in captured.err


def test_a_ruler_that_rejects_the_rules_is_a_real_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Reachable and refusing is not the laptop case: something is wrong with the rules
    or with the tenant, and swallowing it would leave a silent hole. It is a failure of
    the run, reported the way every other failure is - a FAILED line and exit 1, not a
    traceback out of an otherwise successful platform run."""
    from argus.feedback.ruler import RulerSyncError

    _spy(monkeypatch, RulerSyncError("ruler rejected group argus-platform-retention: 400 bad"))
    assert _platform(tmp_path, monkeypatch, InfoRunner(None)) == 1
    captured = capsys.readouterr()
    assert "FAILED: ruler rejected group argus-platform-retention: 400 bad" in captured.err
    assert "Traceback" not in captured.err


def test_a_dry_run_pushes_nothing_and_says_what_it_would_push(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    spy = _spy(monkeypatch)
    assert _platform(tmp_path, monkeypatch, InfoRunner(None), "--dry-run") == 0
    assert spy.calls == []
    out = capsys.readouterr().out
    assert "would push argus-platform as tenant c1" in out


# --- rules-sync reads the recorded tenant ------------------------------------


def _rules_sync(tmp_path: Path, monkeypatch, runner: InfoRunner, *extra: str) -> int:
    monkeypatch.setattr(cli, "real_runner", runner)
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    return cli.main(
        [
            "rules-sync",
            "--gojira",
            str(FIXTURES / "web.yaml"),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
            *extra,
        ]
    )


def test_rules_sync_pushes_to_the_tenant_the_platform_recorded(tmp_path: Path, monkeypatch) -> None:
    """The gojira contract names the cluster one thing and the platform writes as another;
    a group filed under the contract's name would evaluate over an empty tenant."""
    spy = _spy(monkeypatch)
    _prune_spy(monkeypatch)
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED))) == 0
    assert spy.calls[0][1] == "real-name"


def test_rules_sync_prunes_the_groups_it_no_longer_renders(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Issue #57: a renamed SLO left its old group in the ruler, evaluating forever.
    `rules-sync` prunes the same way the `observe` step does, against the same namespace,
    so the command and the step cannot leave the ruler in two different states."""
    _spy(monkeypatch)
    prune = _prune_spy(monkeypatch, deletes=["gone-with-the-rename"])
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED))) == 0
    base, tenant, namespace, keep = prune.calls[0]
    assert (tenant, namespace) == ("real-name", "clips-api")
    assert base == "http://argus-mimir-gateway.argus.svc:80/prometheus"
    # the keep-set is what this run rendered, so nothing it just pushed can be deleted
    assert "argus-collector-health" in keep
    assert "pruned 1 stale group(s): gone-with-the-rename" in capsys.readouterr().out


def test_rules_sync_says_nothing_about_pruning_when_there_is_nothing_stale(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _spy(monkeypatch)
    _prune_spy(monkeypatch)
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED))) == 0
    assert "pruned" not in capsys.readouterr().out


def test_rules_sync_dry_run_opens_no_socket_and_says_what_it_would_do(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """`--dry-run` is on every subcommand, and this one now DELETES. A dry run that
    reached the ruler would prune for real from a command whose whole promise is that it
    changes nothing."""

    def exploding(method: str, url: str, data: bytes | None, headers: dict):
        raise AssertionError(f"a dry run opened a socket: {method} {url}")

    monkeypatch.setattr("argus.feedback.ruler.urllib_http", exploding)
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED)), "--dry-run") == 0
    out = capsys.readouterr().out
    assert "would push" in out
    assert "as tenant real-name, namespace clips-api, and prune any others" in out


def test_rules_sync_refuses_the_platforms_own_ruler_namespace(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An app called `argus-platform` would file its groups where the platform files its
    retention alarm, and then prune everything it did not render - the alarm included."""
    spy = _spy(monkeypatch)
    _prune_spy(monkeypatch)
    contract = tmp_path / "gojira.yaml"
    contract.write_text(
        (FIXTURES / "web.yaml").read_text().replace("app: clips-api", "app: argus-platform")
    )
    monkeypatch.setattr(cli, "real_runner", InfoRunner(dict(RECORDED)))
    monkeypatch.setattr(cli, "_generate_slo_rules", lambda doc, ns: [{"kind": "PrometheusRule"}])
    code = cli.main(
        [
            "rules-sync",
            "--gojira",
            str(contract),
            "--env",
            "dev",
            "--ledger",
            str(tmp_path / "l.db"),
        ]
    )
    assert code == 1
    assert spy.calls == []
    err = capsys.readouterr().err
    assert "FAILED: 'argus-platform' is the platform's own ruler namespace" in err


def test_rules_sync_reports_an_unreachable_ruler_the_way_every_failure_is_reported(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The laptop case reaches `rules-sync` too, and used to end in a traceback. It is
    the CLI's own failure shape, with the port-forward hint `platform` already prints."""
    _spy(monkeypatch, RulerUnreachable("Name or service not known"))
    _prune_spy(monkeypatch)
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED))) == 1
    err = capsys.readouterr().err
    assert "FAILED: http://argus-mimir-gateway.argus.svc:80/prometheus is unreachable" in err
    assert "port-forward" in err
    assert "--mimir-url http://localhost:9009/prometheus" in err
    assert "Traceback" not in err


def test_rules_sync_reports_a_ruler_that_refuses_without_a_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from argus.feedback.ruler import RulerSyncError

    _spy(monkeypatch, RulerSyncError("ruler rejected group argus-cardinality: 400 bad"))
    _prune_spy(monkeypatch)
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED))) == 1
    err = capsys.readouterr().err
    assert "FAILED: ruler rejected group argus-cardinality: 400 bad" in err
    assert "Traceback" not in err


def test_rules_sync_reports_a_prune_the_ruler_refuses(tmp_path: Path, monkeypatch, capsys) -> None:
    """The delete half raises the same error type from a different call; it must not
    escape as a traceback either."""
    from argus.feedback.ruler import RulerSyncError

    _spy(monkeypatch)

    def refusing_prune(*_args, **_kw):
        raise RulerSyncError("ruler refused to delete group stale: 500 boom")

    monkeypatch.setattr("argus.feedback.ruler.prune_groups", refusing_prune)
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED))) == 1
    err = capsys.readouterr().err
    assert "FAILED: ruler refused to delete group stale: 500 boom" in err
    assert "Traceback" not in err


def test_rules_sync_fails_loudly_on_an_explicit_tenant_that_disagrees(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    spy = _spy(monkeypatch)
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED)), "--cluster", "wrong") == 1
    assert spy.calls == []
    assert "tenant mismatch: platform writes as 'real-name'" in capsys.readouterr().err
