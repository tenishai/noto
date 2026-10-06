# Operating argus

What an operator needs beyond the README: the collector tier, the platform's own record,
retention, how rules reach the ruler, endpoints, running observe as a Job, and the image.
Moved here from the README verbatim on 2026-10-05.

## The collector tier

The collector tier (agent DaemonSet, gateway, target allocator) is part of the platform, not part of
wiring an app: `argus platform --cluster <name> --env <dev|qa|prod>` stands it up once, with all three
signals on, and `argus observe --gojira <path> --env <env>` only adds that app's scrape wiring,
dashboards, SLOs and alerts. One consequence, stated plainly: per-class and per-app shaping rules no
longer reach the shared gateway - the baseline rules apply to everyone (a `drop:` rule added, changed,
or disabled by an app's class or `observability:` override is a no-op; `observe`/`render` warn about it
on stderr, naming the ignored rules), and per-app shaping in the shared gateway is a registered
follow-up.

## The platform's own record

`argus platform` writes down what it is, in an `argus-platform-info` ConfigMap in the platform
namespace: `born` (its birth instant, written once and never overwritten), `tenant` (the cluster name
it was given, which is the tenant the gateway writes every metric, log and trace with) and
`environment` (what the gateway stamps on everything it forwards). Re-running `platform` updates
`tenant` and `environment` - they describe the platform as it is now, not as it was born.

**The write tenant and the read tenant can no longer drift apart.** The gateway WRITES with the
platform's tenant; an app's dashboards, rules and cardinality reports READ with whatever `observe`
resolved. When those differed, every panel was empty and nothing errored. `observe` now resolves its
tenant from the record, in this order:

1. an explicit `--cluster` (or `ARGUS_CLUSTER`), which **must agree** with the recorded tenant: a
   disagreement fails the run before anything is applied, naming both values;
2. otherwise the recorded tenant, which wins over a gojira.yaml cluster string that disagrees with it
   (one informational line on stderr);
3. otherwise (no record: a platform installed before this existed) the contract's own cluster string,
   with a warning to re-run `argus platform`.

Three commands consult the record: `observe` and `rules-sync` resolve their tenant from it by the
order above, and `render` previews the same resolution **silently** - it neither warns about a
missing record nor fails on a `--cluster` that disagrees with one, because it applies nothing. On
those read paths an unreadable record is simply a missing one, so `render` and `--dry-run` never
require a cluster.

The WRITE path is the opposite: `argus platform` refuses to write at all when it cannot read the
record and the reason is not "absent", because `born` can only be written once and a mistaken
rewrite would mute the retention alert for another full window.

