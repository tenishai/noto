<div align="center">

# Noto

**The observability arm of [gojira](https://github.com/tenishai/gojira).**
Take a deployment from zero to a full, end-to-end open-source observability platform - automatically.

[![CI](https://github.com/tenishai/noto/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/tenishai/noto/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](docs/design.md)
[![Built on OpenTelemetry](https://img.shields.io/badge/built%20on-OpenTelemetry-f5a800.svg)](https://opentelemetry.io)
[![Stack: Mimir · Loki · Tempo · Perses](https://img.shields.io/badge/stack-Mimir%20%C2%B7%20Loki%20%C2%B7%20Tempo%20%C2%B7%20Perses-1f6feb.svg)](docs/design.md)

[Design](docs/design.md) &nbsp;·&nbsp; [Roadmap](ROADMAP.md) &nbsp;·&nbsp; [Enablement Guide](docs/otel-collector-enablement-guide.md) &nbsp;·&nbsp; [Platform Evolution](docs/observability-platform-design.md)

</div>

---

## Overview

Noto makes what gojira deploys **observable**. Where gojira gets your app running on Kubernetes
(identity, CI/CD, manifests, sizing), Noto continues the same lifecycle and stands up a complete,
open-source observability platform around it: the metrics, logs, and traces backends, the
OpenTelemetry collection tier, and per-app dashboards, SLOs, and alerts derived from the workload
class gojira already assigned. No hand-rolled collector YAML, no parallel project to operate, no
vendor backend.

Noto is opinionated by design. Its goals:

- **Automatic** - once the platform is up and gojira's observe step is on, a gojira deployment is
  observed with no per-app configuration (the observe Job, [docs/operating.md](docs/operating.md)).
- **Opinionated** - a workload's class selects how it is observed; the collector pipelines in the
  [enablement guide](docs/otel-collector-enablement-guide.md) are the default, not a blank file.
- **Open source, end to end** - OpenTelemetry plus Mimir/Loki/Tempo and Perses, no vendor lock-in, no
  per-host licensing.
- **Agent-driven** - operated by a self-contained agent and a set of Agent Skills, not a web UI.
- **gojira-native** - reads gojira's contract and state as the source of truth; it does not duplicate it.

## Quickstart

Render what noto would install for a sample app, with no cluster. The only prerequisite is
[uv](https://docs.astral.sh/uv/); it fetches Python 3.14 for you.

```
git clone https://github.com/tenishai/noto && cd noto
uv sync --frozen
uv run noto render --gojira tests/smoke/gojira.yaml --env dev
ls .noto/rendered-app
```

`render` reads a gojira deploy contract (the sample describes `podinfo`, a `web` class app) and
writes the four families noto derives from the workload class: `scrape.yaml` (the collection
config), `perses.yaml` (the dashboard), `alerts.yaml` (the alert rules) and `slo.yaml` (the SLO
spec). It touches nothing else; `observe` is the command that applies them to a cluster.

To see the whole thing run, `make smoke` creates a [kind](https://kind.sigs.k8s.io/) cluster,
installs the platform (collector, Mimir, Loki, Tempo, Perses), deploys the sample app, observes it
and checks the result. It needs Docker, kind and kubectl. `make test` runs the conventions lint and
the unit and contract tests (`Makefile`); CI runs those plus the architecture-card checker, the
rendered-artifact validators, the OpenTofu checks and the collector distribution build
(`.github/workflows/`).

## Part of a larger platform

Noto is one piece of a shared, opinionated SRE/agent platform - sibling projects that each own one
concern and are built to compose. Noto owns observability:

- **[gojira](https://github.com/tenishai/gojira)** - the deploy platform (identity, CI/CD, manifests,
  sizing). Noto's parent: it reads gojira's deploy contract and gojira's provisioner runs
  `noto observe` as a Job after each deploy (the `ARGUS_SUMMARY` contract in
  [docs/operating.md](docs/operating.md)).

Planned seams, not built: [techne](https://github.com/tenishai/techne) as the home of the ledger,
[liminal](https://github.com/tenishai/liminal) for reaching tools and exposing noto's own operations
as a handler, [cabal](https://github.com/tenishai/cabal) for an observability persona,
[daedalus](https://github.com/tenishai/daedalus) for that agent's bundle, and
[sero](https://github.com/tenishai/sero) for secrets through the External Secrets Operator. Noto
runs self-contained today; gojira is the one integration that ships.

## How it works

```
gojira:  repo ──▶ identity ──▶ CI/CD ──▶ manifests ──▶ app running, /metrics exposed
                                                              │
                                                              ▼  (Noto continues)
noto:   per-cluster platform (collectors + backends + Perses) ──▶ wire each gojira app by class ──▶ dashboards · SLOs · alerts
```

Noto stands the platform up once per cluster, then wires each gojira-managed app into it based on its
workload class, applying everything directly through cloud CLI / SDK / Helm and OpenTelemetry Operator
CRDs - no third-party GitOps controller in the path.

## Operating it

`noto platform --cluster <name> --env <dev|qa|prod>` stands the platform up once per cluster:
the collector tier, the backends, Perses, and the identity the observe Job runs as.
`noto observe --gojira <path> --env <env>` wires one app in. Everything an operator needs
beyond that is in [docs/operating.md](docs/operating.md):

- the collector tier, and why per-app shaping rules do not reach the shared gateway;
- the platform's own record (`noto-platform-info`), and how `observe` resolves its tenant from
  it so the write tenant and the read tenant cannot drift apart;
- retention, enforced per backend and alarmed by `NotoRetentionWindowShort`;
- how rules reach Mimir's ruler: on `platform`, on every `observe`, and via `rules-sync`;
- endpoints: in-cluster DNS by default, port-forward on request;
- running `observe` as a one-shot Job: its identity, a ready-to-copy manifest, the
  `ARGUS_SUMMARY` contract, and what `observe` never deletes;
- the image: what it carries, the floating base, the layout law and the two pinned binaries.

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

**Phase 1 (Observe) is complete**, released as v0.1.0 (tag `v0.1.0`, 2026-07-11) and deployed. **Phase 2
(Curate) is the current focus**, and parts of it have landed: the integrations catalog and the
cardinality skill. See the [roadmap](ROADMAP.md) for the phase plan, the
[changelog](CHANGELOG.md) for what has shipped since the tag, and the
[design document](docs/design.md) for architecture, opinionated defaults, and the deployment order of
operations.

## Documentation

- [Operating noto](docs/operating.md) - the platform record, retention, rules, endpoints, the observe Job, the image
- [Roadmap](ROADMAP.md) - the phased plan (Observe -> Curate -> Guard -> Reason) at a glance
- [Design document](docs/design.md) - architecture, decisions, deployment model, roadmap
- [OpenTelemetry Collector Enablement Guide](docs/otel-collector-enablement-guide.md) - the collector
  patterns and pipeline opinions Noto ships as defaults
- [Platform Evolution](docs/observability-platform-design.md) - the agent topology and
  integrations-that-just-work north star
- [Architecture cards](ARCHITECTURE.md) - the repo card and its seams under docs/arch/
- [Changelog](CHANGELOG.md), [Contributing](CONTRIBUTING.md), [Testing](docs/testing.md)

## How this was built

tenishai is a small team of engineers who build with AI coding agents. Most of
this code was written in pairing sessions with Claude models in Claude Code: a
person set the design, directed the work and reviewed what landed, and the
agent wrote much of the code and docs. Many commits name the agent in a
`Co-Authored-By:` trailer, either a Claude model or the name of the
agent one of us pairs with. A commit without a trailer may still
have had AI help.

## License

[MIT](LICENSE), matching the tenishai family (gojira, techne, liminal, cabal, daedalus).

Copyright (C) 2026 tenish and the noto contributors