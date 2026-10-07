# Proposed upstream: PROMETHEUS_HEADERS + the Argus Mimir recipe

**Target repo:** `gojira-sizing-agent`, a private tenishai repository; this proposal and its patch are kept here as the record
(read-only for Argus, so this ships as a proposal, not a push)
**Patch:** [gojira-sizing-agent-prometheus-headers.patch](gojira-sizing-agent-prometheus-headers.patch)
(git-am-able onto `main` @ `e060ff2`; verified: applies clean, 16/16 tests pass, ruff clean)
**Context:** [argus#52](https://github.com/tenishai/argus/issues/52) - Option A adopted:
gojira-sizing-agent is the single evidence producer everywhere; Argus retired its
posting path the same day (this repo, `argus feedback` is now diagnostic-only).

## Why

The live interop test (2026-07-12, evidence in argus#52) ran the agent unmodified on an
Argus solo cluster and it worked first try, with one bridge: a 20-line nginx proxy whose
only job was injecting `X-Scope-OrgID` in front of Argus's multi-tenant Mimir. The metrics
handler speaks `PROMETHEUS_AUTH=none|google` but has no way to add a header. This patch
replaces that proxy with configuration.

## What the patch does

- `PROMETHEUS_HEADERS` env (comma-separated `Name=Value` pairs, values may contain `=`)
  flows through `ctx.credentials` as `prometheus_headers` and is applied to every
  Prometheus request by the metrics handler. Auth modes still win the `Authorization`
  header on conflict. Declared in the handler's capability card; optional secret key in
  the CronJob manifest.
- README gains an "Argus-managed clusters (Mimir)" recipe next to the GMP one, with the
  live-validated endpoint, tenant header, and kubeletstats `CPU_QUERY`/`MEM_QUERY`
  preset (`k8s_pod_cpu_time` / `k8s_pod_memory_working_set`, pod-level, promoted
  `k8s_*` labels).
- Two tests: header injection (incl. `=` in values) and auth-mode precedence.

## Shape to taste

Everything here is a default, not a position:

- **Env name and format.** `PROMETHEUS_HEADERS` with `Name=Value,` pairs mirrors the
  existing flat-string credentials; JSON or per-header envs work equally well if
  preferred.
- **Preset placement.** The recipe lives in the README (matching how the GMP recipe
  landed). If gojira's cluster registry grows per-cluster query config (the open
  question from the coordination thread: registry vs deploy-time), the recipe moves
  there and the README section shrinks to a pointer.

## To apply

```bash
git am docs/proposals/gojira-sizing-agent-prometheus-headers.patch  # from their checkout
make test lint
```

Or cherry-pick the ideas and discard the patch; the tests are the part worth keeping.
