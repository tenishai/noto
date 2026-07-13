---
name: argus-dashboard
description: Create or extend Perses dashboards-as-code from the profile dashboards: blocks - panels declared as metric:*/logs:*/traces:*, rendered with tenant-header datasources and validated by percli. Use when asked for "a dashboard", "add a panel", "graph this metric", "where do I see latency/errors/logs", or any request to visualize a service's telemetry.
license: AGPL-3.0-or-later
---

# argus-dashboard

Dashboards are profile-owned and rendered to Perses resources (project, datasources
with the tenant header, dashboards). Never author dashboards in the Perses UI or edit
rendered JSON: the next `argus observe` re-renders from the profile and the drift is
gone, unreviewed.

## The schema (profiles/baseline.yaml and profiles/classes/<class>.yaml)

```yaml
dashboards:
  - name: web-red
    panels: ["metric:request-rate", "metric:error-rate", "metric:latency-p99",
             "traces:slow-requests", "logs:errors"]
    enabled: true
```

Panel grammar is `<signal>:<slug>` with signal one of `metric` / `logs` / `traces`.
`logs:*` renders a Loki query scoped to the service; `traces:*` a Tempo query.

## Real queries vs placeholders (read before adding a metric panel)

Only known metric slugs render real PromQL (prometheus-client conventions, verified
against a live gojira-shaped app): `request-rate`, `error-rate`, `latency-p99`. Any
other `metric:` slug renders a placeholder query (`up`) - deliberately loud in the
golden diff so reviewers catch it. Adding a genuinely new metric panel therefore means
adding its PromQL to `_METRIC_QUERIES` in `agent/argus/rendering/perses.py` in the same
PR, or the panel ships showing `up`.

## The judgment layer

- Co-locate correlated panels: a metric next to the logs and traces that explain it,
  on one dashboard. Exemplar click-through in the Perses UI is still unverified
  (issue #47); co-location is the spike-proven mitigation.
- Class dashboards serve every app of the class. Do not add one app's pet panel to a
  class dashboard; there is no per-app dashboards override surface yet (only baseline
  and class), so an app-specific need is a class-design conversation, not a hack.
- Fewer panels beat more: every panel is a query per refresh across every app of the
  class.

## Workflow (non-negotiable)

1. Edit `dashboards:` in the profile YAML.
2. `make test`; `make regen-golden` if goldens changed (the golden diff IS the review);
   `make validate` - percli, the real Perses validator, judges the rendered dashboards.
3. Re-apply: `uv run argus observe --gojira <path> --env <env>` (class dashboards) or
   `uv run argus platform --cluster <name>` (cluster-baseline).
4. Verify through the Perses proxy: the dashboard renders and each panel's query
   returns series (a panel stuck on `up` means step "Real queries" above was skipped).

## What this skill does NOT do

Hand-author Perses JSON, add datasources (rendered per tenant automatically), or wire
new telemetry - a panel can only show what the app's class already collects (that is
argus-observe-app / argus-pipeline territory).
