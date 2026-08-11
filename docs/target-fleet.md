# Target Fleet - the first real deployment

> **Status:** Living document, accepted 2026-07-09
> **What this is:** the concrete production estate Argus is being built to observe first, and the
> constraints that estate imposes. [design.md](design.md) stays deliberately generic; this document
> is where the real numbers live.
> **Scale posture (important):** these are *one deployment's instance parameters*, never the design
> envelope. Argus is built for arbitrary scale - many teams, many companies, small and massive -
> and each deployment is sized from its own measurements (see the scale-posture decision in
> [design.md](design.md)). Nothing in the architecture may be justified by these numbers. Adapted from an earlier internal fleet roadmap by a teammate,
> which predates the current design; its scale parameters, migration strategy, correlation
> acceptance criteria, and remediation guardrails are preserved here so they shape the build.

## The estate

| Parameter | Value | Design consequence |
|---|---|---|
| Clusters | 11 production GKE clusters (plus dev/qa) | One `regional` topology instance per region; the fleet is the first real multi-cluster tenant test |
| Log volume | ~2K requests/10s per cluster, ~2,200 logs/sec fleet-wide | An instance parameter: this deployment's queue/backends start small and grow from measurements. Other deployments plug their own numbers into the same sizing inputs |
| Retention | 1 week raw, then aggregated | Matches the aggregate-then-discard-raw lever in [transport-and-routing.md](transport-and-routing.md); Mimir compactor and Loki/Tempo retention configured to 7d raw from day one |
| Languages | Go, Python | OTel SDK instrumentation targets: Go (shared instrumentation library) and Python (`opentelemetry-instrumentation` auto-instrumentation) |
| Incumbent | Datadog (APM and more) | The business case is the migration; see below |

These numbers are the inputs the design already asks for: the queue entry criteria say to size from
measured egress, the Helm values need target volumes, and the Perses spike should use realistic
per-cluster rates. When the numbers change, change them here (this file is the single home for
fleet-scale facts, the way [versions.md](versions.md) is for pins).

## Datadog migration: parallel-run, then deprecate

Getting off Datadog is the fleet's business driver, and the safe path is a **parallel run**, not a
cutover:

1. **Instrument once, ship twice.** Services instrument with the OTel SDK (never the Datadog SDK).
   During the transition window the gateway adds a temporary `datadog` exporter alongside the
   normal path, so Datadog and Mimir/Loki/Tempo receive the same telemetry simultaneously.
2. **Validate side by side.** Dashboards and alerts are compared between Datadog and Perses for the
   pilot services until the numbers agree and the on-call trusts them.
3. **Deprecate per signal, per service.** APM (traces) first, per the original fleet plan, then
   custom metrics, then logs. Each service's Datadog egress is removed when its Argus equivalents
   pass validation.
4. **Exit criterion:** Datadog APM can be turned off fleet-wide. The `datadog` exporter is deleted
   from the rendered config (it exists only during the window; it is never a permanent fixture -
   see the vendor-egress stance in [design.md](design.md)).

The dual-ship window costs double egress for its duration; that is the price of a no-leap-of-faith
migration and it is temporary by construction.

## Correlation acceptance checklist (folds into Phase 1/2 DoD)

The original fleet roadmap's correlation criteria are adopted as acceptance checks, and most belong
to Phase 1/2 content rendering, not Phase 4:

- [ ] `trace_id` (and `span_id`) injected into structured log records by the shared instrumentation
      setup, so logs and traces join on it.
- [ ] Exemplars enabled end to end (SDK -> collector -> Mimir), so a latency histogram point links
      to the trace that caused it.
- [ ] The click-through works in the visualization layer: metric spike -> exemplar trace -> logs
      for that trace. This is a named check in the Perses capability spike (issue #11); if Perses
      cannot do the click-through, that is fallback-triggering information.
- [ ] Service topology visible from Tempo's service graph (or the spanmetrics-derived equivalent).
- [ ] Error-investigation path: error-rate panel -> error traces -> error logs, on a default
      profile dashboard, with zero per-app configuration.

## Remediation guardrails (requirements for the observer agent, recorded now)

Self-healing is deliberately **not** Argus scope: Argus detects and diagnoses; acting is the
observer persona (see the north star in [design.md](design.md)) using liminal-gated tools under
cabal's guardrails (doom-loop detection, fail-closed validators). The original fleet roadmap's
guardrail thinking is preserved here as requirements for that future work, because it is exactly
right:

- **Risk-tiered actions:** low risk (pod restart) auto-executes; medium risk (scale up)
  auto-executes with notification; high risk (rollback, node operations, memory-limit changes)
  requires human approval, with timeout-based escalation.
- **Blast-radius limits:** a cap on the percentage of pods an action may affect, and rate limits
  (max N restarts per hour per service).
- **Cooldowns** after any action, so remediation cannot flap.
- **Manual override:** a fleet-wide pause switch for all automation.
- **The automation is itself observed:** actions taken, success rate, and time-to-remediation are
  dashboards; automation failures alert; every action lands in an audit trail.

Note the family fit: rightsizing-shaped actions already have a safer home - evidence POSTed to
gojira becomes a reviewed PR (a human-gated actuator), which should be preferred over direct
mutation wherever the fix is a resource change.

## What the fleet plan and the Argus design agree on

For the record, the earlier roadmap and the current design converged independently on: Mimir/Loki/
Tempo on GCS, an OTel DaemonSet per cluster with `k8sattributes`/`batch`/`filter`/`resource`
processors, a central HA gateway, per-cluster identification stamped as a resource attribute, SLO-
anchored alerting, and an AI agent deferred until the foundation exists. Where they differ, the
design's answers stand (Perses over Grafana, TargetAllocator scrape over kube-prometheus
remote_write, a durable queue with entry criteria over a hard-wired central path, profiles over
hand-built dashboards) - see [design.md](design.md) for the reasoning.
