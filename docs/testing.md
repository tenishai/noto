# Testing Philosophy

Argus stands up observability platforms. Our tests have to earn the same trust we ask users to place
in the platform: if Argus says a cluster is observed, it is. So we test the things that, for an
observability tool, actually predict success at each deployment stage - and we keep those tests
**small, fast, and meaningful**. We do not chase coverage numbers.

## Principles

- **Test the contract, not the implementation.** Assert on the behavior we promise (idempotency,
  correct pipeline order, a queryable metric), not on internal call shapes that churn.
- **Fast by default.** The static and unit layers run in seconds on every PR and locally before every
  commit. Slow, infra-touching tests are quarantined to their own layer and gated.
- **Each test maps to a real failure mode** an observability platform hits while deploying. If we
  cannot name the failure a test prevents, we do not write it.
- **Deterministic.** No network or wall-clock dependence in unit tests. Pin inputs; fake the clock.
- **Tests ship with the change.** Every `feat`/`fix` PR adds or updates the layer it touches.

## The layers

The layers mirror the deployment stages, cheapest and fastest first. A PR runs the layers its change
touches; `main` runs everything that does not require live cloud.

### 1. Static / lint (every PR, seconds)

The cheapest guards. No code execution.

- **Conventions:** no em dashes in prose, no floating `latest` pins (`scripts/lint-conventions.sh`).
- **Schema/syntax:** YAML parses; the ocb manifest parses; Helm values lint (`helm lint`); Terraform
  `fmt -check` and `validate`; Python lint/format (`ruff`) once agent code exists.
- **Why it matters:** most "broken deploy" incidents are a malformed manifest or an unpinned version,
  caught here for free.

### 2. Unit (every PR, seconds)

Pure-logic tests of the agent, no cluster.

- **Step engine idempotency:** re-running a `done` step is skipped; a run resumes from the first
  `failed` step; the ledger records one row per step per run. This is the load-bearing guarantee.
- **gojira-reader:** parsing `gojira.yaml` + workload class into the app/cluster model, including the
  optional `observability:` override block and its defaults.
- **Renderers:** a profile renders to the expected CRDs / Helm values for a given workload class.
- **Why it matters:** the agent's safety to re-run and its class -> profile correctness are the core
  promises; they must hold without touching a cluster.

### 3. Contract (every PR, seconds)

Golden-file and assertion tests over **rendered** output, so the enablement-guide opinions can never
silently drift.

- `memory_limiter` is first and `batch` is last in every rendered pipeline.
- `k8sattributes` is passthrough on the Agent and enriches on the Gateway.
- `resource` (not `attributes`) carries environment enrichment; `groupbyattrs` promotes pod/namespace
  for KSM/cAdvisor.
- Exporters use OTLP gRPC + zstd to backends, OTLPHTTP for logs; collector self-monitoring on
  `:13133`/`:8888` is wired.
- **Why it matters:** these are non-negotiable defaults. A contract test is how "non-negotiable"
  becomes enforceable rather than aspirational.

### 4. Integration / smoke (gated, minutes)

The real round-trip, against an ephemeral cluster (e.g. `kind`), gated to run on demand and on release,
not on every PR.

- Bring up the platform on a throwaway cluster; assert backends become healthy and the collector
  scrapes itself.
- **The Phase-1 DoD gate:** a synthetic metric and trace emitted by a sample app are queryable in
  Perses, with a default dashboard and a golden-signal SLO present.
- **Why it matters:** this is the definition of done for Phase 1. Everything else exists to make this
  pass reliably.

## Running tests

A `make test` target (added with the first code) runs layers 1-3 locally. Layer 4 has its own target
and runs in CI on demand and at release. CI wires the same commands so local and CI behavior match.

## What we deliberately do not do

- No coverage-percentage gates. Meaningful tests over line counts.
- No mocking of things we own so heavily that the test only proves the mock.
- No slow tests in the per-PR path. If it needs a cluster or the network, it belongs in layer 4.
