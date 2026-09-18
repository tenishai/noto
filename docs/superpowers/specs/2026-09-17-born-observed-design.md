# Born observed: argus as a one-shot job a provisioner can hire

Status: agreed across both repos (design thread, 2026-09-17). This document is
the argus half. The provisioner half (an `ObserveStep` that launches the job)
is built in gojira and lands independently.

## The goal

When gojira provisions an app, the app gets its observability in the same
run: collection, alerts, SLOs, and dashboards. Pressing Provision again
re-observes, so monitoring follows contract and class edits with the button
that already exists. Nobody runs a second setup by hand.

Gojira launches a one-shot Kubernetes Job from a digest-pinned argus image,
hands it the app's contract, reads one summary line back, and writes pass or
fail in its provisioning ledger. The step cannot fail a provision (an
unobserved app beats an undeployed one), but its failure is loud and readable.
Gojira learns an image name and nothing about how argus works inside.

## What argus must become

Argus today is an operator-run CLI reached through port-forwards. Five things
change.

### 1. The collector tier belongs to the platform, not to the last app observed

This is a correctness prerequisite, not a cleanup.

`render_gateway` builds the shared gateway's pipelines from the signals of
whichever app is being observed (`for signal in p.signals`), and compiles its
shaping rules from that one app's profile. Each `observe` re-renders the whole
gateway and server-side-applies it. Observing an app without `traces` removes
the traces pipeline another app depends on. With every app observed
automatically, last-writer-wins becomes a fleet-wide outage lever.

The fix:

- A **platform profile**: the baseline profile with all three signals
  (`metrics`, `logs`, `traces`) and the baseline shaping rules only.
- The `collectors` family (PriorityClass, target-allocator RBAC, agent
  DaemonSet, gateway, target allocator) is rendered from the platform profile
  and applied by **`argus platform`**, after the helm installs.
- `argus observe` no longer renders or applies the `collectors` family. It
  applies only per-app families: `scrape`, `alerts`, `slo`, and dashboards.

Consequence, stated honestly: per-class shaping rules no longer reach the
shared gateway. The gateway shapes with baseline rules for everyone. Per-app
shaping in a shared gateway (rules scoped by namespace and merged across
observed apps) is registered as a follow-up, not built here.

This also shrinks what the job may touch: nothing cluster-scoped, nothing in
the argus namespace except reading Services.

### 2. Dashboards are an engine step

Perses provisioning exists only in `tests/smoke/provision_perses.py`. It
becomes a real step, `ProvisionPerses`, in the `observe` sequence:

- Upserts the project, its datasources, and the app's rendered dashboards
  through the Perses HTTP API (`POST`, falling back to `PUT` on conflict, the
  smoke script's proven behavior).
- `ephemeral = True`: it re-decides against Perses every run.
- `check()` returns a skip reason only when every rendered document already
  matches what Perses holds.
- The smoke run calls the step through the CLI; `provision_perses.py` is
  deleted.

### 3. Job mode

`argus observe` gains a job entry path, selected by the presence of
`GOJIRA_CONTRACT_YAML` in the environment:

| Input | Meaning |
|---|---|
| `GOJIRA_CONTRACT_YAML` | the raw gojira.yaml text |
| `GOJIRA_CONTRACT_SHA256` | gojira's fingerprint of that contract |
| `ARGUS_ENV` | which environment of the contract to observe (`dev`, `qa`, `prod`) |
| `ARGUS_CLUSTER` | optional explicit tenant override; must match the platform's record, see below |
| `ARGUS_NAMESPACE` | where the platform lives; default `argus` |

Behavior:

- Argus recomputes the fingerprint with gojira's exact algorithm
  (`yaml.safe_load`, then `json.dumps(doc, sort_keys=True,
  separators=(",", ":"), default=str)`, then sha256 hex). A mismatch fails the
  run before anything is applied: the job was handed a contract that is not
  the one gojira fingerprinted.
- The contract text is written to a temp file and flows through the existing
  `--gojira` path. One reader, no second parser.
- The ledger lives in a temp dir. **The job has no ledger volume, by design.**
  An empty ledger means every step's `check()` runs against the real world. A
  persisted ledger would skip a re-observe after a contract edit, which is the
  exact case Provision-again exists for.
- Every object argus applies carries the annotation
  `argus.dev/contract-sha256: <sha>`, so the cluster records which contract
  was observed.
- The final stdout line is always `ARGUS_SUMMARY <one line>`, on success and
  on failure:
  - `ARGUS_SUMMARY observed <app>/<env>: <n> steps applied, <m> skipped, contract <sha12>`
    (plus ` (no platform record: tenant from contract)` when there was no record to read)
  - `ARGUS_SUMMARY observe failed: <step>: <reason>` (exit 1)
  The summary is one line, at most 300 characters, newlines collapsed.
  Gojira reads the termination status and scans the tail for the prefix. It
  never parses logs.
- Exit code: 0 complete, 1 failed (unchanged).

`ARGUS_CLUSTER` was invented as a bridge: one cluster is registered in gojira
under a label that differs from its real name, and the variable let a job
assert the real one. That bridge is no longer needed. The platform records the
tenant it writes with (`argus-platform-info`, see "Retention" below), and
`observe` reads that record before it renders anything: when the contract's
cluster string disagrees and no override was given, the platform's tenant wins
and one stderr line says so. The infrastructure's own name is the only
authority, and now it is written down rather than retyped.

What the variable still is: an explicit override, exactly like `--cluster`,
which it defaults. Given, it must AGREE with the recorded tenant; a
disagreement fails the run before anything is applied, with
`tenant mismatch: platform writes as '<a>' but observe was told '<b>';
dashboards would read an empty tenant` as the summary line. Do not set it to
work around a contract that disagrees; re-run `argus platform`, or fix the
contract.

### 4. In-cluster endpoints and the job's identity

- Endpoint defaults derive from the platform namespace:
  `http://argus-mimir-gateway.<ns>.svc:80`, `http://argus-loki.<ns>.svc:3100`,
  `argus-tempo.<ns>.svc:4317`, `http://argus-perses.<ns>.svc:8080`. The
  existing `SOLO_DEFAULTS` become a function of the namespace. Explicit
  flags and environment overrides still win, so an operator with a
  port-forward keeps working.
- `argus platform` renders and applies the job's identity:
  - ServiceAccount `argus-observe` in the platform namespace.
  - A ClusterRole granting `get`, `list`, `create`, `patch`, `update` on
    `servicemonitors`, `podmonitors`, and `prometheusrules`
    (`monitoring.coreos.com`), bound cluster-wide, because app namespaces are
    created dynamically. Three kinds, no secrets, no workloads.
  - Nothing else. Perses and Mimir are reached over HTTP inside the cluster
    and need no Kubernetes permissions.
- Gojira needs create and get on Jobs in the platform namespace and nothing
  more.

### 5. A published, pinnable image

- `Dockerfile`: `python:3.14-slim` with the package installed and a pinned
  `kubectl` (observe shells out to `kubectl apply`; it never calls `helm`).
  Runs as a non-root numeric user, read-only root filesystem friendly (temp
  dir under `/tmp`). The base layer gets an `apt-get upgrade` pass and CI
  builds with `--pull`, for the stale-base reason the sibling repos learned.
- A publish workflow: on push to `main`, after the existing gates, build and
  push `<ARGUS_IMAGE>:<sha7>`, authenticated by workload identity. Registry,
  provider, and service account come from repository variables; none of those
  names appear in the repo. The job summary prints the pushed digest, which
  is what the provisioner's config pins.

## Retention: enforced and alarmed

The baseline profile declares `retention: {metrics: 30d, logs: 7d, traces:
7d}` and nothing consumes it. A promise nobody enforces is a lie waiting to
be discovered.

- **Enforced**: `argus platform` passes the metrics budget to Mimir as
  `limits.compactor_blocks_retention_period` (through the same
  `mimir.structuredConfig` passthrough the cardinality budget already uses).
  Logs and traces follow the same pattern: `loki.limits_config.retention_period`
  plus `loki.compactor.retention_enabled` (the period alone is inert; Loki only
  deletes when the compactor's retention loop is on, and refuses to start
  without a `delete_request_store`, which the values file supplies), and
  `tempo.retention` - the chart's own key, which it templates into
  `compactor.compaction.block_retention`, and which must be given in hours
  because a Go duration has no day unit. Every key verified against the pinned
  chart and then against the backend's own binary.
- **Alarmed**: a platform-level rule group, applied once per cluster by
  `argus platform` (not per app):
  `ArgusRetentionWindowShort` fires when no sample exists at
  `promise - 1d` ago (`absent_over_time(up[1h] offset 29d)`), but only once
  the platform is older than the promise
  (`and on() (vector(time()) > born + promise)`; `vector(time())` rather than
  a bare `time()` because PromQL refuses a comparison between two scalars).
  The platform's age comes from the `argus-platform-info` ConfigMap's `born`
  timestamp, written once by a check-then-write step and never overwritten.
  Without the age gate the alert would fire for the first month of every new
  cluster.
- The same ConfigMap carries `tenant` and `environment`, which is how the
  tenant an app reads with stays the tenant the gateway writes with (see
  "Job mode" above). Those two are updated on every `platform` run; only
  `born` is immutable.
- A `PrometheusRule` is inert in this stack (the operator is installed without
  a Prometheus), so the platform group is evaluated only once pushed into
  Mimir's ruler. `argus platform` says so at the end of every run; a push for
  the platform's own group, next to `rules-sync`, is a registered follow-up.

## The rightsizing handover (context, not built here)

Argus becomes gojira's usage-evidence source once three things hold: usage
metrics are in Mimir (true today, via kubeletstats), history reaches the
14-day evidence window and the 30-day promise, and a side-by-side run shows
gojira's recommender makes the same call from either source. `argus feedback`
already emits gojira's `UsageProfile` and stays print-only until parity. The
nightly print-only run and the comparison tooling are a separate, smaller
piece of work after this lands.

## Testing

- Unit: fingerprint equality with gojira's algorithm (shared vectors,
  including key-order and comment changes that must not move the hash, and a
  value change that must); job-mode env parsing; summary line on success and
  on every failure path, length-bounded and single-line; namespace-derived
  endpoints with override precedence; `ProvisionPerses` against a fake HTTP
  server (create, conflict-then-update, unchanged-skip, server error).
- Contract (golden): the platform-rendered collector tier has all three
  pipelines regardless of any app; `observe` output contains no `collectors`
  family; every applied object carries the contract annotation; the
  `argus-observe` RBAC renders exactly the three kinds.
- A regression test named for the bug: observing an app without traces does
  not change the gateway.
- Idempotency: a second observe of the same contract reports zero applied.
- The existing smoke run provisions dashboards through the CLI.

## Not in this work

Per-app shaping in the shared gateway; the nightly parity run and diff
tooling; alert routing (Alertmanager still has no receivers); the step
engine's silence during long steps (#62).
