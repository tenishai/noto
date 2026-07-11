---
name: argus-pipeline
description: Author or modify Argus collector pipeline behavior - drop rules, filters, label stripping, raw OTTL - within the contract-tested bounds. Use when asked to "drop noisy logs", "add a redaction/filter", "stop collecting metric X", or "why is this data being dropped".
license: AGPL-3.0-or-later
---

# argus-pipeline

Pipeline behavior is profile-owned, never hand-edited: change the `drop:` rules in
`profiles/baseline.yaml` (everyone) or `profiles/classes/<class>.yaml` (one class), or
the app's `observability:` block (one app). The renderers compile rules to OTTL; the
contract tests and `make validate` judge the result.

## The rule schema

Each rule: `name`, `stage` (agent | gateway | ingest - drop as early as the rule's
inputs allow), `enabled`, and exactly one matcher:

- `log_level_below: info` - drop logs under a severity
- `path_matches: [/health]` - drop spans+logs for endpoints
- `metric_names: [kube_pod_status_reason]` - drop metric series by name regex
- `metric_labels_strip: [pod_uid]` - strip unbounded labels (renders as a transform)
- `namespace_exclude: [kube-system]` - drop a namespace across all signals
- `raw_ottl: {logs: [...]}` - the escape hatch; REQUIRES `justification:`

## Workflow (non-negotiable)

1. Edit the profile YAML - never collector config, never rendered output.
2. `make test` - the OTTL compiler and contract invariants check the rule lands at its
   declared stage and nowhere else.
3. `make regen-golden` if goldens changed - the PR diff of golden files IS the review.
4. `make validate` - the real collector binary judges the rendered config.
5. Re-apply to affected apps/clusters via argus-observe-app / argus-platform.

Refuse requests to hand-edit an OpenTelemetryCollector CR in-cluster: the next reconcile
overwrites it, and the change would bypass every contract test. The profile is the only
mutation surface.
