---
name: argus-observe-app
description: Wire a gojira app into the Argus platform by its workload class - scrape wiring, dashboards, SLOs, alerts, all rendered from its profile. Use when asked to "observe this app", "add dashboards/SLOs for this service", or after gojira deploys a new app.
license: AGPL-3.0-or-later
---

# argus-observe-app

Read the app's `gojira.yaml`, resolve its observability profile (baseline + class +
optional `observability:` override), render every artifact, and apply them through the
step engine. Zero hand-written YAML - the class decides what "observed" means.

## Run it

```
uv run argus observe --gojira <path/to/gojira.yaml> --env <dev|qa|prod> \
    [--kube-context <ctx>] [--perses-url <url>] [--dry-run]
```

**Do not pass `--cluster` unless you mean to assert a tenant.** The platform records the tenant
its gateway writes with (the `argus-platform-info` ConfigMap), and `observe` reads that record:
with no flag, the recorded tenant wins over a disagreeing gojira.yaml cluster string and one
stderr line says so. `--cluster` is an explicit override that must EQUAL the recorded tenant, or
the run fails before applying anything. A mismatch would mean dashboards querying a tenant
nothing writes to: empty panels, no error. See README, "The platform's own record".

`observe` also provisions the app's Perses dashboards over HTTP, not through kubectl - Perses
documents are API objects, not cluster objects. The URL defaults to the platform's in-cluster
Service; from a laptop, port-forward and say so:

```
kubectl -n argus port-forward svc/argus-perses 8080:8080
uv run argus observe --gojira <path> --env dev --perses-url http://localhost:8080
```

Preview without touching the cluster:

```
uv run argus render --gojira <path/to/gojira.yaml> --env dev --out .argus/preview
```

Read the preview when the user asks "what would this app get?" - the YAML is the answer
(ServiceMonitor/PodMonitor, Sloth SLOs, Perses dashboards, alert packs).

The shared collector tier is NOT here: the agent DaemonSet, gateway and target allocator
are one set of objects per cluster, applied by `argus platform` (use argus-platform). An
app is wired into that tier, it never re-renders it. A consequence to be honest about:
per-class and per-app shaping rules no longer reach the shared gateway - the baseline
rules apply to everyone, and per-app shaping in the shared gateway is a registered
follow-up.

## The contract

- The workload class in gojira.yaml selects the profile (web -> HTTP RED + SLOs,
  worker -> throughput, cron -> job health, heavy/gpu -> USE, agent -> runtime health,
  gateway -> edge RED).
- The optional `observability:` block overrides per app (enable traces, retention,
  extra SLOs, disable an inherited rule by name with `enabled: false`). A `drop:` rule
  here renders nowhere: shaping rules only ever reached the shared collector tier, and
  that tier is platform-owned now (see the note above).
- Overrides that raise budgets require a `justification:` - refuse to add one without it.

## GMP supersession (GKE clusters)

gojira-generated apps carry Google Managed Prometheus CRDs. Per design.md, removing them
means changing the manifest source: open a small PR against the app repo deleting
`k8s/base/podmonitoring.yaml` and `k8s/base/rules.yaml`, referencing the Argus
equivalents this skill applied. Never just delete the CRDs in-cluster - gojira's drift
capture will PR them back.
