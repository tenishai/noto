"""No unit test ever talks to a real cluster.

`render` now consults the platform's own record through `cli.real_runner`, which means a
test that only renders would otherwise shell out to whatever kubectl and kubeconfig the
machine happens to have - slow on a laptop pointed at a live cluster, and a result that
depends on that cluster's state. This replaces the runner with one that answers "not
found" to everything; tests that care about the runner install their own on top of it,
which works because monkeypatch applies this fixture first.

The same rule holds for the two seams that speak HTTP. `observe` now pushes the app's
rule groups into Mimir's ruler, so without a default here every test that runs `observe`
would resolve the in-cluster Mimir name on whatever network the machine is on.
"""

import pytest

from argus import cli


def _accepting_ruler(method: str, url: str, data: bytes | None, headers: dict):
    """A ruler that accepts every push and holds no groups, so a test that is not about
    the ruler is not about the ruler. Tests that ARE install their own on top."""
    if method == "GET":
        return 404, "namespace not found"
    return 202, ""


@pytest.fixture(autouse=True)
def no_real_cluster(monkeypatch):
    monkeypatch.setattr(
        cli, "real_runner", lambda cmd, stdin_text: (1, "Error from server (NotFound)")
    )
    monkeypatch.setattr(cli, "default_ruler_http", _accepting_ruler)
