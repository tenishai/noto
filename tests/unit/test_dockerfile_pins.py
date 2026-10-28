"""The Dockerfile's pins must never drift from their sources of truth: the runtime
dependencies come from uv.lock (exported with --frozen, installed with hashes
verified), and the sloth version it downloads matches the CLI and the validate
script. All of it is asserted here by parsing the real files, not by trusting a
comment - a bump to pyproject.toml or scripts/validate-rendered.sh that forgets the
Dockerfile or the lock fails this test instead of silently shipping a stale image.
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


def _lock_versions() -> dict[str, str]:
    data = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    return {pkg["name"]: pkg["version"] for pkg in data["package"] if "version" in pkg}


def _dockerfile_export_line() -> str:
    match = re.search(r"uv export (.+?) -o requirements\.txt", _dockerfile_text())
    assert match, "Dockerfile has no `uv export ... -o requirements.txt` line"
    return match.group(1)


def test_dockerfile_installs_exactly_the_lock() -> None:
    """The image reads its Python pins from uv.lock and nowhere else: the export
    refuses to re-resolve (--frozen), leaves the project itself out (it is copied
    in, not installed from an index), and pip verifies every hash."""
    flags = _dockerfile_export_line()
    assert "--frozen" in flags and "--no-emit-project" in flags
    assert re.search(r"pip install [^\n]*--require-hashes [^\n]*requirements\.txt", _dockerfile_text())
    # The one hand pin allowed is uv itself, the tool that reads the lock.
    assert not re.search(r'RUN pip install --no-cache-dir "(?!uv==)[\w-]+==', _dockerfile_text()), (
        "a hand-pinned pip install crept back in; the lock is the pin"
    )


def test_dockerfile_export_has_no_dev_dependencies() -> None:
    assert "--no-dev" in _dockerfile_export_line()


def test_lock_pins_match_pyproject() -> None:
    """Every runtime dependency pyproject pins must be in uv.lock at that exact
    version, so the image can never silently run a dependency version nothing
    else tests."""
    lock = _lock_versions()
    for name, version in _pyproject_dependencies().items():
        assert lock.get(name) == version, f"{name}: pyproject {version}, lock {lock.get(name)}"


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
    call (agent/noto/cli.py, `_generate_slo_rules`) expects to find."""
    assert _dockerfile_sloth_version() == _validate_rendered_sloth_version()


def test_all_three_sloth_pins_agree() -> None:
    """Three files name the sloth version: the Dockerfile downloads it, the validate
    script fetches it locally, and the CLI looks the binary up by that exact filename.
    The CLI's copy used to be a literal in the middle of a path expression, so a bump
    that moved the other two shipped an image whose binary the CLI could not find, and
    the failure only showed up on the first app with SLOs."""
    from noto.cli import SLOTH_VERSION

    assert SLOTH_VERSION == _dockerfile_sloth_version() == _validate_rendered_sloth_version()


def test_the_missing_binary_message_makes_sense_inside_the_image() -> None:
    """`run make validate` is the right advice on a laptop and nonsense in a container,
    where there is no repo and no make: the message has to name the path it looked at and
    say what a missing binary means in each place."""
    import pytest

    from noto import cli

    with pytest.raises(RuntimeError) as exc_info:
        cli._generate_slo_rules({"kind": "PrometheusServiceLevel"}, "demo", sloth_bin=Path("/no/s"))
    message = str(exc_info.value)
    assert "/no/s" in message
    assert "make validate" in message
    assert "build defect" in message
