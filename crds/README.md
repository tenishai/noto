# crds/

Runtime CRDs (OpenTelemetryCollector agent/gateway/ingest/TargetAllocator,
ServiceMonitor/PodMonitor, PrometheusRule, Sloth PrometheusServiceLevel) are **rendered
by code**, not templated from files: `agent/noto/rendering/` builds them as typed dicts
from resolved profiles + Context (see the #17 spec). Reviewable examples live in the
contract golden files (`tests/contract/golden/`); shape validation runs in
`make validate` (kubeconform + the collector's own `validate`).

This directory intentionally holds no hand-edited YAML - the agent regenerates CRDs from
the contract and never edits collector config by hand (design.md).
