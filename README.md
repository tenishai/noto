<div align="center">

# Argus

**The observability arm of [gojira](https://github.com/tenishai/gojira).**
Take a deployment from zero to a full, end-to-end open-source observability platform - automatically.

[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-design-orange.svg)](docs/design.md)
[![Built on OpenTelemetry](https://img.shields.io/badge/built%20on-OpenTelemetry-f5a800.svg)](https://opentelemetry.io)
[![Stack: Mimir · Loki · Tempo · Perses](https://img.shields.io/badge/stack-Mimir%20%C2%B7%20Loki%20%C2%B7%20Tempo%20%C2%B7%20Perses-1f6feb.svg)](docs/design.md)

[Design](docs/design.md) &nbsp;·&nbsp; [Roadmap](ROADMAP.md) &nbsp;·&nbsp; [Enablement Guide](docs/otel-collector-enablement-guide.md) &nbsp;·&nbsp; [Platform Evolution](docs/observability-platform-design.md)

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

## Part of a larger platform

Argus is one piece of a shared, opinionated SRE/agent platform - sibling projects that each own one
concern and are built to compose. Argus owns observability:

- **[gojira](https://github.com/tenishai/gojira)** - the deploy platform (identity, CI/CD, manifests,
  sizing). Argus's parent: it continues gojira's lifecycle and maps workload class to observability.
- **[techne](https://github.com/tenishai/techne)** - agentic memory. Argus's ledger migrates here, so
  its deploy history becomes durable memory.
- **[liminal](https://github.com/tenishai/liminal)** - the MCP gateway. Argus reaches its tools
  through it, and can expose its own operations as a handler.
- **[cabal](https://github.com/tenishai/cabal)** - agent orchestration. Argus's agent registers here
  as the observability persona.
- **[daedalus](https://github.com/tenishai/daedalus)** - agent construction kit. The Argus agent's
  shell is a daedalus bundle (persona, agent card, wiring).
- **[ladon](https://github.com/tenishai/ladon)** - secrets on OpenBao. Argus consumes secrets as a
  ladon team via the External Secrets Operator.

Argus runs self-contained today and composes with these as the platform comes together; it never
duplicates what a sibling owns.

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

### The collector tier

The collector tier (agent DaemonSet, gateway, target allocator) is part of the platform, not part of
wiring an app: `argus platform --cluster <name> --env <dev|qa|prod>` stands it up once, with all three
signals on, and `argus observe --gojira <path> --env <env>` only adds that app's scrape wiring,
dashboards, SLOs and alerts. One consequence, stated plainly: per-class and per-app shaping rules no
longer reach the shared gateway - the baseline rules apply to everyone (a `drop:` rule added, changed,
or disabled by an app's class or `observability:` override is a no-op; `observe`/`render` warn about it
on stderr, naming the ignored rules), and per-app shaping in the shared gateway is a registered
follow-up.

**The write tenant and the read tenant must be the same value.** The gateway writes with the tenant
`argus platform --cluster` was given. `observe`, `render`, `rules-sync` and `cardinality` each read or
write with their OWN `--cluster`/`--tenant` (falling back to the app's gojira.yaml cluster string when
no override is given) - a per-app command, not the platform. If that value ever differs from the
cluster `platform` was run with, dashboards, rules and cardinality reports query a tenant nothing
writes to: empty panels, no error. Until a later task makes `platform` record its tenant and these
commands read it back, keeping them in sync is on the operator.

### Endpoints: in-cluster by default, port-forward on request

Argus was always run by an operator through port-forwards, so `--mimir-url` used to default to
`localhost`. Running `observe` as a one-shot in-cluster Job changes that: the right default is the
platform's own Service DNS names in the platform's own namespace, not `localhost` (there is no
port-forward inside the cluster). `--mimir-url` (on `rules-sync`, `cardinality`, and `feedback`) and
`--perses-url` (on `observe`) now derive from `--namespace` (env `ARGUS_NAMESPACE`, default `argus`)
unless given explicitly - an explicit flag always wins.

An operator's port-forward habit keeps working exactly as before; just say so explicitly:

```
kubectl -n argus port-forward svc/argus-mimir-gateway 9009:80
argus rules-sync --gojira app/gojira.yaml --env dev --mimir-url http://localhost:9009/prometheus
```

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

Argus is in **design / pre-Phase-1**, with foundations in place and **Phase 1 (Observe)** as the
current focus. See the [roadmap](ROADMAP.md) for the phase plan and the
[design document](docs/design.md) for architecture, opinionated defaults, and the deployment order of
operations. Code scaffolding is in progress.

## Documentation

- [Roadmap](ROADMAP.md) - the phased plan (Observe -> Curate -> Guard -> Reason) at a glance
- [Target fleet](docs/target-fleet.md) - the first real deployment: scale parameters, the Datadog
  parallel-run migration, correlation acceptance checks, and remediation guardrails
- [Design document](docs/design.md) - architecture, decisions, deployment model, roadmap
- [OpenTelemetry Collector Enablement Guide](docs/otel-collector-enablement-guide.md) - the collector
  patterns and pipeline opinions Argus ships as defaults
- [Platform Evolution](docs/observability-platform-design.md) - the agent topology and
  integrations-that-just-work north star

## License

[AGPL-3.0-or-later](LICENSE), matching the tenishai family (gojira, techne, liminal, cabal, daedalus).
