"""Layer-3 contract tests: golden-file assertions over rendered output.

These activate with the renderers (issue #17): memory_limiter first / batch last,
k8sattributes passthrough-on-agent / enrich-on-gateway, profile drop rules present
in every pipeline, routing table matches the ADR, solo vs regional placement.
Until then this placeholder keeps the layer wired into `make test`.
"""

import pytest


@pytest.mark.contract
def test_contract_layer_is_wired() -> None:
    assert True