`rules-sync` resolves its tenant the same way `observe` does, for the same reason: a rule group filed
under a tenant the gateway does not write with evaluates over nothing. `cardinality` still reads with
its own `--tenant`; pass the platform's, which `kubectl -n argus get configmap argus-platform-info -o
yaml` will tell you.

## Retention, enforced and alarmed

The profile's `budgets.retention` (`metrics: 30d`, `logs: 7d`, `traces: 7d`) is not a comment.
`argus platform` passes it to each backend as that backend's own retention setting: Mimir's
compactor block retention, Loki's retention period (plus the compactor switch that actually makes it
delete), Tempo's block retention. It also applies a platform-level `PrometheusRule` named
`argus-platform`, whose `ArgusRetentionWindowShort` fires when nothing was stored at the far edge of
the promised window - a compactor deleting early, or object storage that stopped accepting blocks.
The alert is gated on the platform being older than the promise (that is what `born` is for), so a
cluster stood up this morning does not alarm about its missing 29-day-old samples.

## Rules reach Mimir's ruler

Nothing in this stack evaluates a `PrometheusRule` on its own: kube-prometheus-stack is installed for
its operator, CRDs and Alertmanager, with Prometheus itself disabled, because Mimir is the metrics
engine. Rules become live only once they are pushed into Mimir's ruler.

- **The platform's own group** (`argus-platform`, which carries `ArgusRetentionWindowShort`) is
  pushed by `argus platform` itself at the end of a successful run, as the tenant it just recorded.
  A run from an operator's laptop usually cannot reach the in-cluster Mimir URL; that is not a
  failure of the run, so the push is skipped with one note on stderr naming the URL and the
  port-forward form. Pass `--mimir-url` to push it from there. A ruler that IS reachable and rejects
  the rules fails the run.
- **Each app's groups** are pushed by `argus observe` itself, on every run, in a `sync-rules` step
  that comes after the app's alert and SLO rules are applied and before its dashboards. The groups
  are filed under a ruler namespace named after the app, as the tenant the platform recorded, and
  the same step deletes any group the ruler still holds in that namespace that this run did not
  render (a renamed or removed SLO used to keep evaluating forever). It only ever touches the app's
  own namespace, never another app's and never `argus-platform`. When the ruler cannot be REACHED,
  an operator run reports it and carries on (the laptop case again; pass `--mimir-url`), while a job
  run fails, because a job runs inside the cluster where that name resolves and nobody is reading
  its stderr. A ruler that answers and rejects the rules fails either way.
- **`argus rules-sync --gojira <app> --env <env>`** stays, as the way to re-push (and re-prune)
  without re-observing. It resolves its tenant from the platform's record exactly the way `observe`
  does, and prunes exactly the way the step does, so the command and the step cannot leave the ruler
  in two different states.

## Endpoints: in-cluster by default, port-forward on request

Argus was always run by an operator through port-forwards, so `--mimir-url` used to default to
`localhost`. Running `observe` as a one-shot in-cluster Job changes that: the right default is the
platform's own Service DNS names in the platform's own namespace, not `localhost` (there is no
port-forward inside the cluster). `--mimir-url` (on `observe`, `rules-sync`, `cardinality`, and
`feedback`) and `--perses-url` (on `observe`) derive from `--namespace` (env `ARGUS_NAMESPACE`,
default `argus`)
unless given explicitly - an explicit flag always wins.

An operator's port-forward habit keeps working exactly as before; just say so explicitly:

```
kubectl -n argus port-forward svc/argus-mimir-gateway 9009:80
argus rules-sync --gojira app/gojira.yaml --env dev --mimir-url http://localhost:9009/prometheus
```

## Running observe as a Job

A provisioner (gojira) launches `argus observe` as a one-shot, in-cluster Job rather than
running the CLI itself. `argus platform` renders and applies the identity that Job runs as -
a ServiceAccount plus the narrowest RBAC that lets `observe` do its work: server-side apply
`ServiceMonitor`/`PodMonitor`/`PrometheusRule` (`monitoring.coreos.com`) cluster-wide (app
namespaces are created dynamically, so this cannot be namespace-scoped), plus `get` on the
platform's own `argus-platform-info` ConfigMap by name only. Nothing else - no secrets, no
wildcard, no delete, no workload access. Dashboards go over HTTP to Perses and need no
Kubernetes permission at all.

**The trust scope, stated plainly:** `argus-observe` is a platform-wide identity, not a
per-tenant sandbox. Its ClusterRole reaches those three kinds in **every** app namespace,
including namespaces that do not exist yet, because gojira creates app namespaces
dynamically and a namespace-scoped grant could not cover the next app. Anything that can
launch a Job as this ServiceAccount can therefore write monitors and rules for any app in
the cluster. That is accepted scope for a platform provisioner, not an oversight; the
narrowing that matters is the verb and kind list, and who may create Jobs in the platform
namespace.

**How the Job reaches the API server.** Every kubectl argv argus builds carries
`--request-timeout` (the bounded call). kubectl has an unadvertised side effect there: its
loader only falls back to the in-cluster service-account config when the merged config equals
kubectl's built-in default, and a timeout override alone makes it unequal, so a pod running
nothing but `kubectl ... --request-timeout=30s` quietly dials `localhost:8080`. Job mode does
not rely on the fallback. It writes a kubeconfig into its temp directory from the same
`KUBERNETES_SERVICE_HOST`/`KUBERNETES_SERVICE_PORT` and the mounted token and CA (by path, so
rotation still works and the token never appears in argv), and passes `--kubeconfig` on every
kubectl call. An operator run sees none of this: no service-account mount means no file is
written and the operator's own kubeconfig and `--kube-context` apply exactly as before.

A ready-to-copy Job manifest for the provisioner to create:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: argus-observe-<app>-<env>
  namespace: argus
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  # The outer bound on the summary line: past this the Job is killed, whatever it was
  # doing. Every kubectl call argus makes carries its own --request-timeout, so this is
  # the backstop, not the first line of defence.
  activeDeadlineSeconds: 900
  template:
    spec:
      serviceAccountName: argus-observe
      # Explicit, because observe needs it: it reads the platform's record and applies
      # its three kinds through the API server.
      automountServiceAccountToken: true
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        # The uid the image runs as (Dockerfile: USER 10001), stated on the pod as every
        # other pod in the platform does: the kubelet sets ownership on the projected
        # token from these, and an admission policy that wants an explicit uid passes.
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      volumes:
        # observe writes the contract, its throwaway ledger and sloth's temp files under
        # /tmp - the only path it ever writes to, which is what makes the root filesystem
        # below read-only.
        - name: tmp
          emptyDir: {}
      containers:
        - name: observe
          image: <argus image>@sha256:<digest>
          args: ["observe"]
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              memory: 512Mi
          env:
            - name: GOJIRA_CONTRACT_YAML
              value: <the app's gojira.yaml, as a string>
            - name: GOJIRA_CONTRACT_SHA256
              value: <sha256 of GOJIRA_CONTRACT_YAML, provisioner-computed>
            - name: ARGUS_ENV
              value: dev
            # Optional - defaults to "argus" (the platform namespace argus-observe's
            # RBAC was rendered for; must match what `argus platform` was run with).
            - name: ARGUS_NAMESPACE
              value: argus
```

