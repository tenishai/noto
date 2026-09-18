"""The Dockerfile hand-pins two things that must never drift from their sources of
truth: the runtime dependency versions it `pip install`s, and the sloth version it
downloads. Both are asserted here by parsing the real files, not by trusting a
comment - a bump to pyproject.toml or scripts/validate-rendered.sh that forgets the
Dockerfile fails this test instead of silently shipping a stale image.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _dockerfile_text() -> str:
    return (REPO_ROOT / "Dockerfile").read_text()


def _pyproject_dependencies() -> dict[str, str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    pins = {}
    for dep in data["project"]["dependencies"]:
        name, _, version = dep.partition("==")
        assert version, f"dependency {dep!r} is not pinned with =="
        pins[name] = version
    return pins


def _dockerfile_pip_install_pins() -> dict[str, str]:
    text = _dockerfile_text()
    match = re.search(r"RUN pip install --no-cache-dir (.+)", text)
    assert match, "Dockerfile has no `RUN pip install --no-cache-dir ...` line"
    pins = {}
    for name, version in re.findall(r'"([\w-]+)==([^"]+)"', match.group(1)):
        pins[name] = version
    assert pins, f"could not parse any pins from: {match.group(1)!r}"
    return pins


def test_dockerfile_pip_install_has_no_dev_dependencies() -> None:
    dev_names = {
        dep.partition("==")[0]
        for dep in tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["dependency-groups"][
            "dev"
        ]
    }
    installed = set(_dockerfile_pip_install_pins())
    leaked = installed & dev_names
    assert not leaked, f"dev dependencies leaked into the image: {leaked}"


def test_dockerfile_pip_install_pins_match_pyproject() -> None:
    """Every version the Dockerfile installs must equal pyproject.toml's pin exactly,
    so the image can never silently run a dependency version nothing else tests."""
    assert _dockerfile_pip_install_pins() == _pyproject_dependencies()


def _dockerfile_sloth_version() -> str:
    match = re.search(r"ARG SLOTH_VERSION=(\S+)", _dockerfile_text())
    assert match, "Dockerfile has no `ARG SLOTH_VERSION=...` line"
    return match.group(1)


def _validate_rendered_sloth_version() -> str:
    text = (REPO_ROOT / "scripts" / "validate-rendered.sh").read_text()
    match = re.search(r"^SLOTH_VERSION=(\S+)", text, flags=re.MULTILINE)
    assert match, "scripts/validate-rendered.sh has no SLOTH_VERSION=... line"
    return match.group(1)


def test_dockerfile_sloth_version_matches_validate_rendered() -> None:
    """One pin, enforced in two files: `make validate` fetches sloth locally at this
    version, and the image must ship the exact same binary the CLI's `sloth generate`
    call (agent/argus/cli.py, `_generate_slo_rules`) expects to find."""
    assert _dockerfile_sloth_version() == _validate_rendered_sloth_version()
