---
name: argus-platform
description: Stand up the Argus observability platform (Mimir/Loki/Tempo + Perses + the OTel collector tier) on a cluster. Use when asked to "make this cluster observable", "stand up the observability stack", or "install the argus platform" on a gojira-managed or kind cluster.
license: AGPL-3.0-or-later
---

# argus-platform

Stand up the per-cluster platform in the `solo` topology: pinned Helm components
(helm/pins.yaml) plus namespace and a health validation, all through the idempotent step
engine - re-running is always safe, failures resume where they stopped.

## Run it

```
uv run argus platform --cluster <cluster-name> --env <dev|qa|prod> \
    [--kube-context <ctx>] [--dry-run]
```

- `--cluster` names the ledger scope (`platform@<cluster>`); use the real cluster name.
- `--env` is required: the gateway stamps it as the `environment` resource attribute on
  every signal the cluster sends, so a wrong value mislabels the whole cluster.
- Always run `--dry-run` first on a cluster you did not create; read the report before
  the real run.
- The ledger (`.argus/argus.db`) records every step; a second run skips what is done.

## What it does, in order

1. `namespace` - ensures the `argus` namespace.
2. `helm_cert-manager` through `helm_perses` - installs every solo component from
   helm/pins.yaml at its pinned version with the committed values. Dependency order:
   cert-manager -> operators -> scrape sources -> backends -> Perses.
3. `apply_collectors` - applies the shared OTel collector tier (PriorityClass,
   target-allocator RBAC, agent DaemonSet, gateway, target allocator). It runs after the
   helm steps because these are OpenTelemetryCollector custom resources and the
   opentelemetry-operator install brings their CRDs. The tier is rendered from the
   platform profile (the baseline with metrics, logs and traces all on), never from an
   app: it is shared by every app in the cluster, so no single app may shape it.
4. `validate` - asserts every pod in the namespace is Running/Succeeded.

Preview the tier without touching the cluster:

```
uv run argus render --platform --cluster <cluster-name> --env <dev|qa|prod> --out .argus/preview
```

## When something fails

The engine halts at the failed step and records the error in the ledger. Fix the cause
(read the helm error - it is usually quota, an unreachable kube context, or a values
override the deployment needs), then re-run the same command: done steps skip, the
failed step retries. Do not delete the ledger to "reset" - it is the audit trail.

## What this skill does NOT do

Wire apps (use argus-observe-app), touch cloud resources (the OpenTofu seams run
separately for deployments that bring a GCP project), or install the regional topology
(queue + ingest tier - lands per the ADR's entry criteria).
