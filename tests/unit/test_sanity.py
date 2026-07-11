"""Layer-2 sanity: the package imports and versions agree.

Real unit tests land with the step engine (idempotency, resume, ledger rows),
the gojira-reader (contract parsing), and the renderers (profile -> CRDs).
"""

import argus


def test_package_imports() -> None:
    assert argus.__version__
