# Born Observed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `argus observe` runnable as a one-shot in-cluster Kubernetes Job that a provisioner launches with an app's contract, and make that safe: a platform-owned collector tier, dashboards as an engine step, a published image, and an enforced, alarmed retention promise.

**Architecture:** The shared collector tier moves from `observe` to `platform`, rendered from a platform profile (baseline + all signals), which removes the last-writer-wins gateway bug and shrinks the job's permissions to three monitoring CRD kinds. `observe` gains an environment-driven job entry path that verifies the contract fingerprint, runs with a throwaway ledger, annotates what it applies, provisions Perses over in-cluster HTTP, and always ends with one `ARGUS_SUMMARY` line.

**Tech Stack:** Python 3.14, pydantic, pyyaml, stdlib `urllib`; `kubectl` shelled through the injectable `Runner`; pytest (unit + `contract` golden marker), ruff; GitHub Actions; Docker.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-17-born-observed-design.md`. Exact names and values below are copied from it.
- Env inputs: `GOJIRA_CONTRACT_YAML`, `GOJIRA_CONTRACT_SHA256`, `ARGUS_ENV`, `ARGUS_CLUSTER` (optional), `ARGUS_NAMESPACE` (default `argus`). Job mode is selected by the presence of `GOJIRA_CONTRACT_YAML`.
- Fingerprint algorithm, byte for byte: `doc = yaml.safe_load(text)`; `canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)`; `hashlib.sha256(canon.encode()).hexdigest()`.
- Final stdout line is ALWAYS `ARGUS_SUMMARY <one line>`, single line, at most 300 characters, on success and on every failure path. Exit 0 complete, 1 failed.
- Annotation key: `argus.dev/contract-sha256`.
- Job identity: ServiceAccount `argus-observe`; ClusterRole verbs `get, list, create, patch, update` on `servicemonitors`, `podmonitors`, `prometheusrules` in apiGroup `monitoring.coreos.com`. Nothing else.
- In-cluster endpoints derive from the platform namespace `<ns>`: `http://argus-mimir-gateway.<ns>.svc:80`, `http://argus-loki.<ns>.svc:3100`, `argus-tempo.<ns>.svc:4317`, `http://argus-perses.<ns>.svc:8080`. Explicit flags and env overrides win.
- Retention alert name: `ArgusRetentionWindowShort`. ConfigMap name: `argus-platform-info`, key `born` (RFC 3339 UTC).
- No client, vendor, or registry names in any file. Registry, workload identity provider, and service account come from repository variables.
- This repo's laws: run tools as `PYTHONPATH=agent .venv/bin/python -m pytest ...` (the venv must be Python 3.14; if `import argus` fails, the venv is stale: recreate it with `uv venv --python 3.14 && uv sync`). Lint gate is `make lint` (`./scripts/lint-conventions.sh`, `ruff check .`, `ruff format --check .`). NEVER pipe a test or lint command into tail/head (the pipe hides the exit code): redirect to a file, `echo "exit=$?"`, then tail the file.
- Golden files under `tests/contract/golden/` are regenerated only through the repo's documented regeneration path (read `tests/contract/test_golden.py` and `docs/testing.md` first); every golden change must be explained in the task report.
- Commit with `git -c commit.gpgsign=false commit`; messages end with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Documentation and comments: no em dashes.

---

### Task 1: Contract fingerprint, job-mode inputs, and the summary line

**Files:**
- Create: `agent/argus/job.py`
- Modify: `agent/argus/cli.py` (`cmd_observe`, `_report`, `main`)
- Test: `tests/unit/test_job.py`

**Interfaces (produced):**
```python
# agent/argus/job.py
SUMMARY_PREFIX = "ARGUS_SUMMARY "
SUMMARY_MAX = 300

def contract_fingerprint(gojira_yaml_text: str) -> str: ...

@dataclass(frozen=True)
class JobInputs:
    contract_yaml: str
    contract_sha256: str
    env: str
    cluster: str | None
    namespace: str          # platform namespace, default "argus"

class JobInputError(ValueError): ...

def job_inputs(environ: Mapping[str, str]) -> JobInputs | None:
    """None when GOJIRA_CONTRACT_YAML is absent (operator mode).
    Raises JobInputError when present but the inputs are unusable."""

def summary_line(text: str) -> str:
    """'ARGUS_SUMMARY ' + text collapsed to one line, clipped to SUMMARY_MAX total."""

def success_summary(app: str, env: str, applied: int, unchanged: int, sha: str) -> str
def failure_summary(step: str | None, reason: str) -> str
```

