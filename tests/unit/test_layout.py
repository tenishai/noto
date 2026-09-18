"""Guards the layout the argus image depends on.

`REPO_ROOT` (agent/argus/cli.py) is `Path(__file__).resolve().parents[2]` - derived
from the source file's own location, not an installed package's location - and
`profiles/`, `integrations/` and `helm/pins.yaml` are read from it at runtime
(`_resolved`, `_render_platform_families`, `_step_context`). The Dockerfile relies on
this: it preserves the repo layout under `/app` instead of `pip install`-ing the
package into site-packages, and `COPY`s exactly these paths. If this test ever needs
to change because the layout moved, the Dockerfile's COPY lines must move with it.
"""

from argus.cli import REPO_ROOT


def test_repo_root_is_the_actual_repo_root() -> None:
    assert (REPO_ROOT / "agent" / "argus" / "cli.py").is_file()


def test_profiles_baseline_exists_under_repo_root() -> None:
    assert (REPO_ROOT / "profiles" / "baseline.yaml").is_file()


def test_integrations_dir_exists_under_repo_root() -> None:
    assert (REPO_ROOT / "integrations").is_dir()


def test_helm_pins_exists_under_repo_root() -> None:
    assert (REPO_ROOT / "helm" / "pins.yaml").is_file()
