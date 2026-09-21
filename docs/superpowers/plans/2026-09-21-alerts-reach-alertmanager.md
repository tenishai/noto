# Alerts Reach Alertmanager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three platform gaps that stop an app's alert from ever reaching Alertmanager: the ruler has no Alertmanager URL, `observe` never pushes an app's rules to the ruler, and alerts carry no label saying whose they are.

**Architecture:** Part 1 of `docs/superpowers/specs/2026-09-21-alerts-reach-people-design.md`. Three independent changes: one helm value set by `argus platform`; one new engine step (`SyncRules`) in `observe` plus pruning shared with `argus rules-sync`; ownership labels in the alert and SLO renderers. Routing to chat (Part 2 of the spec) is NOT in this plan.

**Tech Stack:** Python 3.14, the existing step engine (`agent/argus/steps.py`), Mimir ruler HTTP API, pytest, ruff, golden files under `tests/`.

## Global Constraints

- Ruler API base is the Mimir query URL ending in `/prometheus`; rule paths are `{base}/config/v1/rules/{namespace}` (POST one group as YAML, GET lists the namespace's groups as a YAML map `{namespace: [group, ...]}`, a 404 on GET means "no groups"), and `{base}/config/v1/rules/{namespace}/{group}` (DELETE, 202 on success). Every call carries `X-Scope-OrgID: <tenant>`.
- The ruler namespace for an app's groups is the app name, exactly what `cmd_rules_sync` uses today (`ctx.app or "argus"`). The platform's own namespace `argus-platform` is never pruned by an app run.
- A dry run opens no socket. This is a lived rule in this repo (see `ProvisionPerses.check`).
- HTTP in steps is injected, never patched: follow the `perses_opener` precedent with a field on `StepContext`.
- No client or organisation names anywhere (code, comments, tests, docs). Use `example` values.
- Never pipe a test or lint command into `tail`/`head`: redirect to a file, `echo "exit=$?"`, then read the file. The pipe reports the wrong exit code.
- `make test` runs lint + unit + contract; it must pass before each commit. Golden files change in Task 2 and are regenerated the way the repo already does it (read the Makefile and `tests/` for the existing regeneration path; do not hand-edit goldens).
- Commits end with: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`

---

### Task 1: The ruler is told where Alertmanager is

**Files:**
- Modify: `agent/argus/rendering/context.py` (`solo_endpoints`)
- Modify: `agent/argus/cli.py` (`cmd_platform`, the `HelmInstall` `extra_sets` for `mimir`)
- Test: `tests/unit/rendering/` (wherever `solo_endpoints` is already tested), `tests/unit/test_cli.py`

**Interfaces:**
- Produces: `solo_endpoints(namespace)["alertmanager_url"] == f"http://alertmanager-operated.{namespace}.svc:9093"`
- Produces: the mimir `HelmInstall` carries `mimir.structuredConfig.ruler.alertmanager_url=<that URL for args.namespace>`

- [ ] **Step 1: Failing tests.** (a) `solo_endpoints("obs")["alertmanager_url"] == "http://alertmanager-operated.obs.svc:9093"` and the default namespace gives `...alertmanager-operated.argus.svc:9093`. (b) In `test_cli.py`, follow the existing test that asserts the mimir release receives `max_global_series_per_user` / the retention set, and assert the same run's helm command for `mimir` contains `--set` `mimir.structuredConfig.ruler.alertmanager_url=http://alertmanager-operated.<ns>.svc:9093` with a non-default `--namespace`, and that no other component's helm command contains `alertmanager_url`.
- [ ] **Step 2: Run them, confirm they fail** for the right reason (KeyError / missing set).
- [ ] **Step 3: Implement.** Add the key to `solo_endpoints` and extend its docstring with one sentence: `alertmanager-operated` is the operator's governing service, chosen because the release's own service name is truncated by the chart. In `cmd_platform`, add the set to the `c == "mimir"` dict beside `max_global_series_per_user`, reading the URL from `solo_endpoints(args.namespace)`.
- [ ] **Step 4: Check the golden identity.** `context.py` has a back-compat note that goldens rely on `solo_endpoints("argus")`. Adding a key must not change any rendered document; run `make test` and confirm no golden diff. If a golden does change, stop and report: that means something renders the whole endpoint dict.
- [ ] **Step 5: `make test` green, commit** `feat(platform): tell the ruler where Alertmanager is`.

### Task 2: Every per-app alert says whose it is

**Files:**
- Modify: `agent/argus/rendering/alerts.py` (`render_alert_rules`, `render_platform_rules`)
- Modify: `agent/argus/rendering/slo.py` (the Sloth spec's SLO `labels`)
- Test: `tests/unit/rendering/` alert and slo tests; goldens regenerated

**Interfaces:**
- Produces: every rule with an `alert` key in `render_alert_rules` output has labels `app`, `namespace`, `class`, `environment` (values `ctx.app`, `ctx.namespace`, `p.class_name`, `ctx.environment`) in addition to its existing `severity`. Recording rules (no `alert` key) are untouched.
- Produces: the platform retention alert has `app: argus-platform`.
- Produces: each SLO in the Sloth spec has `labels` containing `app`, `namespace`, `class` (Sloth copies SLO labels onto its generated alerts).

- [ ] **Step 1: Failing tests.** One property-style test: render alert rules for a profile with `app_scrape` on, walk every group and rule, and for each rule with `alert` assert the four labels equal the context values and `severity` is still present. One test that a recording rule, if any group has one, gains no labels. One test for the platform rule's `app` label. One test that every SLO in the rendered Sloth doc has the three labels.
- [ ] **Step 2: Run, confirm failure.**
- [ ] **Step 3: Implement** with one helper in `alerts.py`:

```python
def _owned(rule: dict, owner: dict[str, str]) -> dict:
    """The rule with the owner's labels under its own: a rule's own label wins, so a
    pack can still say something different on purpose."""
    if "alert" not in rule:
        return rule
    return {**rule, "labels": {**owner, **rule.get("labels", {})}}
```

Apply it to every rule of every group in `render_alert_rules` (build `owner` once from `ctx`/`p`). The module-level `_COLLECTOR_HEALTH_RULES` must not be mutated: `_owned` returns new dicts. In `slo.py`, add the three labels to each SLO's `labels` without dropping any the SLO already has.
- [ ] **Step 4: Regenerate goldens** by the repo's existing path, then read the golden diff: it must contain only added label lines. Anything else in the diff is a bug in this task.
- [ ] **Step 5: `make test` green, commit** `feat(alerts): every per-app alert carries app, namespace, class and environment`.

### Task 3: `observe` pushes the app's rules to the ruler and prunes stale groups

**Files:**
- Modify: `agent/argus/feedback/ruler.py` (list + delete + prune, injected HTTP for all verbs)
- Modify: `agent/argus/steps.py` (`StepContext` fields, new `SyncRules` step)
- Modify: `agent/argus/cli.py` (`_run_observe` step list, `_step_context`, `cmd_rules_sync` prunes, `_RULER_NOTE` wording)
- Modify: `README.md` ("Rules reach Mimir's ruler" section), `CHANGELOG.md`
- Test: `tests/unit/feedback/` ruler tests, new `tests/unit/test_sync_rules_step.py`, `tests/unit/test_cli.py`, `tests/unit/test_job.py`

**Interfaces:**
- Consumes: `sync_rules(mimir_base, tenant, namespace, docs, http=...) -> int` (exists).
- Produces, in `ruler.py`:
  - `Http` becomes `Callable[[str, str, bytes | None, dict], tuple[int, str]]` with the METHOD first (`http(method, url, data, headers)`); `_urllib_http` and `sync_rules` and every existing caller/test are updated to it. `_urllib_http` must turn `urllib.error.URLError` (not `HTTPError`) into `RulerUnreachable(RulerSyncError)` carrying the reason, so callers no longer catch urllib types.
  - `list_groups(mimir_base, tenant, namespace, http=...) -> list[str]`: group names; `[]` on 404.
  - `prune_groups(mimir_base, tenant, namespace, keep: set[str], http=...) -> list[str]`: deletes every listed group not in `keep`, returns the deleted names; a non-2xx delete raises `RulerSyncError`.
  - `rendered_group_names(docs) -> set[str]`.
- Produces, in `steps.py`: `StepContext.ruler_url: str | None`, `StepContext.tenant: str`, `StepContext.ruler_namespace: str | None`, `StepContext.ruler_http: Http | None` (tests only, same role as `perses_opener`), `StepContext.ruler_required: bool` (job mode sets True).
- Produces: `SyncRules` step, `name = "sync-rules"`, `ephemeral = True`.

`SyncRules` behaviour (this table is the test list):

| Case | `check()` | `apply()` result |
|---|---|---|
| no `PrometheusRule` docs in `rendered["alerts"]` + `rendered["slo"]` | returns `"no rules rendered"` (skip) | not called |
| dry run | `None`, no HTTP | returns `would push N group(s) to <url> as tenant <t>, namespace <ns>`; no HTTP call made (assert the injected http was never called) |
| push ok, nothing stale | `None` | `pushed N group(s); pruned 0` |
| push ok, ruler holds a group not rendered | `None` | `pushed N group(s); pruned 1 (<name>)`, and the DELETE went to `/config/v1/rules/<ns>/<name>` with the tenant header |
| ruler rejects (4xx/5xx body) | | raises, step fails, message names the group |
| `RulerUnreachable`, `ruler_required=False` | | returns `not pushed: <url> is unreachable from here (port-forward and use --mimir-url)`; step succeeds |
| `RulerUnreachable`, `ruler_required=True` | | raises; step fails |

Order matters: push every group first, prune second, so a failed push never deletes the old groups.

- [ ] **Step 1: Failing ruler tests** for `list_groups` (200 with a YAML map, 404 → `[]`), `prune_groups` (deletes only the stale one; keep-set respected; failed delete raises), the new `Http` signature in `sync_rules`, and `RulerUnreachable` from a `URLError`. Use a recording fake `http` that returns canned `(status, body)` per `(method, url)`.
- [ ] **Step 2: Implement `ruler.py`**, update `_push_platform_rules` and `cmd_rules_sync` to the new signature and to catching `RulerUnreachable` instead of `urllib.error.URLError`. Run the ruler tests and `tests/unit/test_ruler_push.py`.
- [ ] **Step 3: Failing step tests** in `tests/unit/test_sync_rules_step.py`, one per row of the table above, built on a `StepContext` with `ruler_http` injected. Read `tests/unit/test_perses_step.py` first and mirror its construction of the context.
- [ ] **Step 4: Implement `SyncRules`** in `steps.py`, following `ProvisionPerses` for shape (dataclass, `name`, `tooling = "mimir-ruler"`, `ephemeral = True`, `check`/`apply`). Docstring states why it exists in one paragraph: applied `PrometheusRule` objects are inert here, rules live only in the ruler, and an app provisioned by job mode previously had alerts nothing evaluated.
- [ ] **Step 5: Wire it.** In `_run_observe`, append `SyncRules()` after the `slo` step and before `ProvisionPerses()`. In `_step_context`, set `ruler_url` from `_resolve_mimir_url(args)`, `tenant` and `ruler_namespace` from the resolved context (`ctx.tenant`, `ctx.app`). `_step_context` is called before the tenant is resolved in some paths: read `_run_observe` and pass the values where the resolved `ctx` exists rather than guessing; if `_step_context` needs new keyword arguments, add them with defaults so `cmd_platform` is untouched. `_observe_job` sets `ruler_required=True`.
- [ ] **Step 6: CLI tests.** In `test_cli.py`: an `observe --dry-run` run lists a `sync-rules` step and makes no HTTP call. In `test_job.py`: a job run whose ruler is unreachable ends with `ARGUS_SUMMARY observe failed:` and exit 1; a job run whose ruler accepts ends with the success summary and the step counted in `N steps applied`. Check whether any existing test pins the exact step count for observe and update it.
- [ ] **Step 7: `rules-sync` prunes too.** After its `sync_rules` call, call `prune_groups` with the rendered names and print `pruned N stale group(s): ...` when N > 0. Test it. This closes issue #57; say so in the CHANGELOG entry.
- [ ] **Step 8: Docs.** README section "Rules reach Mimir's ruler": `observe` now pushes and prunes on every run; `rules-sync` remains for re-pushing without re-observing; the operator-mode unreachable note and the job-mode failure are both described in one short paragraph. Replace `_RULER_NOTE` so it no longer tells the reader that only `rules-sync` pushes app groups. CHANGELOG under Unreleased: three bullets, one per task.
- [ ] **Step 9: `make test` green, commit** `feat(observe): push the app's rules to the ruler and prune stale groups (closes #57)`.

---

## After the plan (operator, not the implementer)

1. Merge, let the image workflow publish, re-run `argus platform --env dev --cluster <cluster> --context <ops context>` so the ruler gets its Alertmanager URL.
2. Re-observe one app. Confirm its groups appear in the ruler under its own namespace.
3. Live proof: push a throwaway always-firing rule with `argus rules-sync`-shaped input, confirm it appears at Alertmanager's `/api/v2/alerts` with the `app` label, then remove it. It lands on the `"null"` receiver until routing (spec Part 2) exists.
