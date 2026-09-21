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


def test_rules_sync_fails_loudly_on_an_explicit_tenant_that_disagrees(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    spy = _spy(monkeypatch)
    assert _rules_sync(tmp_path, monkeypatch, InfoRunner(dict(RECORDED)), "--cluster", "wrong") == 1
    assert spy.calls == []
    assert "tenant mismatch: platform writes as 'real-name'" in capsys.readouterr().err
