# Alerts reach people: design

Status: design, 2026-09-21. Part 1 (the three platform gaps) is approved to build now.
Part 2 (routing to chat) is designed here and waits on two things outside this repo.

## The problem, verified on the dev cluster 2026-09-21

Alerting is decided in `docs/design.md` (Alertmanager plus `PrometheusRule`), and
Alertmanager is deployed. But nothing an app's alert rule says can reach a person:

1. **The ruler does not know where Alertmanager is.** Mimir's ruler has no
   `alertmanager_url`. A firing alert is evaluated and then dropped.
2. **A born-observed app's rules never reach the ruler.** `observe` applies the app's
   `PrometheusRule` objects, and those are inert in this stack (the operator runs, no
   Prometheus does). Only `argus platform` and the separate `argus rules-sync` command
   push to the ruler. An app provisioned through job mode has alert rules that no engine
   evaluates. The ruler currently holds groups only for apps someone ran `rules-sync` for
   by hand.
3. **Alerts cannot be routed by who owns them.** Rules carry one label, `severity`.
   Nothing says which app an alert belongs to, so nothing can send it to that app's team.
4. **Alertmanager has one receiver, `"null"`.** No route, no destination.

## Part 1: the three platform gaps (build now)

### 1. The ruler is told where Alertmanager is

`solo_endpoints(namespace)` gains `alertmanager_url`:
`http://alertmanager-operated.{namespace}.svc:9093`. That is the operator's own governing
service, so the name does not depend on the helm release name or its truncation
(the release's own service is `argus-kube-prometheus-stac-alertmanager`, cut at 63
characters by the chart, which is exactly the kind of name not to hardcode).

`argus platform` sets `mimir.structuredConfig.ruler.alertmanager_url` to it on the mimir
release, through the same `--set` passthrough the retention and cardinality values use.

### 2. `observe` pushes the app's rules to the ruler, and prunes what it no longer renders

A new step, `SyncRules`, runs in `observe` after the `alerts` and `slo` families are
applied and before `ProvisionPerses`. It:

- pushes every group from the rendered `alerts` and `slo` `PrometheusRule` docs to the
  ruler, tenant-scoped, under the ruler namespace named after the app (the same namespace
  `argus rules-sync` already uses, so the two converge on one set of groups);
- lists the groups the ruler holds in that namespace and deletes any this run did not
  render (closes #57: a renamed or removed SLO left its old group evaluating forever).

Failure semantics follow `_push_platform_rules`, with one difference for job mode:

| Situation | Operator mode | Job mode |
|---|---|---|
| dry run | reports what it would push; opens no socket | same |
| ruler unreachable | note on stderr, step succeeds (a laptop cannot reach the in-cluster URL) | step FAILS (the job runs in the cluster; unreachable is real) |
| ruler answers and rejects | step fails | step fails |

The ruler URL is `solo_endpoints(platform_namespace)["mimir_query_url"]` unless
`--mimir-url` is given, which is what `rules-sync` resolves today.

`argus rules-sync` stays, as the way to re-push without re-observing. It gains the same
pruning, so the command and the step cannot disagree.

### 3. Every per-app alert says whose it is

Every rule in an app's rendered `PrometheusRule` (collector health, cardinality, scrape
down) gains the labels `app`, `namespace`, `class` and `environment`, beside the existing
`severity`. The Sloth spec's SLO-level `labels` gain `app`, `namespace` and `class`, so
Sloth's generated page and ticket alerts carry them too. The platform's own retention
alert gains `app: argus-platform`.

`team` is deliberately not added here. The contract does not carry a team today, and
adding a contract field is a change to agree with the deploy platform's owners (Part 2).
`app` is enough to route on: one app, one owner.

## Part 2: routing to chat (designed, not built yet)

Waits on: (a) the organisation's chat alerting credential existing as a Kubernetes Secret
in the platform namespace, which is what the assembly room's Connections area will supply
through the secret backend; (b) agreeing one contract field with the deploy platform's
owners.

**Two audiences.** The admin channel receives every alert. An app's own channel also
receives that app's alerts when its owner opted in.

**Shape.** One `AlertmanagerConfig` per destination, all in the PLATFORM namespace:

- `argus-route-admin`, rendered by `argus platform`: matches everything, sends to the
  admin channel.
- `argus-route-<app>`, rendered by `observe` as a new `routing` family when the contract
  asks for it: matches `app="<app>"`, sends to the app's channel.

The operator gives every `AlertmanagerConfig`'s top route `continue: true`, so the admin
route and the app route both fire for the same alert. The Alertmanager CR is set to
`alertmanagerConfigMatcherStrategy: {type: None}` so the operator does not inject a
namespace matcher that would confine each config to alerts from its own namespace.

**Why all in the platform namespace:** an `AlertmanagerConfig` can only reference a Secret
in its own namespace. Putting per-app configs in app namespaces would copy the chat
credential into every app's namespace. Here it lives in exactly one place, and the observe
job's identity gains one namespaced Role (create/patch `alertmanagerconfigs` in the
platform namespace), not a cluster-wide one.

**Credential.** One chat bot token, not per-channel webhooks: the receiver posts through
the chat API with `httpConfig.authorization.credentials` referencing the platform Secret,
and `channel` is set per config. One credential reaches any channel the bot is invited to.
Absent Secret means routing is off and the `"null"` receiver stays, the same "absent =
feature off" rule the rest of the platform follows.

**Contract field (to agree):**

```yaml
observability:
  alerts:
    channel: C0123456789   # present = also send this app's alerts here
```

The assembly room writes it when the agent's owner ticks the box on the agent's chat page;
it travels in the canonicalized contract the observe job already receives, so there is no
second wiring path. Platform inputs: `argus platform --alerts-channel <id>
--alerts-secret <name>`.

## Testing

Unit tests for each gap (rendered helm sets, the step's push/prune/failure table with an
injected HTTP function, label presence across every rendered rule, golden files updated).
Live proof on the dev cluster after merge: a deliberately failing rule shows up in
Alertmanager's API (`/api/v2/alerts`) with the `app` label on it. Until Part 2, it lands on
the `"null"` receiver, which is expected.
