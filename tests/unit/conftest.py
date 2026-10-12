"""No unit test ever talks to a real cluster, and none opens a socket.

`render` now consults the platform's own record through `cli.real_runner`, which means a
test that only renders would otherwise shell out to whatever kubectl and kubeconfig the
machine happens to have - slow on a laptop pointed at a live cluster, and a result that
depends on that cluster's state. This replaces the runner with one that answers "not
found" to everything; tests that care about the runner install their own on top of it,
which works because monkeypatch applies this fixture first.

The same rule holds for every seam that speaks to the ruler, and there are three: the
`SyncRules` step's injected `ruler_http`, and the two commands that call the ruler client
directly with no seam at all (`noto platform`'s own group, `noto rules-sync`). All three
are replaced here, the last two by patching `urllib_http` under both names it is reachable
by, so a test that gets a URL or a tenant wrong fails on the assertions below rather than
resolving a cluster-internal name on whatever network the machine is on.
"""

import pytest

from noto import cli
from noto import steps as steps_module
from noto.feedback import ruler as ruler_module


def _accepting_ruler(method: str, url: str, data: bytes | None, headers: dict):
    """A ruler that accepts every push and holds no groups, so a test that is not about
    the ruler is not about the ruler. Tests that ARE install their own on top.

    It answers only what a ruler is asked: every call must address the rules API and carry
    a tenant, because a call that got either wrong reaches the wrong data in a real
    cluster and a fake that ignored them would let that pass."""
    assert "/config/v1/rules/" in url, f"not a ruler rules URL: {method} {url}"
    assert headers.get("X-Scope-OrgID"), f"no tenant header on {method} {url}"
    if method == "GET":
        return 404, "namespace not found"
    return 202, ""


@pytest.fixture(autouse=True)
def no_real_cluster(monkeypatch):
    monkeypatch.setattr(
        cli, "real_runner", lambda cmd, stdin_text: (1, "Error from server (NotFound)")
    )
    monkeypatch.setattr(cli, "default_ruler_http", _accepting_ruler)
    monkeypatch.setattr(ruler_module, "urllib_http", _accepting_ruler)
    monkeypatch.setattr(steps_module, "urllib_http", _accepting_ruler)
