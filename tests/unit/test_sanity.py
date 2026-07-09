"""Layer-2 sanity: the package imports and versions agree.

Real unit tests land with the step engine (idempotency, resume, ledger rows),
the gojira-reader (contract parsing), and the renderers (profile -> CRDs).
"""

import tomllib
from pathlib import Path

import argus

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_package_imports() -> None:
    assert argus.__version__


def test_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert argus.__version__ == pyproject["project"]["version"]
