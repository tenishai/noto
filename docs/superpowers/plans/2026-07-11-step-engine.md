# Step Engine + SQLite Ledger - Implementation Plan (issue #18)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Compact plan: the
> design is already specified (design.md data model + gojira's proven provisioner pattern); this
> ports it to Argus's shape (stdlib sqlite3, target_ref scoping across region/cluster/app).

**Goal:** the idempotent, resumable `check() -> apply() -> record` engine with the SQLite ledger,
scope-aware via `target_ref` (e.g. `storage@us-east4`, `bootstrap@dev-cluster`, `wire@clips`).

**Interfaces:**

```python
# agent/argus/engine/ledger.py
class Ledger:                      # stdlib sqlite3, WAL; schema: provision_steps + events
    def __init__(self, path: Path, now_fn: Callable[[], str] = utc_now_iso): ...
    def step_status(self, target_ref: str, step_name: str) -> StepRow | None
    def record_event(self, target_ref: str, type: str, payload: dict) -> None
    # provision_steps: id, target_ref, step_name, tooling, status
    #   (pending|running|done|skipped|failed), resource_ref, error, started_at,
    #   finished_at; UNIQUE(target_ref, step_name)
    # events: id, target_ref, type, payload_json, created_at (append-only)

# agent/argus/engine/engine.py
class StepStatus(StrEnum): PENDING RUNNING DONE SKIPPED FAILED
class Step(Protocol):
    name: str; tooling: str        # tf|helm|crd|cli
    ephemeral: bool = False        # DONE never skips; check() re-decides each run
    def check(self, ctx: object) -> str | None: ...   # existing resource ref -> SKIPPED
    def apply(self, ctx: object) -> str: ...          # create; ref for the ledger
@dataclass
class RunResult: completed: bool; steps_run: list[str]; steps_skipped: list[str];
                 failed_step: str | None; error: str | None
class ProvisionEngine:
    def __init__(self, steps: list[Step])              # duplicate names rejected
    def run(self, ledger: Ledger, target_ref: str, ctx: object) -> RunResult
    # done/skipped rows skip (unless ephemeral); check-hit -> SKIPPED(+ref);
    # apply -> DONE(+ref); exception -> FAILED(+error) + step-failed event + halt.
```

**TDD tasks (one commit each):**
1. Ledger: schema init idempotent; step row upsert/read; events append-only; injectable clock
   (deterministic tests, per testing.md).
2. Engine happy path + idempotency: fresh run applies all; second run skips all via ledger.
3. Failure semantics: exception -> FAILED + error recorded + halt (later steps untouched) +
   `step_failed` event; resume retries only the failed step onward.
4. check()-existing -> SKIPPED with resource_ref; ephemeral steps re-check despite DONE;
   duplicate step names raise; distinct target_refs are independent (a second cluster's run
   doesn't touch the region scope's rows - the scope-sharing property).

**Out of scope:** concrete steps (land with #20-22), orchestrator across scopes (lands with the
skills, #23), techne migration.
