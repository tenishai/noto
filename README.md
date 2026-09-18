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

### The platform's own record

`argus platform` writes down what it is, in an `argus-platform-info` ConfigMap in the platform
namespace: `born` (its birth instant, written once and never overwritten), `tenant` (the cluster name
it was given, which is the tenant the gateway writes every metric, log and trace with) and
`environment` (what the gateway stamps on everything it forwards). Re-running `platform` updates
`tenant` and `environment` - they describe the platform as it is now, not as it was born.

**The write tenant and the read tenant can no longer drift apart.** The gateway WRITES with the
platform's tenant; an app's dashboards, rules and cardinality reports READ with whatever `observe`
resolved. When those differed, every panel was empty and nothing errored. `observe` now resolves its
tenant from the record, in this order:

1. an explicit `--cluster` (or `ARGUS_CLUSTER`), which **must agree** with the recorded tenant: a
   disagreement fails the run before anything is applied, naming both values;
2. otherwise the recorded tenant, which wins over a gojira.yaml cluster string that disagrees with it
   (one informational line on stderr);
3. otherwise (no record: a platform installed before this existed) the contract's own cluster string,
   with a warning to re-run `argus platform`.

Three commands consult the record: `observe` and `rules-sync` resolve their tenant from it by the
order above, and `render` previews the same resolution **silently** - it neither warns about a
missing record nor fails on a `--cluster` that disagrees with one, because it applies nothing. On
those read paths an unreadable record is simply a missing one, so `render` and `--dry-run` never
require a cluster.

The WRITE path is the opposite: `argus platform` refuses to write at all when it cannot read the
record and the reason is not "absent", because `born` can only be written once and a mistaken
rewrite would mute the retention alert for another full window.

`rules-sync` resolves its tenant the same way `observe` does, for the same reason: a rule group filed
under a tenant the gateway does not write with evaluates over nothing. `cardinality` still reads with
its own `--tenant`; pass the platform's, which `kubectl -n argus get configmap argus-platform-info -o
yaml` will tell you.

### Retention, enforced and alarmed

The profile's `budgets.retention` (`metrics: 30d`, `logs: 7d`, `traces: 7d`) is not a comment.
`argus platform` passes it to each backend as that backend's own retention setting: Mimir's
compactor block retention, Loki's retention period (plus the compactor switch that actually makes it
delete), Tempo's block retention. It also applies a platform-level `PrometheusRule` named
`argus-platform`, whose `ArgusRetentionWindowShort` fires when nothing was stored at the far edge of
the promised window - a compactor deleting early, or object storage that stopped accepting blocks.
The alert is gated on the platform being older than the promise (that is what `born` is for), so a
cluster stood up this morning does not alarm about its missing 29-day-old samples.

### Rules reach Mimir's ruler

Nothing in this stack evaluates a `PrometheusRule` on its own: kube-prometheus-stack is installed for
its operator, CRDs and Alertmanager, with Prometheus itself disabled, because Mimir is the metrics
engine. Rules become live only once they are pushed into Mimir's ruler.

- **The platform's own group** (`argus-platform`, which carries `ArgusRetentionWindowShort`) is
  pushed by `argus platform` itself at the end of a successful run, as the tenant it just recorded.
  A run from an operator's laptop usually cannot reach the in-cluster Mimir URL; that is not a
  failure of the run, so the push is skipped with one note on stderr naming the URL and the
  port-forward form. Pass `--mimir-url` to push it from there. A ruler that IS reachable and rejects
  the rules fails the run.
- **Each app's groups** are pushed by `argus rules-sync --gojira <app> --env <env>`, which now
  resolves its tenant from the platform's record exactly the way `observe` does.

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

### Running observe as a Job

A provisioner (gojira) launches `argus observe` as a one-shot, in-cluster Job rather than
running the CLI itself. `argus platform` renders and applies the identity that Job runs as -
a ServiceAccount plus the narrowest RBAC that lets `observe` do its work: server-side apply
`ServiceMonitor`/`PodMonitor`/`PrometheusRule` (`monitoring.coreos.com`) cluster-wide (app
namespaces are created dynamically, so this cannot be namespace-scoped), plus `get` on the
platform's own `argus-platform-info` ConfigMap by name only. Nothing else - no secrets, no
wildcard, no delete, no workload access. Dashboards go over HTTP to Perses and need no
Kubernetes permission at all.

**The trust scope, stated plainly:** `argus-observe` is a platform-wide identity, not a
per-tenant sandbox. Its ClusterRole reaches those three kinds in **every** app namespace,
including namespaces that do not exist yet, because gojira creates app namespaces
dynamically and a namespace-scoped grant could not cover the next app. Anything that can
launch a Job as this ServiceAccount can therefore write monitors and rules for any app in
the cluster. That is accepted scope for a platform provisioner, not an oversight; the
narrowing that matters is the verb and kind list, and who may create Jobs in the platform
namespace.

A ready-to-copy Job manifest for the provisioner to create:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: argus-observe-<app>-<env>
  namespace: argus
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    spec:
      serviceAccountName: argus-observe
      # Explicit, because observe needs it: it reads the platform's record and applies
      # its three kinds through the API server.
      automountServiceAccountToken: true
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: observe
          image: <argus image>@sha256:<digest>
          args: ["observe"]
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          env:
            - name: GOJIRA_CONTRACT_YAML
              value: <the app's gojira.yaml, as a string>
            - name: GOJIRA_CONTRACT_SHA256
              value: <sha256 of GOJIRA_CONTRACT_YAML, provisioner-computed>
            - name: ARGUS_ENV
              value: dev
            # Optional - defaults to "argus" (the platform namespace argus-observe's
            # RBAC was rendered for; must match what `argus platform` was run with).
            - name: ARGUS_NAMESPACE
              value: argus
```

`<argus image>@sha256:<digest>` is a placeholder: pin the published image by digest, never a
mutable tag, and never bake a registry name into this repo. The image is a normal argument to
the provisioner's own Job template, not something Argus renders.

The contract rides in an environment variable, and environment variables share the pod spec's
own size budget (the API server rejects an object over roughly 1 MiB), so keep `gojira.yaml`
small - well under that ceiling, since the spec carries everything else too.

The last line the job ever writes to stdout is always `ARGUS_SUMMARY <one line>`, on both
success and failure - the provisioner reads the Job's termination status for pass/fail and
scans the tail of the log for that line; it never parses the rest. Exit code 0 means observe
completed, 1 means it failed (the summary line names the failing step and reason).

`ARGUS_CLUSTER` is an explicit override and nothing more. The case it was invented for - gojira's
record of a cluster disagreeing with the cluster's real name - is now answered by the platform's own
record, which is the authority: with no override, `observe` adopts the tenant the platform writes
with and says so. Set `ARGUS_CLUSTER` only when you mean to assert a tenant, and it must match the
recorded one or the job fails with `tenant mismatch: ...` as its summary line.

The provisioner itself needs far less than `observe` does: only `create` and `get` on `Jobs`
in the platform namespace. It does not need the `argus-observe` ServiceAccount's own RBAC -
that identity belongs to the Job it launches, not to the thing launching the Job.

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
