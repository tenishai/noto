# Changelog

All notable changes to Argus are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/) (see [docs/versioning.md](docs/versioning.md)).

## [Unreleased]

### Removed

- **docs/target-fleet.md.** It described one real production estate, the first
  deployment's instance parameters. The design keeps its scale posture (size each
  deployment from its own measurements); the estate itself is not part of the project.

### Changed

- Author contact in `pyproject.toml`, the design doc and the sizing-agent proposal patch
  is the maintainer's GitHub noreply address.

### Fixed

- **helm targets the same cluster as kubectl.** Every kubectl argv honored
  `--kube-context`; the two helm calls (release probe and `upgrade --install`) did not,
  so they used whatever context the kubeconfig had current. Found on the first
  `platform --kube-context <dev> --dry-run` after the born-observed merge: kubectl
  spoke to dev, helm reached for prod, and only an unreachable network stopped it.
  `StepContext.helm()` now builds every helm argv beside `kubectl()`, with the same
  context. Until this lands, run `platform` only with the intended cluster as your
  CURRENT kube context.

- **A ruler that rejects the platform's rules fails the run, and says so.** The rejection
  used to escape `argus platform` as an unhandled exception - a traceback at the end of a
  run whose cluster work all succeeded. It is now a `FAILED: ...` line on stderr and exit
  1, because a group that never reached the ruler is an alarm nobody armed and nothing
  else in the run mentions it. A ruler that cannot be REACHED is still just a note.