`<argus image>@sha256:<digest>` is a placeholder: pin the published image by digest, never a
mutable tag, and never bake a registry name into this repo. The image is a normal argument to
the provisioner's own Job template, not something Argus renders.

One thing the job applies is not a cluster object at all: Perses documents (the project, its
datasources and the dashboards) are HTTP API objects, provisioned over HTTP, so they carry no
`argus.dev/contract-sha256` annotation and no `kubectl get` will find them.

The contract rides in an environment variable, and environment variables share the pod spec's
own size budget (the API server rejects an object over roughly 1 MiB), so keep `gojira.yaml`
small - well under that ceiling, since the spec carries everything else too.

The last line the job ever writes to stdout is always `ARGUS_SUMMARY <one line>`, on both
success and failure - the provisioner reads the Job's termination status for pass/fail and
scans the tail of the log for that line; it never parses the rest. Exit code 0 means observe
completed, 1 means it failed (the summary line names the failing step and reason).

```
ARGUS_SUMMARY observed demo/dev: 4 steps applied, 1 skipped, contract ab12cd34ef56
ARGUS_SUMMARY observe failed: apply_alerts: kubectl: forbidden
```

The counts are **steps**, not objects: how many engine steps ran and how many were skipped.
A success can carry one parenthetical note, and today there is exactly one:
`(no platform record: tenant from contract)`, which means `observe` found no
`argus-platform-info` record and fell back to the cluster string in the contract. The run is
real and the app is wired, but the tenant it reads with was never confirmed against the one
the gateway writes with - re-run `argus platform`. A record that exists and cannot be READ is
not that case and does not fall back: in a real job run it fails with
`ARGUS_SUMMARY observe failed: platform record unreadable: ...`, because the likeliest reason
is that the job's identity was never rolled out. `--dry-run` is the one exception, in job
mode exactly as everywhere else: it touches nothing and must never require a cluster, so an
unreadable record falls back to the contract's cluster and the run still ends with the
`(no platform record: tenant from contract)` note.

**`observe` never deletes.** It renders what the app's class asks for and server-side applies
it; nothing prunes. So a class change that drops the scrape family (or sets `app_scrape:
false`), or an app rename, leaves the previous `ServiceMonitor`/`PodMonitor`/`PrometheusRule`
live in the app's namespace, scraping and alerting, until somebody removes it by hand:

```
kubectl -n <app-ns> delete servicemonitors,podmonitors,prometheusrules -l argus.dev/managed=true
```

That is the deliberate trade: the job's identity carries no `delete` verb at all, so it cannot
remove an object it did not create either. Label-selector pruning is on the roadmap and needs
that identity widened first.

