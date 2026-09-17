---
name: argus-cardinality
description: Diagnose and curb metric cardinality against the profile budget - read the per-tenant series report, identify offenders, propose drop/strip rules as profile diffs. Use when asked "what's blowing up our series count", "why is Mimir rejecting samples", "we have too many metrics", when an ArgusCardinalityBudget80/Exceeded alert fires, or any cost/limit conversation about metrics volume.
license: AGPL-3.0-or-later
---

# argus-cardinality

The budget is profile-owned (`budgets.cardinality` per env), enforced soft: it becomes
Mimir's `max_global_series_per_user` at platform time, and budget-burn alerts ship in
every alert pack (80% is a ticket, exceeded is a page). This skill turns a cardinality
report into the smallest profile diff that gets back under budget.

## Read the report first

```
uv run argus cardinality --tenant <cluster> --mimir-url <url> [--limit 15]
```

(solo: port-forward the gateway, `svc/argus-mimir-gateway 9009:80`). Top metric names
by active series count, per tenant. No report, no diff: never propose drops from
memory of what "usually" is noisy.

## Decide per offender (the judgment layer)

- Whole metric nobody queries -> `metric_names` drop at `gateway`.
- Metric is useful but one label is unbounded (`pod_uid`, `request_id`, a raw path) ->
  `metric_labels_strip` keeps the metric, kills the explosion.
- A namespace nobody owns emitting freely -> `namespace_exclude`.
- The scrape source itself is misconfigured (a debug scrape, a duplicate job) -> fix
  the source; a drop rule hiding a config bug is debt.
- Raising the budget is the last resort, requires a `justification:` (schema-enforced),
  and changes what the on-call gets paged for - treat it as a policy change, not a fix.

Check who queries an offender before dropping it: dashboards (`profiles/*/dashboards`),
SLO SLIs, and alert packs all read metrics by name; `grep` the profiles and
`content/` for the metric name. Dropping a metric an SLO reads breaks the SLO silently.

## Propose as profile diffs

Rules use the argus-pipeline schema (`drop:` entries; one matcher each) at the
narrowest layer: baseline is everyone, class is one workload shape, the app's
`observability:` block is one app. Drop at the earliest stage the rule's inputs allow -
name/label-only rules run at `gateway`; anything needing app context runs at `agent`.

## Workflow (non-negotiable)

1. Report first (above). Note the offenders and the budget headroom.
2. Edit the profile YAML at the narrowest layer.
3. `make test`; `make regen-golden` if goldens changed; `make validate` (the real
   collector binary judges the rendered OTTL).
4. Re-apply: `uv run argus platform --cluster <name> --env <env>` - every gateway and
   agent shaping rule lives in the baseline and reaches the cluster through the shared
   collector tier, which only `platform` applies.
5. Re-measure: run the report again; the series count moving down is the acceptance.
   An unchanged count means the rule matched nothing - wrong name, wrong stage, or the
   series predate the rule (Mimir counts active series; wait one head-compaction).

## What this skill does NOT do

Edit Mimir limits in-cluster (`max_global_series_per_user` is set from the profile at
platform time - change the profile, re-run platform), delete data already stored, or
raise a budget without a justification and an owner who accepts the new paging line.
