---
name: noto-slo
description: Define, tune, or disable SLOs for a gojira app - edit the profile slos: blocks, Sloth generates the multiwindow burn-rate rules, the Mimir ruler evaluates them. Use when asked to "add an SLO", "set an availability/latency target", "tighten or relax an objective", "alert on error budget", or any request that names a reliability promise, even if the word SLO never appears.
license: MIT
---

# noto-slo

SLOs are profile-owned and tool-neutral: a `slos:` entry goes in, Sloth
(sloth.slok.dev/v1) emits the multiwindow-multiburn PrometheusRules, `noto rules-sync`
pushes them to the Mimir ruler. Never hand-roll burn-rate math, recording rules, or
alert expressions - Sloth owns that math by design decision, and hand-rolled rules
bypass every contract test.

## The schema (profiles/*.yaml or the app's observability: block)

```yaml
slos:
  - name: availability-99.9      # unique; disabling an inherited SLO reuses its name
    objective: 99.9              # percent
    signal: availability         # or latency
    latency_threshold: 500ms     # required for latency (ms or s only)
    enabled: true                # false disables an inherited SLO by name
```

Put the SLO at the narrowest layer that owns the promise:
`profiles/classes/<class>.yaml` binds every app of the class; the app's
`observability:` block in its gojira.yaml binds one app.

## The judgment layer

- An objective is a promise someone defends with an error budget. Do not write 99.99
  for a single-replica dev app or a class whose infra cannot deliver it; ask what is
  being promised, to whom, before raising an objective.
- SLIs assume prometheus-client conventions, live-verified: `http_requests_total{status}`
  and `http_request_duration_seconds_bucket`. An app that does not expose these gets
  rules that never fire. Check the app's /metrics before promising the SLO.
- A latency threshold should sit at a real histogram bucket boundary; a threshold
  between buckets quietly measures the next bucket up.

## Workflow (non-negotiable)

1. Edit the profile YAML or override block - never rendered output, never in-cluster.
2. `make test`; `make regen-golden` if goldens changed (the golden diff IS the review).
3. `uv run noto observe --gojira <path> --env <env>` - renders the Sloth spec,
   generates the rules, applies them through the step engine.
4. `uv run noto rules-sync --gojira <path> --env <env> --mimir-url <url>` - pushes
   every rendered group to the ruler for the app's tenant (solo: port-forward the
   gateway, `svc/noto-mimir-gateway 9009:80`).
5. Verify the groups evaluate: the ruler API lists them per tenant; the smoke DoD
   asserts the same thing if you are on a smoke cluster.

## What this skill does NOT do

Author PrometheusRule YAML directly (Sloth generates it), define SLOs on signals the
platform does not collect for the app's class, or raise a cardinality budget to make an
SLI query cheaper (that is noto-cardinality's call, with a justification).
