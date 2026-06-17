<div align="center">

# Argus

**The observability arm of [gojira](https://github.com/shaz/gojira).**
Take a deployment from zero to a full, end-to-end open-source observability platform - automatically.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-design-orange.svg)](docs/design.md)
[![Built on OpenTelemetry](https://img.shields.io/badge/built%20on-OpenTelemetry-f5a800.svg)](https://opentelemetry.io)
[![Stack: Mimir · Loki · Tempo · Perses](https://img.shields.io/badge/stack-Mimir%20%C2%B7%20Loki%20%C2%B7%20Tempo%20%C2%B7%20Perses-1f6feb.svg)](docs/design.md)

[Design](docs/design.md) &nbsp;·&nbsp; [Enablement Guide](docs/otel-collector-enablement-guide.md) &nbsp;·&nbsp; [Platform Evolution](docs/observability-platform-design.md)

</div>

---

## Overview

Argus makes what gojira deploys **observable**. Where gojira gets your app running on Kubernetes
(identity, CI/CD, manifests, sizing), Argus continues the same lifecycle and stands up a complete,
100% open-source observability platform around it: the metrics, logs, and traces backends, the
OpenTelemetry collection tier, and per-app dashboards, SLOs, and alerts derived from the workload
class gojira already assigned. No hand-rolled collector YAML, no parallel project to operate, no
vendor backend.

Argus is opinionated by design. Its goals:

- **Automatic** - a gojira deployment goes from running to fully observed with zero extra config.
- **Opinionated** - a workload's class selects how it is observed; battle-tested collector pipelines
  are the default, not a blank file.
- **Open source, end to end** - OpenTelemetry plus Mimir/Loki/Tempo and Perses, no vendor lock-in, no
  per-host licensing.
- **Agent-driven** - operated by a self-contained agent and a set of Agent Skills, not a web UI.
- **gojira-native** - reads gojira's contract and state as the source of truth; it does not duplicate it.

## How it works

```
gojira:  repo ──▶ identity ──▶ CI/CD ──▶ manifests ──▶ app running, /metrics exposed
                                                              │
                                                              ▼  (Argus continues)
argus:   per-cluster platform (collectors + backends + Perses) ──▶ auto-wire every app by class ──▶ dashboards · SLOs · alerts
```

Argus stands the platform up once per cluster, then wires each gojira-managed app into it based on its
workload class, applying everything directly through cloud CLI / SDK / Helm and OpenTelemetry Operator
CRDs - no third-party GitOps controller in the path.

## The stack

| Layer | Tool |
|---|---|
| Collection | OpenTelemetry Collector (custom distribution) - Agent (DaemonSet) + Gateway (Deployment) + TargetAllocator (StatefulSet) |
| Metrics | Mimir |
| Logs | Loki |
| Traces | Tempo |
| Visualization | Perses (dashboards-as-code; metrics, logs, traces) |
| Alerting | Alertmanager |
| Cluster sources | kube-state-metrics, node-exporter, kubelet / cAdvisor |

## Status

Argus is in **design / pre-Phase-1**. The architecture, opinionated defaults, deployment order of
operations, and phased roadmap are defined in the [design document](docs/design.md). Code scaffolding
is in progress.

## Documentation

- [Design document](docs/design.md) - architecture, decisions, deployment model, roadmap
- [OpenTelemetry Collector Enablement Guide](docs/otel-collector-enablement-guide.md) - the collector
  patterns and pipeline opinions Argus ships as defaults
- [Platform Evolution](docs/observability-platform-design.md) - the agent topology and
  integrations-that-just-work north star

## License

[Apache 2.0](LICENSE)