`ARGUS_CLUSTER` is an explicit override and nothing more. The case it was invented for - gojira's
record of a cluster disagreeing with the cluster's real name - is now answered by the platform's own
record, which is the authority: with no override, `observe` adopts the tenant the platform writes
with and says so. Set `ARGUS_CLUSTER` only when you mean to assert a tenant, and it must match the
recorded one or the job fails with `tenant mismatch: ...` as its summary line.

The provisioner itself needs far less than `observe` does, all of it in the platform namespace:
`create` and `get` on `jobs` (launch the Job, poll its conditions), plus `get` and `list` on
`pods` and `get` on `pods/log`, because the summary line is read from the Job's pod log
(`kubectl logs job/<name>` lists the Job's pods, then reads one). Without the pod and log
verbs the Job still runs and the verdict is still the exit condition, but the ledger row
loses argus's one-line explanation. It does not need the `argus-observe` ServiceAccount's own
RBAC - that identity belongs to the Job it launches, not to the thing launching the Job.

## The image

The `Dockerfile` at the repo root builds the image the Job above runs: `python:3.14-slim`,
upgraded (`apt-get -y upgrade`) so a stale cached base layer never ships known-fixed CVEs,
running as the non-root numeric user `10001` with `HOME=/tmp`. `ENTRYPOINT` is
`python -m argus.cli`; `CMD` is `observe`, so the Job manifest above only needs to pass
`args: ["observe"]`.

**The image is for `observe` and `render`.** It carries the CLI, the profiles, the
integrations catalog and `helm/pins.yaml` - not `helm/values/`, which `argus platform` reads
for every chart it installs, so `platform` does not run from this image and is not meant to:
standing up a cluster is an operator's run from a checkout, once, not a job.

**The base tag floats on purpose.** `python:3.14-slim` is a moving tag, because the point of
the `apt-get -y upgrade` above is to pick up whatever the base has fixed since the last build;
pinning the base by digest would freeze exactly what that line exists to refresh. What is
pinned is the OUTPUT: every build publishes `<image>@sha256:<digest>`, and that digest is what
a provisioner's Job config points at. The floating input is rebuilt and re-published; the
immutable output is what runs.

**The layout law.** `REPO_ROOT` (`agent/argus/cli.py`) is `Path(__file__).resolve().parents[2]`
- derived from the source file's own location, not an installed package - and `profiles/`,
`integrations/` and `helm/pins.yaml` are read from it at runtime. The image therefore does
**not** `pip install` argus into site-packages: it `COPY`s the repo layout verbatim under
`/app` (`agent/`, `profiles/`, `integrations/`, `helm/pins.yaml`) and runs with
`PYTHONPATH=/app/agent`, so `REPO_ROOT` resolves to `/app` inside the container exactly as it
does at the repo root on disk. `tests/unit/test_layout.py` guards this: if it ever fails, the
Dockerfile's `COPY` lines need to move with whatever changed. Runtime dependencies
(`pydantic`, `pyyaml`) are `pip install`ed at the exact versions `pyproject.toml` pins, never
the `dev` group; `tests/unit/test_dockerfile_pins.py` fails the build the moment those two
drift apart.

**Two pinned binaries live in the image, neither from PyPI:**

- `kubectl`, at `/usr/local/bin/kubectl`, downloaded from `dl.k8s.io` at the exact
  `KUBECTL_VERSION` build arg and verified against its own published `.sha256` file before
  `chmod +x`.
- `sloth`, at `/app/.argus/tools/sloth-<version>`, the same binary and version
  `scripts/validate-rendered.sh` fetches locally for `make validate` (one pin, enforced by
  `test_dockerfile_pins.py`). `observe` shells out to it to turn an app's SLO spec into a
  `PrometheusRule` (`_generate_slo_rules`); without it, every app with SLOs would fail in the
  Job. Sloth publishes no checksum file for this asset, so its sha256 is hand-recorded in the
  Dockerfile's `SLOTH_SHA256_AMD64` build arg and checked with `sha256sum -c` at build time.
  `.argus` is otherwise runtime scratch and is never copied into the image wholesale (see
  `.dockerignore`) - this one binary is the sole exception, placed there deliberately.

The image is `linux/amd64` only. It is built by an operator from a commit and pushed to the registry their platform reads; this repository publishes nothing. A provisioner's Job config pins the resulting `<image>@sha256:<digest>`, never a mutable tag (see the Job manifest above).