- [ ] **Step 1: failing tests** in `tests/unit/test_job.py`:

```python
import hashlib, json
import pytest
from argus.job import (JobInputError, SUMMARY_MAX, contract_fingerprint, failure_summary,
                       job_inputs, success_summary, summary_line)

A = "app: demo\nclass: web\nenvironments:\n  dev: {cluster: c1, namespace: demo}\n"

def test_fingerprint_matches_the_provisioners_algorithm():
    doc = {"app": "demo", "class": "web", "environments": {"dev": {"cluster": "c1", "namespace": "demo"}}}
    want = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    assert contract_fingerprint(A) == want

def test_fingerprint_ignores_format_but_not_meaning():
    reordered = "# a comment\nclass: web\napp: demo\nenvironments:\n  dev:\n    namespace: demo\n    cluster: c1\n"
    assert contract_fingerprint(reordered) == contract_fingerprint(A)
    assert contract_fingerprint(A.replace("c1", "c2")) != contract_fingerprint(A)

def test_job_inputs_absent_means_operator_mode():
    assert job_inputs({}) is None

def test_job_inputs_happy_path_and_defaults():
    sha = contract_fingerprint(A)
    got = job_inputs({"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": sha, "ARGUS_ENV": "dev"})
    assert (got.env, got.cluster, got.namespace, got.contract_sha256) == ("dev", None, "argus", sha)
    got = job_inputs({"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": sha.upper(), "ARGUS_ENV": "dev",
                      "ARGUS_CLUSTER": " real-name ", "ARGUS_NAMESPACE": "obs"})
    assert (got.cluster, got.namespace, got.contract_sha256) == ("real-name", "obs", sha)

@pytest.mark.parametrize("env,needle", [
    ({"GOJIRA_CONTRACT_YAML": A, "ARGUS_ENV": "dev"}, "GOJIRA_CONTRACT_SHA256"),
    ({"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": "0" * 64, "ARGUS_ENV": "dev"}, "does not match"),
    ({"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": "xyz", "ARGUS_ENV": "dev"}, "64 hex"),
    ({"GOJIRA_CONTRACT_YAML": ": : not yaml [", "GOJIRA_CONTRACT_SHA256": "0" * 64, "ARGUS_ENV": "dev"}, "not valid YAML"),
    ({"GOJIRA_CONTRACT_YAML": "- a list\n", "GOJIRA_CONTRACT_SHA256": "0" * 64, "ARGUS_ENV": "dev"}, "mapping"),
])
def test_job_inputs_rejects_unusable_inputs(env, needle):
    with pytest.raises(JobInputError, match=needle):
        job_inputs(env)

def test_job_inputs_requires_a_known_env():
    sha = contract_fingerprint(A)
    with pytest.raises(JobInputError, match="ARGUS_ENV"):
        job_inputs({"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": sha})
    with pytest.raises(JobInputError, match="ARGUS_ENV"):
        job_inputs({"GOJIRA_CONTRACT_YAML": A, "GOJIRA_CONTRACT_SHA256": sha, "ARGUS_ENV": "staging"})

def test_summary_line_is_one_bounded_line():
    line = summary_line("first\n\nsecond\tthird " + "x" * 500)
    assert line.startswith("ARGUS_SUMMARY first second third ")
    assert "\n" not in line and len(line) == SUMMARY_MAX

def test_success_and_failure_summaries():
    assert success_summary("demo", "dev", 3, 2, "ab" * 32) == \
        "ARGUS_SUMMARY observed demo/dev: 3 applied, 2 unchanged, contract abababababab"
    assert failure_summary("apply-alerts", "kubectl: forbidden\nmore") == \
        "ARGUS_SUMMARY observe failed: apply-alerts: kubectl: forbidden more"
    assert failure_summary(None, "bad inputs") == "ARGUS_SUMMARY observe failed: bad inputs"
```

