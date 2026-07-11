"""Golden files: full rendered output per class x topology, byte-exact.

Regeneration is explicit (`make regen-golden`) and reviewed as a PR diff - the review
of that diff is how an opinion change becomes deliberate rather than accidental.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parents[2]
GOLDEN = REPO / "tests" / "contract" / "golden"

# The regen script is the single source of truth for what gets rendered and with
# which fixture Context; import it so test and regen can never disagree.
_spec = importlib.util.spec_from_file_location("regen_golden", REPO / "scripts" / "regen-golden.py")
assert _spec and _spec.loader
regen_golden = importlib.util.module_from_spec(_spec)
sys.modules["regen_golden"] = regen_golden
_spec.loader.exec_module(regen_golden)

pytestmark = pytest.mark.contract

CASES = [
    (class_name, topology)
    for class_name in regen_golden.CLASSES
    for topology in regen_golden.TOPOLOGIES
]


@pytest.mark.parametrize(("class_name", "topology"), CASES)
def test_rendered_output_matches_golden(class_name: str, topology: str) -> None:
    outdir = GOLDEN / f"{class_name}-{topology}"
    assert outdir.is_dir(), (
        f"missing golden dir {outdir}; run `make regen-golden` and review the diff"
    )

    artifacts = regen_golden.render_everything(class_name, topology)
    rendered_names = {f"{name}.yaml" for name in artifacts}
    on_disk = {p.name for p in outdir.glob("*.yaml")}
    assert rendered_names == on_disk, "artifact set drifted; run `make regen-golden`"

    for name, doc in artifacts.items():
        expected = (outdir / f"{name}.yaml").read_text()
        actual = yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
        assert actual == expected, (
            f"{class_name}-{topology}/{name}.yaml drifted from golden; "
            "if intentional, run `make regen-golden` and review the diff in your PR"
        )
