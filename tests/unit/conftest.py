"""No unit test ever talks to a real cluster.

`render` now consults the platform's own record through `cli.real_runner`, which means a
test that only renders would otherwise shell out to whatever kubectl and kubeconfig the
machine happens to have - slow on a laptop pointed at a live cluster, and a result that
depends on that cluster's state. This replaces the runner with one that answers "not
found" to everything; tests that care about the runner install their own on top of it,
which works because monkeypatch applies this fixture first.
"""

import pytest

from argus import cli


@pytest.fixture(autouse=True)
def no_real_cluster(monkeypatch):
    monkeypatch.setattr(
        cli, "real_runner", lambda cmd, stdin_text: (1, "Error from server (NotFound)")
    )