- [ ] **Step 2:** run, expect ImportError.
- [ ] **Step 3: implement `agent/argus/job.py`.** `contract_fingerprint` exactly per Global Constraints. `job_inputs`: return None without `GOJIRA_CONTRACT_YAML`; require `GOJIRA_CONTRACT_SHA256` matching `^[0-9a-fA-F]{64}$` (message contains "64 hex"), lowercase it; `yaml.safe_load` failure raises with "not valid YAML"; a non-mapping document raises with "mapping"; fingerprint mismatch raises with "does not match" and names both 12-character prefixes; `ARGUS_ENV` must be one of `dev|qa|prod` (message contains "ARGUS_ENV"); `ARGUS_CLUSTER` stripped, empty becomes None; `ARGUS_NAMESPACE` default `argus`. `summary_line`: `" ".join(text.split())`, prefix, clip to `SUMMARY_MAX` total characters.
- [ ] **Step 4: wire the CLI.** In `cli.py`:
  - `main()`: before argparse handles `observe`, nothing changes for operators. Add a private `_observe_job(inputs: JobInputs, args) -> int` used when `job_inputs(os.environ)` is not None AND the subcommand is `observe`. In job mode `--gojira` and `--env` are not required: make them optional in the `observe` parser and error (exit 2, with a failure summary line) when neither job inputs nor `--gojira`/`--env` are present.
  - `_observe_job`: write `inputs.contract_yaml` to a file in `tempfile.mkdtemp(prefix="argus-job-")`, set `args.gojira`, `args.env = inputs.env`, `args.cluster = inputs.cluster or args.cluster`, `args.ledger = <tmpdir>/argus.db`, then run the same sequence `cmd_observe` runs.
  - The summary: count `applied = len(result.steps_run)`, `unchanged = len(result.steps_skipped)`. On completion print `success_summary(...)`; on failure print `failure_summary(result.failed_step, result.error or "unknown error")`. `JobInputError` (and any exception escaping job mode) prints `failure_summary(None, str(exc))` and returns 1. The summary is printed to **stdout** with `flush=True` as the very last output; `_report`'s existing lines stay on stderr.
  - Operator mode output is unchanged (no summary line) so existing tests and habits hold.
- [ ] **Step 5: CLI tests** in `tests/unit/test_job.py` (or the existing CLI test module if one exists; check `tests/unit/` first) driving `main([...])` with a fake runner the way existing CLI/step tests inject one: job mode success ends stdout with a success summary and returns 0; a failing step ends with a failure summary naming the step and returns 1; a fingerprint mismatch applies NOTHING (the fake runner records zero calls) and returns 1 with a failure summary; the ledger path used is under the temp dir, not `.argus/`.
- [ ] **Step 6:** `make lint` and the unit suite, exit 0. Commit `feat(job): contract fingerprint, env-driven job mode, and the ARGUS_SUMMARY line`.

---

### Task 2: Record which contract was observed

**Files:** Modify `agent/argus/steps.py` (`ApplyRendered`), `agent/argus/cli.py` (`_step_context`), `StepContext`; Test: the existing steps unit test module.

**Interfaces:** `StepContext` gains `annotations: dict[str, str] = field(default_factory=dict)`. `ApplyRendered.apply` merges `ctx.annotations` into `metadata.annotations` of every document it applies (existing annotations win on key conflict only if identical keys are not ours; our key always takes our value). Job mode sets `{"argus.dev/contract-sha256": inputs.contract_sha256}`.

