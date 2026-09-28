---
card: arch-card:argus-step-engine
updated: 2026-09-28
anchor: agent/argus/engine/engine.py sha=0c0d2e64d95801fbd00790e72ad86d817c6e9316
anchor: agent/argus/engine/ledger.py sha=4352b39e0fccfb319d8dc49833b3d7d30870946c
---
# step-engine — idempotent check→apply→record over a SQLite ledger

WHAT / SEAM: `Step` Protocol (agent/argus/engine/engine.py) — `name: str`, `tooling: str`, `check(ctx) -> str | None`, `apply(ctx) -> str`, optional `ephemeral: bool` attribute. `ProvisionEngine(steps: list[Step]).run(ledger, target_ref, ctx) -> RunResult`. `Ledger` (agent/argus/engine/ledger.py) — SQLite (stdlib `sqlite3`, WAL mode), one `provision_steps` row per `(target_ref, step_name)` plus an append-only `events` table. Ported from gojira's provisioner shape (engine.py:3).
OPS / FLOW: for each step in order, `ProvisionEngine.run` reads `ledger.step_status(target_ref, step.name)`. A row already `DONE`/`SKIPPED` and the step is NOT `ephemeral` → skip entirely (no `check()` call). Otherwise: `ledger.start_step` (status `running`), call `step.check(ctx)` — a non-`None` return means the step already reflects reality, recorded `SKIPPED` with that value as `resource_ref`; `None` means "do it", so `step.apply(ctx)` runs and its return is recorded `DONE`. Any exception from `check`/`apply` is caught, recorded `FAILED` with the exception text, an `events` row `step_failed` is appended, and the run stops immediately — the engine halts on the FIRST failure so a re-run resumes exactly there.
INVARIANTS:
- `target_ref` is the ledger's scope key (`storage@<region>`, `wire@<app>`, …) — same steps applied under different `target_ref`s are independent; this is what lets region-scoped and app-scoped work interleave in one ledger file without cross-talk.
- `ephemeral = True` steps (every concrete step in agent/argus/steps.py that touches Helm state, rendered CRDs, the ruler, or Perses) are NEVER ledger-skipped even when `DONE` — `check()` re-decides against the real world every run, because the resource they manage can drift out-of-band (a chart edited by hand, a CRD deleted). Non-ephemeral steps (`EnsureNamespace`, `EnsurePlatformInfo`'s born-creation half) are safe to trust the ledger for because they are create-once/idempotent by construction.
- `ProvisionEngine.__init__` raises on duplicate step names in one sequence — the ledger's `UNIQUE(target_ref, step_name)` constraint depends on names being unique within a run.
- The ledger's clock is injectable (`Ledger(path, now_fn=...)`) specifically so tests are deterministic (docs/testing.md, "Step engine idempotency: the load-bearing guarantee").
- `RunResult.completed` is only `True` when every step in the sequence finished `DONE` or `SKIPPED` with no failure; callers (cli.py's `cmd_platform`/`cmd_observe`) branch on this and on `failed_step`/`error` to build the operator's or the Job's `ARGUS_SUMMARY` line.
CONSUMERS: every concrete step class in agent/argus/steps.py (`EnsureNamespace`, `HelmInstall`, `EnsurePlatformInfo`, `ApplyRendered`, `WaitForCollectors`, `ValidateRoundTrip`, `SyncRules`, `ProvisionPerses`) implements the `Step` Protocol; agent/argus/cli.py's `cmd_platform`/`cmd_observe` build the ordered step list and drive `ProvisionEngine.run`.
API: `Ledger.step_status`, `Ledger.start_step`, `Ledger.finish_step`, `Ledger.record_event`, `Ledger.events` — the only public surface; nothing outside engine/ reads `provision_steps`/`events` directly.
