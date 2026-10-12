<div align="center">

# Noto

**The observability arm of [gojira](https://github.com/tenishai/gojira).**
Take a deployment from zero to a full, end-to-end open-source observability platform - automatically.

[![CI](https://github.com/tenishai/noto/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/tenishai/noto/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](docs/design.md)
[![Built on OpenTelemetry](https://img.shields.io/badge/built%20on-OpenTelemetry-f5a800.svg)](https://opentelemetry.io)
[![Stack: Mimir · Loki · Tempo · Perses](https://img.shields.io/badge/stack-Mimir%20%C2%B7%20Loki%20%C2%B7%20Tempo%20%C2%B7%20Perses-1f6feb.svg)](docs/design.md)

[Design](docs/design.md) &nbsp;·&nbsp; [Roadmap](ROADMAP.md) &nbsp;·&nbsp; [Enablement Guide](docs/otel-collector-enablement-guide.md) &nbsp;·&nbsp; [Platform Evolution](docs/observability-platform-design.md)

</div>

---

## Overview

Noto makes what gojira deploys **observable**. Where gojira gets your app running on Kubernetes
(identity, CI/CD, manifests, sizing), Noto continues the same lifecycle and stands up a complete,
100% open-source observability platform around it: the metrics, logs, and traces backends, the
OpenTelemetry collection tier, and per-app dashboards, SLOs, and alerts derived from the workload
class gojira already assigned. No hand-rolled collector YAML, no parallel project to operate, no
vendor backend.

Noto is opinionated by design. Its goals:

- **Automatic** - a gojira deployment goes from running to fully observed with zero extra config.
- **Opinionated** - a workload's class selects how it is observed; battle-tested collector pipelines
  are the default, not a blank file.
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
and checks the result. It needs Docker, kind and kubectl, and takes about 15 minutes. `make test`
runs the conventions lint and the unit and contract tests, the same gates CI runs.

## Part of a larger platform

Noto is one piece of a shared, opinionated SRE/agent platform - sibling projects that each own one
concern and are built to compose. Noto owns observability:

- **[gojira](https://github.com/tenishai/gojira)** - the deploy platform (identity, CI/CD, manifests,
  sizing). Noto's parent: it continues gojira's lifecycle and maps workload class to observability.
- **[techne](https://github.com/tenishai/techne)** - agentic memory. Noto's ledger migrates here, so
  its deploy history becomes durable memory.
- **[liminal](https://github.com/tenishai/liminal)** - the MCP gateway. Noto reaches its tools
  through it, and can expose its own operations as a handler.
- **[cabal](https://github.com/tenishai/cabal)** - agent orchestration. Noto's agent registers here
  as the observability persona.
- **[daedalus](https://github.com/tenishai/daedalus)** - agent construction kit. The Noto agent's
  shell is a daedalus bundle (persona, agent card, wiring).
- **[ladon](https://github.com/tenishai/ladon)** - secrets on OpenBao. Noto consumes secrets as a
  ladon team via the External Secrets Operator.

Noto runs self-contained today and composes with these as the platform comes together; it never
duplicates what a sibling owns.

## How it works

```
gojira:  repo ──▶ identity ──▶ CI/CD ──▶ manifests ──▶ app running, /metrics exposed
                                                              │
                                                              ▼  (Noto continues)
noto:   per-cluster platform (collectors + backends + Perses) ──▶ auto-wire every app by class ──▶ dashboards · SLOs · alerts
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

**Phase 1 (Observe) is complete**, released as v0.1.0 on 2026-07-11 and deployed. **Phase 2
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
`Co-Authored-By:` trailer, either a Claude model or Poe (earlier Raven), the
name of the agent one of us pairs with. A commit without a trailer may still
have had AI help.

## License

[AGPL-3.0-or-later](LICENSE), matching the tenishai family (gojira, techne, liminal, cabal, daedalus).

Copyright (C) 2026 tenish and the noto contributors