- [ ] Failing tests: with annotations set, every document handed to the runner's stdin carries the key; documents with no `metadata.annotations` get one; an existing unrelated annotation survives; with empty annotations the YAML sent is byte-identical to today (protects goldens and operators).
- [ ] Implement by copying each doc before mutation (rendered families are reused; never mutate the caller's objects).
- [ ] Wire job mode to pass the annotation; operator mode passes none.
- [ ] `make lint`, unit + contract suites, exit 0. Commit `feat(steps): applied objects record the observed contract`.

---

### Task 3: The collector tier belongs to the platform

**Files:** Modify `agent/argus/cli.py` (`_render_families`, `cmd_platform`, `cmd_observe`, `cmd_render`, parsers), possibly `agent/argus/profiles/` loaders; Test: `tests/unit/`, `tests/contract/` goldens.

**Interfaces:**
```python
def _platform_profile() -> ResolvedProfile:
    """Baseline resolved with no class deltas and ALL signals: metrics, logs, traces."""
def _render_platform_families(ctx: Context) -> dict[str, list[dict]]:
    """{'collectors': [priorityclass, *ta_rbac, agent, gateway, target_allocator]}"""
def _render_families(p, ctx) -> dict[str, list[dict]]:
    """Per-app only: alerts, perses, scrape (when app_scrape), slo (when sloth). NO collectors."""
```
`platform` gains a required `--env {dev,qa,prod}` (the collector tier stamps `environment`); its `Context` has `app=None`/`namespace=None` as the dataclass allows (read `agent/argus/rendering/context.py`; if `app`/`namespace` are required, give the platform context the platform namespace and an app of `"argus"`, and say so in the report).

- [ ] **Step 1: the regression test, named for the bug.** `test_observing_an_app_without_traces_does_not_change_the_gateway`: render platform families once; render per-app families for a class without traces; assert no document of kind `OpenTelemetryCollector` appears in any per-app family, and the platform gateway's `service.pipelines` has `metrics`, `logs`, `traces` (plus `metrics/self`).
- [ ] **Step 2:** `test_platform_profile_has_all_signals_and_only_baseline_rules`: the platform gateway config equals what `render_gateway` produces for the baseline profile with signals forced to all three; assert it does not vary with any class file (render it, then assert no class name from `profiles/classes/` influences it by rendering twice after monkeypatching the class loader to raise).
- [ ] **Step 3: implement.** How the platform profile is built: call the existing `resolve(load_baseline(...), <an empty class>, load_override(None))` and then force signals. Read `agent/argus/profiles/resolve.py` for how `signals` is represented (frozen model: use `model_copy(update=...)`). Do not add a fake class file under `profiles/classes/`.
- [ ] **Step 4:** `cmd_platform`: after the `HelmInstall` steps and before `ValidateRoundTrip`, add `ApplyRendered(family="collectors")` with `rendered=_render_platform_families(ctx)`. The collectors need the operator CRDs, which `opentelemetry-operator` in `PLATFORM_ORDER` installs first: keep that ordering and say so in a comment.
- [ ] **Step 5:** `cmd_observe` iterates `("scrape", "alerts")` (+ `slo`), never `collectors`. `cmd_render` writes per-app families, and gains `--platform` to write the platform families instead (so goldens and humans can see them).
- [ ] **Step 6: goldens.** The `collectors.yaml` goldens move from per-app to platform. Follow the repo's regeneration path; add a platform golden per topology the goldens cover; delete the per-app collector goldens. Explain every golden diff in the report. `tests/contract/test_invariants.py` invariants that assumed collectors in the app output must be re-pointed at the platform output, not deleted.
- [ ] **Step 7:** update `README.md` and `docs/design.md` where they describe `observe` rendering the collector tier (grep for "gateway" and "collectors"). One honest sentence on the consequence: per-class shaping rules no longer reach the shared gateway; baseline rules apply to everyone; per-app shaping is a registered follow-up.
- [ ] **Step 8:** `make lint`, `make test`, `make validate` if the local toolchain has the validators (report if not), exit 0. Commit `fix(platform): the collector tier is platform-owned - observing one app can no longer reconfigure the gateway for all`.

---

### Task 4: In-cluster endpoints derive from the platform namespace

**Files:** Modify `agent/argus/rendering/context.py` (`SOLO_DEFAULTS`), callers; `agent/argus/cli.py` flags; Test: `tests/unit/`.

**Interfaces:**
```python
def solo_endpoints(namespace: str = "argus") -> dict[str, str]:
    """mimir_otlp_http, loki_otlp_http, tempo_otlp_grpc, mimir_query_url, perses_url"""
# values: f"http://argus-mimir-gateway.{ns}.svc:80/otlp", f"http://argus-loki.{ns}.svc:3100/otlp",
#         f"argus-tempo.{ns}.svc:4317", f"http://argus-mimir-gateway.{ns}.svc:80/prometheus",
#         f"http://argus-perses.{ns}.svc:8080"
```
`SOLO_DEFAULTS` stays as `solo_endpoints("argus")` for back-compat (goldens must not move). A global `--namespace` flag (default from `ARGUS_NAMESPACE`, else `argus`) feeds it. Verify the Service names and ports against `helm/values/*.yaml` and the chart release names in `helm/pins.yaml`; if a real name differs from the spec's, the chart is the authority: use the real name and report the difference.

- [ ] Tests: defaults for `argus` equal today's constants exactly; a custom namespace changes every host; an explicit `--mimir-url` still wins over the derived default for the commands that take it (`rules-sync`, `card`/`feedback`: read the parsers); their `localhost` defaults become "derived unless given", and the help text says so.
- [ ] Commit `feat(endpoints): in-cluster defaults derive from the platform namespace`.

---

### Task 5: Dashboards are an engine step

**Files:** Create `agent/argus/perses_client.py`; Modify `agent/argus/steps.py` (new `ProvisionPerses`), `agent/argus/cli.py` (`cmd_observe`); Delete `tests/smoke/provision_perses.py`; Modify `tests/smoke/run-smoke.sh`; Test: `tests/unit/test_perses_step.py`.

**Interfaces:**
```python
class PersesError(RuntimeError): ...
class PersesClient:
    def __init__(self, base_url: str, opener: Callable[[urllib.request.Request], tuple[int, bytes]] | None = None, timeout: float = 15.0): ...
    def get(self, path: str) -> dict | None            # None on 404
    def upsert(self, collection_path: str, doc: dict) -> str   # "created" | "updated" | "unchanged"

class ProvisionPerses:        # a Step
    name = "provision-perses"; tooling = "perses"; ephemeral = True
    def check(self, ctx) -> str | None   # skip reason only when EVERY rendered doc is unchanged
    def apply(self, ctx) -> str          # "perses: 1 created, 2 updated, 3 unchanged"
```
Read `tests/smoke/provision_perses.py` FIRST and preserve its proven behavior: the order (project, then datasources, then dashboards), the collection paths (`/api/v1/projects`, `/api/v1/projects/<project>/datasources`, `/api/v1/projects/<project>/dashboards`), and POST-then-PUT-on-409. "Unchanged" compares the rendered `spec` with the server's `spec` (ignore server-managed `metadata` fields such as `createdAt`, `updatedAt`, `version`).

- [ ] Tests with an injected `opener` fake (no sockets): create (404 then 200 on POST); conflict path (409 on POST then PUT 200 = "updated"); unchanged skip (GET returns an equal spec: no POST or PUT is sent, `check()` returns a reason); a 500 raises `PersesError` whose message has the status and the first 200 characters of the body on one line; the step's `apply` return string counts correctly; a transport error (`URLError`) raises `PersesError` naming the base URL.
- [ ] The base URL comes from `solo_endpoints(ns)["perses_url"]` unless `--perses-url` / `ARGUS_PERSES_URL` is given. Dry-run sends nothing and reports what it would send.
- [ ] `cmd_observe` appends `ProvisionPerses()` last (dashboards after the rules they chart). The step gets the `perses` family from `rendered`.
- [ ] Smoke: replace the script call in `tests/smoke/run-smoke.sh` with the CLI path (keep the port-forward; pass `--perses-url http://localhost:8080`). Delete `provision_perses.py`. Update `docs/testing.md` if it names the script.
- [ ] Commit `feat(perses): dashboards are an engine step - the smoke script retires`.

---

### Task 6: The job's identity

**Files:** Modify `agent/argus/rendering/collectors.py` or create `agent/argus/rendering/rbac.py` (follow `render_targetallocator_rbac`'s dict style); `agent/argus/cli.py` (`_render_platform_families`); Test: unit + a platform golden.

**Interfaces:** `render_observe_rbac(namespace: str = "argus") -> list[dict]` returning exactly: ServiceAccount `argus-observe` (namespaced), ClusterRole `argus-observe`, ClusterRoleBinding `argus-observe`. Rules: one rule, apiGroups `["monitoring.coreos.com"]`, resources `["servicemonitors", "podmonitors", "prometheusrules"]`, verbs `["get", "list", "create", "patch", "update"]`.

- [ ] Tests: exactly three documents; the ClusterRole has exactly one rule with exactly those values; no `secrets`, no `*`, no `delete` anywhere in the rendered RBAC (assert on the serialized YAML); the binding's subject is the ServiceAccount in the given namespace.
- [ ] Add to the platform `collectors` family (or a new `identity` family applied by `platform`; pick whichever keeps `ApplyRendered` ordering simple, and say which).
- [ ] An invariant test: server-side apply by this identity is sufficient for `observe`. List every `kind` the per-app families can render (scrape, alerts, slo) and assert each maps to one of the three granted resources. If `slo` renders a kind outside the three (read `render_sloth`), STOP and report instead of widening the role.
- [ ] Document in `README.md`: the Job spec a provisioner should create (serviceAccountName, the five env vars, `restartPolicy: Never`, `ttlSecondsAfterFinished`, image by digest), as a YAML example with placeholder image.
- [ ] Commit `feat(platform): argus-observe - the narrow identity a provisioner's job runs as`.

---

### Task 7: Retention, enforced and alarmed

**Files:** Modify `agent/argus/cli.py` (`cmd_platform` helm `--set` wiring, new steps), `agent/argus/steps.py` (new `EnsurePlatformBorn`), `agent/argus/rendering/alerts.py` (new `render_platform_rules`); Test: unit + golden.

**Interfaces:**
```python
def retention_helm_sets(retention: Mapping[str, str]) -> dict[str, list[str]]:
    """component -> ['--set', 'key=value', ...]; mimir gets limits.compactor_blocks_retention_period=<metrics>."""
class EnsurePlatformBorn:     # Step, check-then-create, NEVER overwrites
    name = "ensure-platform-born"
def render_platform_rules(retention: Mapping[str, str], born_epoch: int) -> dict   # PrometheusRule
```
- [ ] Read how `cli.py:146` passes the cardinality budget to the mimir chart and mirror it for `compactor_blocks_retention_period`. Verify the exact values key against the pinned chart version in `helm/pins.yaml` (`helm show values` if helm is available; otherwise cite the chart docs you relied on in the report). Loki (`limits_config.retention_period` plus the compactor's `retention_enabled`) and Tempo (`compactor.compaction.block_retention`) follow the same pattern; if either key cannot be verified against the pinned chart, wire Mimir only, leave the other two unwired, and say so plainly in the report. Do not guess a values key.
- [ ] `EnsurePlatformBorn`: `check` runs `kubectl get configmap argus-platform-info -n <ns> -o jsonpath={.data.born}` through the runner and skips when it returns a value; `apply` creates the ConfigMap with `born` = now (UTC, RFC 3339). It must never patch an existing one. Tests: existing value is preserved; missing creates; the created manifest is valid.
- [ ] `render_platform_rules`: a PrometheusRule `argus-platform` in the platform namespace with alert `ArgusRetentionWindowShort`. Expression, for a metrics promise of N days: `absent_over_time(up[1h] offset <N-1>d) and on() (time() > <born_epoch + N*86400>)`; `for: 1h`; severity `warning`; annotations explain in one sentence what it means and what to check (compactor retention setting, object storage capacity). Test the expression string for `30d` and a `born_epoch`, and that a `90d` override yields `offset 89d`.
- [ ] `cmd_platform` applies it after the born step (it needs `born`): read `born` back through the runner. Follow the path existing alert rules take to reach the Mimir ruler (read `rules-sync` and how per-app `PrometheusRule`s are consumed); if platform rules need `rules-sync` to be evaluated, make `platform` say so in its final output and document it in `README.md`.
- [ ] Commit `feat(platform): the retention promise is enforced in Mimir and alarmed when the window is short`.

---

### Task 8: A published, pinnable image

**Files:** Create `Dockerfile`, `.dockerignore`, `.github/workflows/image.yml`; Modify `README.md`; Test: a unit test that guards the layout assumption.

- [ ] **The layout law.** `cli.py` computes `REPO_ROOT = Path(__file__).resolve().parents[2]` and reads `profiles/`, `integrations/`, and `helm/pins.yaml` from it. The image must preserve the repo layout and must NOT `pip install` the package into site-packages. Add `tests/unit/test_layout.py` asserting `REPO_ROOT / "profiles" / "baseline.yaml"`, `REPO_ROOT / "integrations"`, and `REPO_ROOT / "helm" / "pins.yaml"` exist, with a docstring saying the container depends on it.
- [ ] **Dockerfile:**
```dockerfile
FROM python:3.14-slim
# The upgrade pass pulls base packages to current security revisions even when
# the builder's cached base layer is stale; CI also builds with --pull.
RUN apt-get update && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG KUBECTL_VERSION=v1.33.4
RUN curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl" \
    && curl -fsSL -o /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod +x /usr/local/bin/kubectl && rm /tmp/kubectl.sha256
WORKDIR /app
COPY agent/pyproject.toml agent/uv.lock* ./agent/
RUN pip install --no-cache-dir "pydantic==2.13.4" "pyyaml==6.0.3"
COPY agent ./agent
COPY profiles ./profiles
COPY integrations ./integrations
COPY helm/pins.yaml ./helm/pins.yaml
RUN groupadd --system --gid 10001 argus \
    && useradd --system --uid 10001 --gid argus --home-dir /tmp --shell /usr/sbin/nologin argus
USER 10001
ENV PYTHONPATH=/app/agent PYTHONUNBUFFERED=1 HOME=/tmp
ENTRYPOINT ["python", "-m", "argus.cli"]
CMD ["observe"]
```
  Verify before committing: where `pyproject.toml` actually lives (repo root or `agent/`) and that the two pinned dependency versions equal the project's pins (read them from `pyproject.toml`; the Dockerfile must not drift: add a unit test that parses the Dockerfile's `pip install` line and compares it with `pyproject.toml`'s dependencies). Verify `python -m argus.cli` works (if the module lacks a `__main__` guard path, add `agent/argus/__main__.py` calling `main()` and use `python -m argus`). If Docker is available locally, build for `linux/amd64` and run `docker run --rm <img> --help`; report the result, or that Docker was unavailable.
- [ ] `.dockerignore`: `.git`, `**/.venv`, `**/__pycache__`, `docs`, `tests`, `terraform`, `distro`, `.argus`.
- [ ] **Workflow `.github/workflows/image.yml`:** on push to `main` and `workflow_dispatch`; job `build-push` guarded by `if: vars.ARGUS_IMAGE != ''`; `permissions: {contents: read, id-token: write}`; steps: checkout; compute `tag=${GITHUB_SHA::7}` and `host=${ARGUS_IMAGE%%/*}`; `google-github-actions/auth@v2` with `workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}`, `service_account: ${{ vars.GCP_BUILD_SA }}`, `token_format: access_token`; `docker/login-action@v3` (username `oauth2accesstoken`); `docker/setup-buildx-action@v3`; `docker/build-push-action@v6` with `pull: true`, `push: true`, `platforms: linux/amd64`, `provenance: false`, tag `${{ vars.ARGUS_IMAGE }}:<tag>`, and `id: build`; a final step writing to `$GITHUB_STEP_SUMMARY` the full pinned reference `${{ vars.ARGUS_IMAGE }}@${{ steps.build.outputs.digest }}` with one sentence: this digest is what a provisioner's config pins. The image job must not run unless the repo's existing CI gates pass: use `workflow_run` on the `ci` workflow's success for `main`, or fold the job into `ci.yml` with `needs:` on the existing gate jobs (read `ci.yml` and pick the simpler; say which).
- [ ] Validate the YAML parses (`python -c "import yaml; yaml.safe_load(open(...))"`). No registry, project, or organization names anywhere in the committed files.
- [ ] Commit the Dockerfile and tests first, then the workflow file ALONE in its own commit (`ci: publish a digest-pinnable argus image`).

---

## Self-review notes

Spec coverage: platform-owned tier (T3), dashboards step (T5), job mode + fingerprint + summary + throwaway ledger (T1), annotation (T2), endpoints (T4), identity (T6), retention enforced and alarmed (T7), image and publish (T8). The README Job example (T6) is the provisioner-facing contract. Out of scope items in the spec have no task, by design.
