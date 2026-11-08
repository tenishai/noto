---
name: noto-dashboard
description: Create or extend Perses dashboards-as-code from the profile dashboards: blocks - panels declared as metric:*/logs:*/traces:*, rendered with tenant-header datasources and validated by percli. Use when asked for "a dashboard", "add a panel", "graph this metric", "where do I see latency/errors/logs", or any request to visualize a service's telemetry.
license: AGPL-3.0-or-later
---

# noto-dashboard

Dashboards are profile-owned and rendered to Perses resources (project, datasources
with the tenant header, dashboards). Never author dashboards in the Perses UI or edit
rendered JSON: the next `noto observe` re-renders from the profile and the drift is
gone, unreviewed.

## The schema (profiles/baseline.yaml and profiles/classes/<class>.yaml)

A dashboard declares either a flat panel list (one untitled grid, two columns of 12x8)
or titled rows (one collapsible grid per row, panels packed by their own sizes). One or
the other, never both.

```yaml
dashboards:
  - name: web-red
    panels: ["metric:request-rate", "metric:error-rate", "metric:latency-p99",
             "traces:slow-requests", "logs:errors"]
    enabled: true
  - name: agent-health
    duration: 24h        # the range it opens on (default 1h)
    refresh: 1m          # auto-refresh; omit for none
    rows:
      - title: Is it alive
        panels: ["metric:ready-replicas", "metric:restarts-24h", "metric:up-for",
                 "metric:memory-of-limit", "metric:image", "metric:readiness-history"]
      - title: Logs
        open: true       # the row's state when the page loads
        panels: ["logs:errors", "logs:recent"]
      - title: About
        panels: ["markdown:about"]
```

Panel grammar is `<signal>:<slug>` with signal one of `metric` / `logs` / `traces` /
`markdown`. `logs:*` renders a Loki query scoped to the service; `traces:*` a Tempo query;
`markdown:about` a text panel (class, namespace, cluster, environment, the app's links,
and what the page cannot show). An app dashboard with rows also gets a `pod` variable:
its resource panels narrow by `$pod`, all pods by default.

The chart kind follows the slug, not the signal: `ready-replicas`, `restarts-24h`,
`up-for` and `oom-kills` are StatCharts, `memory-of-limit` a GaugeChart, `image` a Table,
`readiness-history` a StatusHistoryChart; every other `metric:` slug is a TimeSeriesChart.
Kinds, plugin options (units, thresholds, dashed request/limit lines) and panel sizes live
in `_PANEL_SPECS` / `_PANEL_SIZES` in `agent/noto/rendering/perses.py`, and each panel's
`description` says what it shows and what it cannot. Keep that honest when adding one.

## Links (the one per-app input)

The `observability:` block in gojira.yaml may carry `links: {name: url}` (where the app
is managed, its repository, its chat surface). They render as the dashboard's own links
and inside `markdown:about`. noto never hardcodes one; the provisioner that writes
gojira.yaml knows them.

## Real queries vs placeholders (read before adding a metric panel)

Only known metric slugs render real PromQL. App-emitted, prometheus-client conventions
(verified against a live gojira-shaped app): `request-rate`, `error-rate`, `latency-p99`.
From kubelet and kube-state-metrics, which every pod has (so these work for the `agent`
class, whose workload emits no metrics): `ready-replicas`, `restarts-24h`, `up-for`,
`memory-of-limit`, `image`, `readiness-history`, `cpu` (usage beside request and limit),
`memory` (working set beside limit), `memory-working-set`, `pod-restarts`, `network`,
`pod-filesystem`, `oom-kills`. Any other `metric:` slug renders a placeholder query (`up`),
deliberately loud in the golden diff so reviewers catch it. Adding a genuinely new metric
panel therefore means adding its PromQL to `_METRIC_QUERIES` in
`agent/noto/rendering/perses.py` in the same PR, or the panel ships showing `up`.

Two traps the queries already step around: `kube_pod_status_ready` is dropped at the
gateway (baseline `ksm-churn`), so readiness is built from the per-container series; and
kubeletstats collects pod, container and node groups only, so a mounted volume's fill is
not available (`pod-filesystem` is the pod's own writable layer and says so).

`logs:errors` matches lines that carry a level (`level=error`, `"level":"error"`, an
upper-case `ERROR`/`FATAL` token), a Python traceback or a Go panic; not the word "error"
in prose. An agent that talks about errors still lands in `logs:recent`.

## The judgment layer

- Co-locate correlated panels: a metric next to the logs and traces that explain it,
  on one dashboard. Exemplar click-through in the Perses UI is still unverified
  (issue #47); co-location is the spike-proven mitigation.
- Class dashboards serve every app of the class. Do not add one app's pet panel to a
  class dashboard; there is no per-app dashboards override surface (only baseline and
  class, plus the app's `links`), so an app-specific need is a class-design conversation,
  not a hack.
- A row that would be empty does not exist. The agent class has no "what it is doing"
  row (turns, tool calls, tokens) because the runtime emits no such signal yet; when it
  does, the row is added with real queries, not before with placeholders.
- Fewer panels beat more: every panel is a query per refresh across every app of the
  class.

## Workflow (non-negotiable)

1. Edit `dashboards:` in the profile YAML.
2. `make test`; `make regen-golden` if goldens changed (the golden diff IS the review);
   `make validate` - percli, the real Perses validator, judges the rendered dashboards.
3. Re-apply: `uv run noto observe --gojira <path> --env <env>` (class dashboards) or
   `uv run noto platform --cluster <name> --env <env>` (cluster-baseline). Apps observed
   before the change keep their old dashboard until observe runs for them again; see
   "Re-provisioning dashboards" in docs/operating.md.
4. Verify through the Perses proxy: the dashboard renders and each panel's query
   returns series (a panel stuck on `up` means step "Real queries" above was skipped).

## What this skill does NOT do

Hand-author Perses JSON, add datasources (rendered per tenant automatically), or wire
new telemetry - a panel can only show what the app's class already collects (that is
noto-observe-app / noto-pipeline territory).
