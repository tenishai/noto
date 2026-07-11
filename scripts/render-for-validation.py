#!/usr/bin/env python3
"""Render artifacts for real-tool validation (testing.md layer "validate-rendered").

Renders the representative pair (web-solo, web-regional) and writes artifacts grouped
by the tool that judges them:

  <out>/otelcol/<case>-<role>.yaml   standalone collector configs (spec.config extracted)
  <out>/k8s/<case>-<name>.yaml       CRDs for kubeconform (ServiceMonitor/PodMonitor/
                                     PrometheusRule/OpenTelemetryCollector)
  <out>/sloth/<case>.yaml            Sloth specs (validated by `sloth generate` itself)
  <out>/perses/<case>.yaml           Perses resources for `percli lint`

Usage: render-for-validation.py <out-dir>
"""

import importlib.util
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agent"))

_spec = importlib.util.spec_from_file_location("regen_golden", REPO / "scripts" / "regen-golden.py")
assert _spec and _spec.loader
regen_golden = importlib.util.module_from_spec(_spec)
sys.modules["regen_golden"] = regen_golden
_spec.loader.exec_module(regen_golden)

CASES = [("web", "solo"), ("web", "regional")]


def main(out: Path) -> None:
    for sub in ("otelcol", "k8s", "sloth", "perses"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    for class_name, topology in CASES:
        case = f"{class_name}-{topology}"
        artifacts = regen_golden.render_everything(class_name, topology)
        for name, doc in artifacts.items():
            if name.startswith("collector-"):
                role = name.removeprefix("collector-")
                config = doc["spec"]["config"]
                (out / "otelcol" / f"{case}-{role}.yaml").write_text(yaml.safe_dump(config))
                (out / "k8s" / f"{case}-{name}.yaml").write_text(yaml.safe_dump(doc))
            elif name in ("servicemonitor", "podmonitor", "alerts"):
                (out / "k8s" / f"{case}-{name}.yaml").write_text(yaml.safe_dump(doc))
            elif name == "sloth":
                (out / "sloth" / f"{case}.yaml").write_text(yaml.safe_dump(doc))
            elif name == "perses":
                docs = yaml.safe_dump_all(doc)
                (out / "perses" / f"{case}.yaml").write_text(docs)
    print(f"rendered validation artifacts for {[f'{c}-{t}' for c, t in CASES]} into {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