- **A platform record that cannot be read is no longer mistaken for one that is not
  there - in job mode.** An absent record is a fact a job can act on (fall back to the
  contract's cluster, and say so in the summary). A present one that returns Forbidden
  almost always means the job's identity was never rolled out, which is exactly when the
  fallback wires an app to a tenant nothing writes to; it now fails the job with
  `platform record unreadable: ...`. Operator mode keeps its warning-and-fall-back - an
  operator is standing there reading stderr - and `render` stays silent either way.

- **`born` is written once and can no longer be rewritten by a failed read.** The platform's
  birth instant is what gates `ArgusRetentionWindowShort`, and the write path used the same
  lenient reader as the read path: NotFound, Forbidden, an apiserver blip and a network error
  all collapsed to "no record", after which a full `data:` apply stamped `born` with now and
  muted the alert for another whole retention window. `born` is now written by a
  `kubectl create` that cannot overwrite (an `AlreadyExists` is success) and appears in no
  later write at all; `tenant` and `environment` reconcile through a separate merge patch.
  A pre-read that fails for any reason other than "absent" refuses to write, with
  `refusing to rewrite the platform's birth instant: could not read the record: <one line>`.
  The read path (`observe`, `rules-sync`, `render`, every dry run) still treats an unreadable
  record as a missing one, so a preview never needs a cluster.

- **`argus render` previews what the commands actually do.** `render --platform` now shows the
  platform's own record and its own alert rules alongside the collector tier and the job
  identity - all four families come from one renderer that `argus platform` and the goldens
  also read, instead of three hand-derivations of the same thing. `argus render` (per-app)
  resolves its tenant from the platform's record the way `observe` does, silently: it neither
  warns about a missing record nor fails on a disagreeing `--cluster`, because it applies
  nothing. `render_platform_rules` refuses a missing `metrics` promise with a sentence rather
  than a bare `KeyError`.

- **The collector tier is platform-owned; observing one app can no longer reconfigure the
  gateway for all.** The shared tier (PriorityClass, target-allocator RBAC, agent DaemonSet,
  gateway, target allocator) used to be re-rendered from the profile of whichever single app
  was being observed and then server-side applied: last writer wins, so observing an app whose
  class has no traces signal deleted the traces pipeline out from under every other app in the
  cluster. `argus platform` now renders the tier from a platform profile (the baseline, all
  three signals, no class deltas) and applies it after the helm installs, and `argus observe`
  applies per-app families only (scrape, alerts, SLOs). `argus platform` gains a required
  `--env` (the gateway stamps `environment` on everything it forwards) and `argus render`
  gains `--platform`. Consequence, stated plainly: per-class and per-app shaping rules no
  longer reach the shared gateway - the baseline rules apply to everyone, and per-app shaping
  in the shared gateway is a registered follow-up.

  **Upgrading an existing cluster, in order.** Every step depends on the one before it, and
  the first is the one that cannot be undone:

  1. **Decide `retention.*` in the profile first - Loki's deletion is irreversible.** Until
     now Loki's compactor kept every log line it had, because nothing set a retention period
     and nothing enabled the deletion loop. The first `argus platform` run after this merges
     sets both from the profile's promise (`retention.logs`, `7d` by default), and on its next
     compaction loop the compactor begins permanently deleting log data older than that
     window. That is the intended behavior - a promise nobody enforces is a lie - but logs
     deleted this way are gone. Override `retention.logs` (and `retention.metrics` /
     `retention.traces`, which take effect in Mimir and Tempo the same way) to whatever the
     cluster is actually meant to keep, BEFORE the run rather than after.
  2. **Run `argus platform --cluster <name> --env <env>`.** One run does all of it: it creates
     the `argus-observe` identity a provisioner's Job needs, writes the platform's own record
     (`born`, `tenant`, `environment`), and re-applies the collector tier with all three
     pipelines - `observe` no longer applies that tier, so an existing cluster keeps its old
     gateway, missing traces pipeline included, until `platform` re-applies it. The apply
     itself is clean: same field manager, same object names, a superset manifest.
  3. **Verify the record says what you expect** before anything reads it:
     `kubectl -n argus get configmap argus-platform-info -o jsonpath={.data.tenant}` must
     print the tenant the gateway writes with. This is the value every app's dashboards,
     rules and cardinality queries will read with from now on.
  4. **Only then point a provisioner at the image.** A job launched before step 2 finds no
     identity (it cannot read the record) and no record to resolve its tenant from; one
     launched before step 3 may wire apps to a tenant nobody checked.

### Added

- **The ruler knows where Alertmanager is.** Mimir's ruler had no `alertmanager_url`, so it
  evaluated every rule and then dropped whatever fired: an alarm that reached nothing.
  `solo_endpoints` gains `alertmanager_url` (the operator's own `alertmanager-operated`
  Service, whose name does not depend on a helm release name the chart truncates), and
  `argus platform` sets it on the mimir release through the same `--set` passthrough the
  cardinality budget and the retention promises already use.

- **`argus observe` pushes the app's rules to the ruler, and prunes what it no longer
  renders (closes #57).** A `PrometheusRule` is inert in this stack, so an app provisioned
  by a job had alert rules nothing evaluated until somebody remembered to run `rules-sync`
  by hand. A new `sync-rules` step runs after the alert and SLO families are applied and
  before the dashboards: it pushes every rendered group under a ruler namespace named after
  the app, as the tenant the platform recorded, and then deletes any group the ruler still
  holds in THAT namespace which this run did not render - which is what left a renamed SLO's
  old group evaluating forever. Push first, prune second, always: a failed push never
  deletes the groups that were working, and no run can reach another app's namespace or the
  platform's own. An unreachable ruler is a note in operator mode (a laptop cannot route to
  the in-cluster URL; `observe` now takes `--mimir-url` for that) and a failed run in job
  mode, where the job runs inside the cluster and nobody reads a note. `argus rules-sync`
  stays as the way to re-push without re-observing, and prunes the same way, so the command
  and the step cannot leave the ruler in two different states. Because both now delete,
  both refuse to act on `argus-platform` (a legal app name, whose run would prune the
  platform's own retention alarm), a `--dry-run` of either says what it would push and
  prune without opening a socket, and every ruler failure - refused, or unreachable with
  the port-forward way out of it - is reported as a `FAILED:` line and exit 1 rather than a
  traceback.

- **Every per-app alert says whose it is.** Rules carried one label, `severity`, so nothing
  could route an alert to the app that owns it. Every rule in the app's own group
  (`argus-app-<app>`) now carries `app`, `namespace`, `class` and `environment` beside it (a
  rule's own label still wins, so a pack can say something different on purpose; recording
  rules are untouched), and each SLO in the Sloth spec carries `app`, `namespace` and
  `class` - Sloth puts those on the recording rules it generates, and its burn-rate alerts
  aggregate those series `without (sloth_window)`, so a firing SLO alert carries them too.
  The two cluster-wide groups every app renders (`argus-collector-health`,
  `argus-cardinality`) get one label and it is not the app's: `app: argus-platform`, the
  same label the platform's own retention alert carries. Their labelsets stay identical
  across apps, which is what lets Alertmanager collapse one collector fault into one
  notification instead of one per app. Routing on those labels is the next piece and is not
  in this change.

- **`argus observe` runs as a one-shot Job, and the launcher reads one line.** A
  provisioner (gojira) launches `observe` in the cluster instead of running the CLI:
  the app's contract arrives in `GOJIRA_CONTRACT_YAML` with a `GOJIRA_CONTRACT_SHA256`
  the job re-computes and verifies before anything is applied (same canonicalization on
  both sides: parse the YAML, sorted keys, compact separators, sha256), `ARGUS_ENV`
  names the environment and `ARGUS_NAMESPACE` the platform's namespace. The contract's
  own strings are validated where they enter - `app` and each environment's `namespace`
  must be Kubernetes names, `cluster` a cluster name - because downstream they become
  object names, `-n` arguments and a tenant header. The ledger lives in a temp dir, by
  design: a job with no ledger re-checks the real world every run.

  **The contract with the launcher:** exit 0 means observed, exit 1 means failed, and
  the LAST line on stdout is always `ARGUS_SUMMARY <one line>` - nothing else is ever
  written to stdout, so progress goes to stderr and the launcher never parses a log.
  `ARGUS_SUMMARY observed <app>/<env>: <n> steps applied, <m> skipped, contract <sha12>`
  on success (the counts are STEPS, not objects: engine steps run and skipped), and
  `ARGUS_SUMMARY observe failed: <step>: <reason>` on every failure, argument parsing
  and an unreadable platform record included. A success can carry one note today,
  `(no platform record: tenant from contract)`: the run is real, but the tenant it wired
  the app to read with was never confirmed against the one the gateway writes with.

- **Every object `observe` applies in job mode carries the contract it observed.**
  `argus.dev/contract-sha256: <sha>` is stamped on each applied document, so the cluster
  itself can answer "has the app changed since argus looked" without asking argus.
  Operator mode sets no annotation at all - there is no job contract to record. Perses
  documents carry none either, in either mode: they are HTTP API objects, not cluster
  objects.

- **The job's runtime is bounded.** Every kubectl argv argus builds carries
  `--request-timeout`, so an apiserver that accepts a connection and never answers can no
  longer hang a Job that has to end with a summary line (or a `render` on a laptop whose
  kubeconfig points at a cluster that is gone). The polling step passes its own remaining
  deadline instead, because that flag bounds the watch underneath `kubectl rollout
  status` too. The README's Job manifest adds `activeDeadlineSeconds: 900` as the outer
  bound on the summary line, plus a read-only root filesystem with the `/tmp` volume the
  job actually writes to.

- **Dashboards are an engine step.** Perses resources are provisioned by `ProvisionPerses`
  through the Perses HTTP API (POST, falling back to PUT on 409, skipping documents whose
  `spec` already matches), in the order the smoke script proved by hand: project, then
  datasources, then dashboards. It runs LAST in `observe`, because a dashboard charts the
  alerts and SLOs applied before it. `tests/smoke/provision_perses.py` retires. A dry run
  opens no socket at all and says what it would upsert.

- **In-cluster endpoints by default.** `--mimir-url` (`rules-sync`, `cardinality`,
  `feedback`) and `--perses-url` (`observe`) now derive from `--namespace` (env
  `ARGUS_NAMESPACE`) rather than defaulting to `localhost`: inside a Job there is no
  port-forward. An explicit flag still wins, and the port-forward form is documented for
  an operator's laptop. `feedback` also resolves its tenant from the platform's record
  now, like every other reader.

- **An image, published by digest.** The `Dockerfile` at the repo root builds the image
  the Job runs: the repo layout copied verbatim (`REPO_ROOT` is derived from the source
  file's own location, so nothing is pip-installed into site-packages), runtime
  dependencies at exactly the versions `pyproject.toml` pins, and two hash-verified
  binaries - `kubectl`, and the pinned `sloth` the SLO step shells out to. It runs as a
  non-root numeric user. It is scoped to `observe` and `render`: `helm/values/` is not in
  it, so `argus platform` is still an operator's run from a checkout. The base tag floats
  on purpose (the `apt-get -y upgrade` exists to pick up what the base has fixed); the
  published OUTPUT is what gets pinned, by digest, in a provisioner's Job config.

  **Prerequisite, and how it fails:** publishing needs the image reference and the
  build identity configured as repository or organization variables (`ARGUS_IMAGE`, plus
  the workload-identity provider and build service account). Until `ARGUS_IMAGE` exists,
  the publish job's `if` is false and the whole job is SKIPPED - which GitHub reports as
  a green check on the run. A green CI is therefore not evidence that an image was
  published; the run's job list is. No registry, project or account name is committed to
  this repo, by rule.

- **The platform arms its own alarm: `argus platform` pushes its rule group to Mimir's
  ruler.** A `PrometheusRule` is inert in this stack, so applying `argus-platform` and
  stopping there left `ArgusRetentionWindowShort` unevaluated - an alarm nobody armed.
  A successful `argus platform` run now pushes that group as the tenant it just recorded,
  and `argus platform` gains `--mimir-url` (derived from `--namespace` unless given, the
  same helper the other commands use). The push is tolerant in exactly one direction: a
  ruler that cannot be REACHED (the ordinary case from an operator's laptop) is one stderr
  note naming the URL and the port-forward form, and the run still succeeds; a ruler that
  answers and rejects the rules fails the run. A dry run pushes nothing and says what it
  would have pushed. `argus rules-sync` now resolves its tenant from the platform's record
  the same way `observe` does, so an app's groups reach the tenant the gateway writes with.

- **The retention promise is enforced in the backends and alarmed when the window is
  short.** `profiles/baseline.yaml`'s `budgets.retention` (`metrics: 30d, logs: 7d,
  traces: 7d`) reached nothing; it now becomes each backend's own retention setting at
  apply time, the same way the cardinality budget already did: Mimir
  (`mimir.structuredConfig.limits.compactor_blocks_retention_period`), Loki
  (`loki.limits_config.retention_period` plus `loki.compactor.retention_enabled`, which is
  what actually makes it delete), Tempo (`tempo.retention`, the chart's own key for
  `compactor.compaction.block_retention`, converted to hours because a Go duration has no
  day unit). `helm/values/loki.yaml` gains `compactor.delete_request_store`, without which
  Loki refuses to start once retention is enabled. Every key was verified against the
  PINNED chart and then against the backend's own binary, not from memory. `argus platform`
  also applies a platform-level `PrometheusRule` (`argus-platform`) whose
  `ArgusRetentionWindowShort` fires when nothing was stored at the far edge of the promised
  window, gated on the platform being older than the promise so a new cluster does not
  alarm for its first month. The birth instant comes from a new `argus-platform-info`
  ConfigMap, written by `argus platform`, which also records the `tenant` the gateway
  writes with and the `environment` it stamps.
- **The platform records its tenant, and `observe` reads it: dashboards can no longer read
  an empty tenant.** The gateway WRITES with the tenant `argus platform --cluster` was
  given, while each app's dashboards, rules and cardinality queries READ with the tenant
  `observe` resolved; when those differed, every panel was empty and nothing errored.
  `observe` now resolves its tenant from the `argus-platform-info` record: an explicit
  `--cluster`/`ARGUS_CLUSTER` that DISAGREES with the record fails the run before anything
  is applied; with no override, the recorded tenant wins over a disagreeing contract with
  one stderr line; with no record at all, the contract's cluster is used and a warning says
  to re-run `argus platform`. `ARGUS_CLUSTER` is no longer a bridge around a disagreeing
  contract - it is an explicit override that must match.
- **`argus platform` renders and applies `argus-observe`'s identity** - the ServiceAccount
  and RBAC a provisioner's Job runs `argus observe` as (born-observed, task 6): one
  ClusterRole (`get`/`list`/`create`/`patch`/`update` on `servicemonitors`/`podmonitors`/
  `prometheusrules`, `monitoring.coreos.com`, cluster-wide because app namespaces are
  created dynamically) plus a namespaced Role granting `get` on the platform's own
  `argus-platform-info` ConfigMap by name only - nothing else, no secrets, no wildcard, no
  delete. Applied as a new `identity` family, right after `collectors`. README documents the
  Job spec a provisioner should create, hardened (`runAsNonRoot`, `RuntimeDefault` seccomp,
  no privilege escalation, all capabilities dropped, `automountServiceAccountToken` explicit
  because the job needs the token), and both README and the module state the trust scope
  plainly: this identity is trusted platform-wide across every app namespace, not sandboxed
  per tenant, because app namespaces are created dynamically.
- **Profiles for gojira's `agent` and `gateway` workload classes**: agent -> runtime health
  (kubeletstats memory + kube-state-metrics restarts + logs; alert-based like cron - agents
  expose no /metrics, and an objective over metrics the app does not emit is an SLO that can
  never fire), gateway -> edge RED (web's panels plus restarts; single-replica by class
  contract; SLOs join when the gateway exposes http metrics). `metric:pod-restarts` gains a
  real KSM query (namespace-scoped - gojira gives every app its own namespace); the
  cluster-level baseline context still renders it empty for want of a namespace, unchanged
  from its placeholder days.
- **`app_scrape: false`** (class profile knob, default true): both new classes set it - their
  workload contract serves /healthz, not /metrics - which suppresses the per-app
  ServiceMonitor/PodMonitor *and* the ArgusScrapeDown alert group, applying the
  can-never-fire principle to scrapes and alerts alike. Flip it (or grow a /metrics
  endpoint) and the whole scrape lane returns. Live-checked against a real agent-class
  gojira.yaml: rendered families are alerts/perses only.

- **Exemplar click-through: verified, answered, and permanently asserted** (issue #47, the
  oldest open thread - carried from the Perses spike): a pinned OTel Python SDK sample app
  (tests/smoke/exemplar-app.yaml) proves exemplars flow SDK -> agent -> gateway -> Mimir
  (`max_global_exemplars_per_user` now in the pinned values) and out through the Perses
  datasource proxies with resolvable trace ids; the smoke DoD gains an 8th assertion so the
  path stays live-proven. Perses 0.53.1's UI cannot render exemplars (zero exemplar code in
  all 83 frontend assets, Playwright-verified; screenshot in the spike addendum), so
  co-located correlated panels remain the documented answer, with a named revisit trigger.
  The backend is exemplar-ready the day Perses ships the UI.

- **The Phase 2 skills, live-proven** (issue #45): `argus-slo`, `argus-dashboard`,
  `argus-cardinality` join the Phase 1 three, each with eval prompts in `evals/evals.json`
  (the Phase 1 eval definitions, deferred from #23, land too). 18 eval runs on a live kind
  cluster: with/without judgment pairs (the skills flip dangerous-by-default outcomes:
  silent budget raises, SLOs that can never fire) and six live runs (platform 4/4,
  observe 4/4, pipeline 4/4, slo 7/7, dashboard 5/5, cardinality 6/6). Evidence on the issue.
- **web-red gains a memory panel** with the live-verified kubeletstats query (the eval caught
  the cAdvisor name returning nothing on the OTLP-only Mimir); `traces:slow-requests` now
  filters by the class latency SLO threshold (a panel's title is a promise about what it
  shows); baseline drops platform internals (`alertmanager_.+`, `system[._].+` - hostmetrics
  duplicating node-exporter) for ~13% live-measured headroom with no budget change.
- Eval-program fallout: #56 (HelmInstall status check, merged), #57 (rules-sync is
  upsert-only, filed), a mis-applied golden-fixture gateway CR on the smoke cluster routing
  ingest to the wrong tenant (fixed live).

### Fixed

- **HelmInstall no longer trusts a fingerprint on a non-deployed release**: helm stamps
  `--description` (the drift fingerprint) even when `--wait` times out, so a failed or
  `pending-*` release could ledger-skip on resume and surface later as a confusing `validate`
  failure. `check()` now requires `info.status == "deployed"` before honoring the fingerprint.
  Found during the issue #45 live skill evals (the argus-platform resume eval traced this exact
  scenario); regression-tested.

- **HelmInstall detects values/pin drift** (issue #46 - the gap that cost two manual
  `helm upgrade` interventions during live work): a fingerprint of (chart version + values file
  content + extra_sets) is stored in the helm release description and re-verified every run
  (the step is now ephemeral - ledger DONE never masks drift). Matching fingerprint skips;
  missing or stale fingerprint upgrades. Lives in helm's own state, so it survives ledger loss.
  Live-proven: first platform run stamped all nine releases, second run skipped all nine.

### Added

- **argus-integrations: declarative autodiscovery** (issue #43): `integrations/<name>/integration.yaml`
  (match labels/port + receiver template, pydantic-validated, duplicate names rejected) renders
  into `receiver_creator` + `k8s_observer` on the agent. Two seed integrations ship (redis, nginx).
  Rules target "port" endpoints - live-verified: pod.container endpoints carry no port and the
  sub-receiver fails ("missing port in address"). Distro gains receivercreator, k8sobserver,
  redisreceiver, nginxreceiver (all v0.154.0). Live acceptance in the smoke suite: a redis pod
  with only the `argus.dev/integration: redis` label lights up with zero config -
  `redis_clients_connected` queryable in Mimir with full k8s labels.
- **Cardinality budgets, enforced soft** (issue #44): the profile budget becomes Mimir's
  `max_global_series_per_user` at platform time (`argus platform --env`); budget-burn alerts
  (80% ticket / exceeded page) join the alert pack; `argus cardinality --tenant` prints top metric
  names by series count (Mimir cardinality API; `cardinality_analysis_enabled` in values).
- **The ruler, and rules that actually evaluate** (design gap found during #44): nothing evaluated
  our PrometheusRules (no Prometheus runs; Mimir's ruler was disabled). Mimir ruler enabled;
  `argus rules-sync` pushes every rendered group (alert packs + Sloth SLO output) to the ruler API
  per tenant. Live-verified: 9 groups synced and evaluating, asserted in the smoke DoD.

### Changed

- **`argus feedback` is diagnostic-only; gojira-sizing-agent is the single evidence producer**
  (issue #52, Option A - decided on live evidence: the agent ran unmodified on an Argus solo
  cluster, reported first try, and both producers' measurements converged, 4m/40Mi vs 4m/38Mi).
  The POST path and `--gojira-url` are gone (`gojira_client.py` deleted); running without
  `--dry-run` now exits 2 with a pointer, before touching Mimir. The measuring half stays as
  the `--dry-run` diagnostic, and the cross-repo `usage_profile.json` fixture stays asserted
  byte-identical (the three-repo contract). The missing pieces on the agent's side (an
  `X-Scope-OrgID` header option + the kubeletstats query preset) are proposed upstream as a
  verified git-am patch: [docs/proposals/](docs/proposals/gojira-sizing-agent-prometheus-headers.md).
- **Feedback-loop queries corrected to live-verified names** (against a running smoke cluster):
  Mimir's OTLP path stores kubeletstats metrics WITHOUT unit/_total suffixes - the real names are
  `k8s_pod_cpu_time` / `k8s_pod_memory_working_set` (pod-level, containers pre-summed). Mimir's
  `promote_otel_resource_attributes` limit is a comma-separated string, not a YAML list
  (fixed in helm values; the list form crashed every Mimir component at config parse). The smoke
  sample app now declares requests/limits (gojira-shaped apps always do; KSM had no series to
  report otherwise).
- **The loop verified end to end, live**: a local gojira (its own venv + seeded dev DB, repo
  untouched) accepted Argus's evidence (`usage profile id 1`) and immediately computed an open
  sizing recommendation (cpu 1m->50m, memory 1Mi->64Mi) with a justification quoting our
  percentiles. Deploy -> observe -> distill -> recommend, across both repos.

### Added

- **The gojira rightsizing feedback loop** (Phase 2, issue #42): `argus feedback` distills an
  app's usage from Mimir (per-pod cpu/memory percentiles via kubeletstats, declared
  requests/limits + restarts/OOM via kube-state-metrics) into gojira's `UsageProfile` and POSTs it
  to `/api/v1/usage` (Bearer token from `GOJIRA_USAGE_TOKEN`, never a flag). The wire contract is
  locked by gojira's own cross-repo golden fixture, asserted byte-identical in both repos exactly
  as gojira's docstring prescribes. `--dry-run` prints the evidence and posts nothing.
- **Agent now collects host + pod usage telemetry**: kubeletstats + hostmetrics receivers wired
  into the metrics pipeline (a design-stated duty the smoke run revealed missing), with
  nodes/stats+proxy RBAC rendered, and Mimir values gain `promote_otel_resource_attributes` so
  k8s labels are queryable on the OTLP path. Live verification of the kubeletstats-as-stored
  names rides the next `make smoke` run (stated, not assumed).

## [0.1.0] - 2026-07-11

### Added

- **Phase 1 definition of done: MET** (issue #24). The kind smoke test
  (`make smoke` / tests/smoke/) takes an empty cluster through `argus platform` (step engine, 9
  pinned components) and `argus observe` on a real gojira-shaped app (podinfo, traces opted in via
  the `observability:` block - exercising the override path), then asserts through the Perses
  proxies: default dashboard present, Sloth-generated SLO rules applied, a real metric, real logs,
  and a real trace from the app. All five assertions pass; dashboard screenshot committed
  (tests/smoke/dod-dashboard.png). Gated smoke workflow added (workflow_dispatch + release).

### Fixed (smoke-found, each now encoded in renderers/values)

- Agent collector needs `KUBE_NODE_NAME` via the downward API (k8sattributes node filter).
- Solo gateway file_storage queue needs a writable emptyDir (non-root, read-only rootfs).
- TargetAllocator: rendered ServiceAccount + ClusterRole/Binding (operator does not create them for
  a user-supplied SA), explicit select-all ServiceMonitor/PodMonitor selectors (defaults select
  nothing), and secrets read for its prometheus-CR watcher.
- k8sattributes RBAC ClusterRole bound to the operator-created collector SAs (the enablement
  guide's "RBAC required", made concrete).
- Mimir: query-scheduler cannot be disabled (frontend/querier still dial it; queries hang) - solo
  values keep one replica. Perses: plugins extract to the writable data volume
  (readOnlyRootFilesystem blocks /etc/perses/plugins - the spike gotcha, chart edition).
- Default dashboard and SLO queries now use Prometheus-client conventions
  (`http_requests_total{status=...}`, `http_request_duration_seconds_*`) - what gojira-contract
  apps actually expose; the OTel-semconv names were verified wrong against a live app.
- podinfo-class apps: OTLP gRPC goes to :4317 (Go SDK default protocol).

### Added

- **The three Phase 1 skills + the CLI fallback** (issue #23): `agent/skills/argus-platform`,
  `argus-observe-app` (including the GMP-supersession PR flow), and `argus-pipeline`
  (profile-owned drop-rule changes only) in the Anthropic skill format, with eval prompts staged
  in references/ (with/without runs land with the #24 smoke cluster). The `argus` CLI
  (`render` / `platform` / `observe`) is the daedalus-fallback surface: every flow runs through
  the step engine + ledger with concrete steps (EnsureNamespace, HelmInstall from pins.yaml,
  server-side ApplyRendered, ValidateRoundTrip), exercised end to end against a fake runner.

### Added

- **Helm pins + values** (issue #22): [helm/pins.yaml](helm/pins.yaml) - the machine-readable
  manifest (repo/chart/exact version/values/topology) the step engine reads - plus per-component
  values encoding the spike gotchas (Perses plugin mount, Loki OTLP structured metadata + index
  labels, Tempo metrics-generator ownership, k-p-s reduced to operator CRDs + Alertmanager).
  Every pin re-verified against the live chart repos on 2026-07-11 (several moved since the June
  research: Loki chart 18.4.4/app 3.7.3, cert-manager 1.21.0, OTel operator chart 0.119.0 landing
  exactly on the 0.154.0 train, Perses chart 0.22.0 carrying exactly the spike-verified 0.53.1);
  Redpanda 26.1.8 and Altinity clickhouse-operator 0.27.1 introduced for the regional topology.
  New `make validate-helm` + CI job templates every pinned chart with our values - it caught the
  Loki bucketNames template requirement immediately. versions.md verify-before-pinning checklist
  resolved; crds/README rewritten to reflect renderer-owned CRDs.

### Added

- **OpenTofu cloud seams, GCP implementations** (issue #21): provider-namespaced modules
  `terraform/modules/object-storage/gcp` (one lifecycle-managed GCS bucket per backend block
  store; retention days are inputs per the scale posture) and `terraform/modules/workload-identity/gcp`
  (one GSA per backend, objectAdmin on its own bucket only, Workload Identity bound to the argus
  KSAs), plus a validated example wiring and a committed provider lockfile (linux_amd64 +
  darwin_arm64). The logging-bypass seam is deliberately NOT a module: it mutates the
  gojira-owned cluster resource, so it lands as an idempotent engine CLI step (README records the
  reasoning). CI gains a path-relevant opentofu fmt + validate job (setup-opentofu, tofu 1.12.3).
  AWS/Azure implementations remain explicitly out of scope (multi-cloud goal, GCP first).

### Added

- **The Argus collector distribution** (issue #20): [distro/manifest.yaml](distro/manifest.yaml) -
  core + the exact contrib components the renderers emit, all pinned to the v0.154.0 train with
  gomod lines sourced from the official otelcol-contrib manifest at the same tag. Built locally
  with pinned ocb 0.154.0 and validated: all 7 rendered collector configs pass
  `argus-otelcol validate` against our own binary. New CI workflow (path-filtered on distro/ and
  the renderers) rebuilds and re-validates on change. Go toolchain pinned 1.26.5 (0.154's generics
  hit linker bugs on older Go - observed on 1.23/1.25.0). Span-metrics ownership decided:
  Tempo metrics-generator owns (spike-verified); the spanmetrics connector ships unwired as the
  fallback. resourceprocessor added to the component set (the renderers emit resource/default;
  the issue's original list missed it - caught by validating against our own binary).

### Added

- **gojira-reader** (issue #19): parses the real gojira.yaml contract (app, class, per-environment
  cluster/namespace bindings, optional schedule and overrides) with fixtures copied from gojira's
  own golden tests so the two repos cannot drift silently. Required-field failures are loud
  (file + field named); unknown fields warn and are retained (gojira owns the contract). The
  proposed `observability:` block is read when present and feeds the profile layer's
  `load_override` verbatim. Includes the `ClusterInfo` registry-row shape.

### Added

- **Step engine + SQLite ledger** (issue #18): the idempotent, resumable
  `check() -> apply() -> record` engine ported from gojira's proven provisioner shape, scope-aware
  via `target_ref` (region work shared across clusters, cluster/app work independent). Stdlib
  sqlite3 with WAL; `provision_steps` (one row per step per target_ref) + append-only `events`;
  ephemeral steps re-check every run; failures halt, record, and resume exactly where they
  stopped. Deterministic tests via injectable clock.

### Changed

- **Testing made sharper, not bigger** (test-value review): new layer 3.5 `make validate` runs
  rendered artifacts through the REAL consumers' validators - `otelcol-contrib validate`,
  `sloth generate`, `kubeconform` (CRDs-catalog schemas), `percli lint` - all pinned, cached, and
  wired into CI. On its first run it caught four real bugs the self-referential suite passed over
  (invalid kafka `encoding` key, missing TargetAllocator receiver wiring, wrong env-var syntax for
  `collector_id`, `file_storage` needing `create_directory`), all fixed here. Golden files shrunk
  from ten class-x-topology directories to the human-reviewable pair (web-solo, web-regional);
  all-class breadth stays with the contract invariants. Duplicated and trivial tests deleted
  (behaviors now assert in exactly one layer). testing.md gains the layer 3.5 description and a
  "test budget" section gating future accretion. New pin: kubeconform 0.8.0 (verified at
  introduction).

### Added

- **The core of Argus** (issue #17, per the approved spec and plan): layered observability
  profiles (`profiles/baseline.yaml` + five class profiles, pydantic-validated, merged by a pure
  resolver), the OTTL filter-rule compiler (structured `drop:` rules with a justified `raw_ottl`
  escape hatch; `metric_labels_strip` renders as a transform, not a filter), typed-dict renderers
  for every artifact family (collector CRDs for agent/gateway/ingest/TargetAllocator across solo
  and regional topologies, ServiceMonitor/PodMonitor, Sloth `PrometheusServiceLevel`, Perses
  project/datasources/dashboards with tenant headers, alert packs incl. the portable scrape-down
  replacement for gojira's GMP rule), and the contract-test layer that makes the opinions
  enforceable: 80 invariant assertions plus 79 golden files per class x topology, regenerated only
  via `make regen-golden` with the diff reviewed in PRs. New pinned runtime deps: pydantic 2.13.4,
  pyyaml 6.0.3 (verified at introduction). The layer-2/3 placeholder tests are replaced by the
  real suites (134 tests).

### Changed

- **Scale posture made explicit** (design.md decision row): Argus is designed for arbitrary scale -
  many teams and many companies, small and massive. Every component scales horizontally; scale
  numbers are inputs (profile budgets, sizing tiers, topic retention), never constants. One
  deployment's volume (target-fleet.md, now labeled as instance parameters) is not the design
  envelope, and "because our volume is small" justifications were corrected in the Fluent Bit,
  ClickHouse-vs-Loki, and warehouse-sizing records.
- **Fluent Bit upgraded from rejected to a log-shipper seam**: OTel filelog stays the default;
  Fluent Bit (tailing to OTLP into the same gateway, downstream untouched) is the supported
  per-deployment alternative for very high per-node log rates - chosen by benchmark on real node
  profiles, rendered from the same profile schema, contract-tested. Implementation post-Phase-1.
  What remains rejected: FB as a second always-on substrate with hand-authored config.

### Added

- **Design spec for profiles + renderers + contract tests** (issue #17), approved and committed at
  [docs/superpowers/specs/2026-07-11-profiles-renderers-design.md](docs/superpowers/specs/2026-07-11-profiles-renderers-design.md):
  layered composition (baseline + class + override), structured filter rules with a justified
  `raw_ottl` escape hatch, ephemeral renders with golden files as the only persisted output, typed
  models -> `yaml.dump` renderers (no Jinja2 for these artifacts), and the invariant list the
  contract tests enforce.

### Changed

- **SLO generator decided: Sloth** (with the #17 design) - pure spec-in/rules-out generator fits the
  renderer pipeline; Pyrra (operator + UI) recorded as the alternative. Closes the open question.
- **Fluent Bit and Telegraf recorded as rejected alternatives**: one telemetry substrate per node
  stands (OTel filelog has ample headroom at fleet volume; the bypassed GKE logging agent is itself
  Fluent Bit). Revisit trigger recorded: measured filelog underperformance, with
  Fluent-Bit-to-OTLP-gateway as the clean fallback. Telegraf overlaps existing scrape/OTLP paths.

- **Python scaffold** (issue #16, the first code PR): `pyproject.toml` (package `argus` under
  `agent/`, hatchling, CPython 3.14.6 via `.python-version`), uv-managed env with committed
  lockfile, ruff lint+format, pytest with the unit and contract layers wired, `make test` running
  layers 1-3 per docs/testing.md, and CI's unit+contract job activated via `astral-sh/setup-uv`.
  Pins verified at introduction and recorded: uv 0.11.28 (bumped from 0.11.21), ruff 0.15.20,
  pytest 9.1.1, setup-uv v8.

### Changed

- **Warehouse sink default is now self-hosted ClickHouse** (per region, fed the curated subset via
  the OTel ClickHouse exporter), replacing "BigQuery with per-cloud equivalents later." One OSS,
  cloud-portable implementation instead of three managed-warehouse builds; BigQuery/Athena/Synapse
  remain optional per-cloud alternatives behind the same seam. Perses ClickHouse datasource
  coverage was verified in the capability spike; agents get SQL for analytics.
  Postgres/CloudSQL/AlloyDB/TimescaleDB recorded as rejected for this role (row-store vs
  columnar-OLAP workload shape; CloudSQL/AlloyDB additionally vendor/GCP-only; Timescale License
  not OSS) - Postgres stays the family's transactional-state engine.
- **ClickHouse replacing Loki/Tempo reaffirmed as rejected**, now with recorded revisit triggers:
  Loki label-model outgrown, a Perses ClickHouse TraceQuery plugin appearing (today traces in
  ClickHouse would be invisible in Perses), or agents needing arbitrary SQL beyond the warehouse
  subset.

### Added

- **Perses capability spike: PASS** ([docs/spikes/perses/RESULTS.md](docs/spikes/perses/RESULTS.md),
  closes issue #11). Perses 0.53.1 verified hands-on against pinned Mimir/Loki/Tempo on kind: full
  Loki/Tempo/Prometheus plugin coverage, dashboards-as-code via API, all three query languages
  through the Perses proxy, tenancy headers, Tempo metrics-generator span-metrics into Mimir, UI
  render screenshot. Grafana fallback not invoked; exemplar click-through carried as a Phase 1
  content-work check. Reproduction manifests committed; operational gotchas recorded (config mount
  must not mask /etc/perses plugins; Loki OTLP structured-metadata config; native OTLP ingest paths).

- docs/target-fleet.md (removed 2026-10-05): the first concrete deployment target - 11 production
  GKE clusters, ~2,200 logs/sec fleet-wide, 1 week raw retention then aggregation, Go/Python. Adapted
  from a teammate's earlier fleet roadmap; preserves its Datadog parallel-run migration strategy
  (instrument once with OTel, dual-ship during the window, deprecate per signal per service), its
  correlation acceptance checklist (trace_id in logs, exemplars, metric -> trace -> log
  click-through, folded into the Phase 1/2 DoD and the Perses spike), and its remediation guardrails
  (risk-tiered actions, blast-radius limits, cooldowns, pause switch) as recorded requirements for
  the future observer agent - remediation itself stays out of Argus scope.

### Changed

- **Topology sizes named as a first-class concept**: `solo` (one cluster, queueless, file-backed
  gateway sending queue, single-node backends - the Phase 1 DoD and kind smoke-test shape) and
  `regional` (shared queue + ingest tier + backends - the production shape). Same rendered
  components, different placement.
- **Tenancy decided**: tenant per cluster, stamped as `X-Scope-OrgID` by the ingest tier (gateway in
  `solo`), giving isolation and per-team cost attribution via tenant + namespace labels.
- **GMP supersession mechanics specified**: deleting gojira's generated CRDs in-cluster is not
  enough (drift capture would PR them back); the `wire@<app>` step opens a PR against the app repo
  removing the GMP manifests, which makes Argus's `gh` write access to app repos an explicit Phase 1
  scope. Long-term fix remains the upstream gojira skip flag.
- **Observer-agent north star recorded** in design.md and ROADMAP Phase 4: one generic
  daedalus-bundled agent made team-specific by an Argus-rendered config file, fed by a queue
  consumer group; kept sharp against the history-reading forecaster. Market context noted
  (Chronosphere validates the cost-control thesis commercially; Argus is the open-source,
  profiles-in-git answer), and aggregate-then-discard-raw added to the ADR as the third cost lever.

- **OpenTofu replaces Terraform** (CLI pin 1.12.3, verified 2026-07-08): Terraform has been BUSL
  (not open source) since 1.6, violating the 100% OSS hard convention. Providers are MPL-2.0 and
  work unchanged; sources re-pointed at the OpenTofu registry. The `terraform/` directory name stays
  (it is just HCL).
- **SLOs are generated, never hand-rolled**: profiles render an SLO spec consumed by Sloth or Pyrra
  (evaluation tracked as an open question, leaning Sloth), which emit the multiwindow-multiburn
  `PrometheusRule` output. Burn-rate math is not something we own.
- **StatsD demoted to legacy-only** in the transport ADR: OTLP is the push path, Prometheus scrape
  the pull path; the statsd receiver remains in the distro as a "supported, never recommended"
  affordance for un-instrumented third-party workloads.
- **Queue entry criteria added** to the transport ADR: the solo topology ships queueless with a
  file-backed persistent gateway sending queue; the regional Redpanda queue lands with the second
  cluster in a region or the first queue consumer. NATS JetStream recorded as considered-and-set-aside.
- **VictoriaMetrics/VictoriaLogs recorded as a rejected alternative** for the storage tier (Mimir
  multi-tenancy is load-bearing; MetricsQL dialect drift; no traces story).
- **Perses capability spike tracked as blocking the Phase 1 DoD** (metrics + logs + traces verified
  hands-on against Mimir/Loki/Tempo), with Grafana OSS via the Grafana Operator as the named
  fallback now that AGPL is license-clean for us.

- **Relicensed Apache-2.0 -> AGPL-3.0-or-later**, moving with the tenishai family (gojira, techne,
  liminal, cabal, daedalus all relicensed 2026-07-08). Permits library-level embedding of the
  siblings without a license boundary. LICENSE, README, CLAUDE.md, design.md, and the skills-format
  convention updated.
- **Backend hosting revised: per-cluster collection, per-region storage**, with a durable
  Redpanda/Kafka transport and an OTel-collector ingest tier doing profile-owned filtering and
  routing-connector fan-out. New ADR: [docs/transport-and-routing.md](docs/transport-and-routing.md).
  Multi-cloud is the stated goal (GCP first) via four provider-interface seams: object storage,
  workload identity, warehouse sink, managed-logging bypass.
- **design.md now documents the real gojira contract** (verified against gojira's template,
  2026-07-08): `app`, `class`, an `environments` list each carrying its own cluster/namespace,
  optional `schedule` and `overrides`. The `observability:` block is re-labeled as a proposal to
  gojira, not an existing field. The trigger question is resolved to the standalone pass (gojira has
  no outbound hook today).
- **The Argus agent's shell is a daedalus bundle** (agent card + persona + wiring), keeping the step
  engine, renderers, profiles, and skills as Argus-owned tools, with a thin CLI fallback while
  daedalus is v0.1.
- Sibling links updated from personal repos to the [tenishai](https://github.com/tenishai) org;
  ecosystem tables gained daedalus and ladon with concrete integration paths (liminal entry-point
  handler, techne `init_session_for_external_id`, cabal registry PR, ladon team onboarding via ESO).
- Step 7 of the order of operations now **supersedes gojira's generated Google Managed Prometheus
  CRDs** (`PodMonitoring`/`Rules`) so Argus-managed clusters are not double-scraped; proposing a
  skip flag upstream is tracked as an open question.
- Phase 2 gains the **gojira rightsizing feedback loop**: Argus distills per-app p95/p99 usage from
  Mimir and POSTs it to gojira's `/api/v1/usage`, closing the deploy-observe-resize loop.
- Visualization is now **Perses**, not Grafana. Perses is CNCF, Apache-2.0, and dashboards-as-code
  (native CRDs), and covers metrics (Mimir), traces (Tempo), and logs (Loki) - satisfying the Phase-1
  trace requirement with no Grafana in the stack. Retires the "LGTM" label in favor of naming the
  substrate explicitly (Mimir/Loki/Tempo + Perses). See decision 7 in
  [docs/design.md](docs/design.md).

### Added

- README "Part of a larger platform" section naming the sibling projects (gojira, techne, liminal,
  cabal) and Argus's relationship to each.
- Top-level [ROADMAP.md](ROADMAP.md): the phased plan (Observe -> Curate -> Guard -> Reason -> Later)
  at a glance, linked from the README, with design.md as the source of truth.
- Implementation language locked to **Python** (CPython 3.14, `uv`, `ruff`) for consistency with the
  sibling repos and first-class Anthropic SDK support. See decision 8 in [docs/design.md](docs/design.md).
- Canonical pinned-versions reference at [docs/versions.md](docs/versions.md) capturing the latest
  stable version of every tool we pin, with verify-at-pin-time caveats.
- CLAUDE.md convention: any newly introduced dependency must be verified to its current latest stable
  version and recorded in docs/versions.md before pinning.
- Phase 0 foundations: contributor guide with branching strategy and commit conventions
  ([CONTRIBUTING.md](CONTRIBUTING.md)), versioning and release policy
  ([docs/versioning.md](docs/versioning.md)), testing philosophy and layers
  ([docs/testing.md](docs/testing.md)), a conventions lint, and a CI skeleton.
- Baseline repo scaffold: OTel-style README, project conventions in CLAUDE.md, Apache-2.0 license,
  design and source-capture docs, and the project-local web-design-guidelines skill.
