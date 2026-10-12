---
card: arch-card:noto-step-engine
updated: 2026-09-28
anchor: agent/noto/engine/engine.py sha=96fb650e80c80b71a542475f50590b1a022d507a
anchor: agent/noto/engine/ledger.py sha=4352b39e0fccfb319d8dc49833b3d7d30870946c
---
# step-engine — idempotent check→apply→record over a SQLite ledger

WHAT / SEAM: `Step` Protocol (agent/noto/engine/engine.py) — `name: str`, `tooling: str`, `check(ctx) -> str | None`, `apply(ctx) -> str`, optional `ephemeral: bool` attribute. `ProvisionEngine(steps: list[Step]).run(ledger, target_ref, ctx) -> RunResult`. `Ledger` (agent/noto/engine/ledger.py) — SQLite (stdlib `sqlite3`, WAL mode), one `provision_steps` row per `(target_ref, step_name)` plus an append-only `events` table. Ported from gojira's provisioner shape (engine.py:3).
OPS / FLOW: for each step in order, `ProvisionEngine.run` reads `ledger.step_status(target_ref, step.name)`. A row already `DONE`/`SKIPPED` and the step is NOT `ephemeral` → skip entirely (no `check()` call). Otherwise: `ledger.start_step` (status `running`), call `step.check(ctx)` — a non-`None` return means the step already reflects reality, recorded `SKIPPED` with that value as `resource_ref`; `None` means "do it", so `step.apply(ctx)` runs and its return is recorded `DONE`. Any exception from `check`/`apply` is caught, recorded `FAILED` with the exception text, an `events` row `step_failed` is appended, and the run stops immediately — the engine halts on the FIRST failure so a re-run resumes exactly there.
INVARIANTS:
- `target_ref` is the ledger's scope key (`storage@<region>`, `wire@<app>`, …) — same steps applied under different `target_ref`s are independent; this is what lets region-scoped and app-scoped work interleave in one ledger file without cross-talk.
- `ephemeral = True` steps (every concrete step in agent/noto/steps.py except `EnsureNamespace`) are NEVER ledger-skipped even when `DONE`: `check()` re-decides against the real world every run, because the resource they manage can drift out-of-band (a chart edited by hand, a CRD deleted). `EnsureNamespace` is the only non-ephemeral step, so it is the only one the ledger can skip. Ephemerality is per step: `EnsurePlatformInfo` is `ephemeral = True` as a whole.
- `ProvisionEngine.__init__` raises on duplicate step names in one sequence — the ledger's `UNIQUE(target_ref, step_name)` constraint depends on names being unique within a run.
- The ledger's clock is injectable (`Ledger(path, now_fn=...)`) specifically so tests are deterministic (docs/testing.md, "Step engine idempotency: the load-bearing guarantee").
- `RunResult.completed` is only `True` when every step in the sequence finished `DONE` or `SKIPPED` with no failure; cli.py's `cmd_platform` and `cmd_observe` branch on this for their exit code (operator mode has no summary line); `_observe_job` branches on it and on `failed_step`/`error` to build the Job's `ARGUS_SUMMARY` line.
CONSUMERS: every concrete step class in agent/noto/steps.py (`EnsureNamespace`, `HelmInstall`, `EnsurePlatformInfo`, `ApplyRendered`, `WaitForCollectors`, `ValidateRoundTrip`, `SyncRules`, `ProvisionPerses`) implements the `Step` Protocol; agent/noto/cli.py's `cmd_platform` and `_run_observe` (shared by `cmd_observe` and `_observe_job`) build the ordered step list and drive `ProvisionEngine.run`.
API: `Ledger.step_status`, `Ledger.start_step`, `Ledger.finish_step`, `Ledger.record_event`, `Ledger.events` — the only public surface; nothing outside engine/ reads `provision_steps`/`events` directly.
