#!/usr/bin/env bash
# The Phase 1 DoD gate (testing.md layer 4, issue #24): empty kind cluster -> solo
# platform via the step engine -> sample app observed by class -> a real metric AND
# trace queryable in Perses, dashboard + SLO present. Run: make smoke [KEEP=1]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CLUSTER=argus-smoke
CTX=kind-$CLUSTER
LEDGER="$REPO/.argus/smoke.db"
# Python 3.14 site ignores underscore .pth files -> hatchling editable installs break
# console scripts (see memory/pyproject note). PYTHONPATH is the deterministic fix.
export PYTHONPATH="$REPO/agent"
START=$(date +%s)

step() { echo; echo "=== [$((($(date +%s)-START)/60))m] $*"; }

step "kind cluster"
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 120s

step "argus platform (step engine; resumable)"
rm -f "$LEDGER"
uv run argus platform --cluster "$CLUSTER" --kube-context "$CTX" --ledger "$LEDGER"

step "sample app (podinfo + traffic)"
kubectl --context "$CTX" apply -f "$REPO/tests/smoke/sample-app.yaml"

step "argus observe (wire podinfo by class)"
uv run argus observe --gojira "$REPO/tests/smoke/gojira.yaml" --env dev \
  --kube-context "$CTX" --ledger "$LEDGER"

step "provision perses (rendered docs via API, like the spike)"
uv run argus render --gojira "$REPO/tests/smoke/gojira.yaml" --env dev --out "$REPO/.argus/smoke-rendered"
kubectl --context "$CTX" -n argus rollout status statefulset/argus-perses --timeout=300s
kubectl --context "$CTX" -n argus port-forward svc/argus-perses 8080:8080 >/dev/null 2>&1 &
PF_PERSES=$!
for i in $(seq 1 30); do
  curl -sf http://localhost:8080/api/v1/health >/dev/null 2>&1 && break
  sleep 2
done
uv run python "$REPO/tests/smoke/provision_perses.py" "$REPO/.argus/smoke-rendered/perses.yaml"

step "rules-sync (Mimir ruler, tenant-scoped) + cardinality report"
kubectl --context "$CTX" -n argus port-forward svc/argus-mimir-gateway 9009:80 >/dev/null 2>&1 &
PF_MIMIR=$!
for i in $(seq 1 15); do
  curl -sf -m 2 -H "X-Scope-OrgID: argus-smoke" http://localhost:9009/prometheus/api/v1/labels >/dev/null 2>&1 && break
  sleep 2
done
uv run argus rules-sync --gojira "$REPO/tests/smoke/gojira.yaml" --env dev \
  --mimir-url http://localhost:9009/prometheus --ledger "$LEDGER"
uv run argus cardinality --tenant argus-smoke --mimir-url http://localhost:9009/prometheus --limit 8

step "wait for telemetry, then assert the DoD through the Perses proxy"
uv run python "$REPO/tests/smoke/assert_dod.py"
kill $PF_PERSES 2>/dev/null || true

step "PASS - Phase 1 definition of done met"
if [ "${KEEP:-0}" != "1" ]; then
  step "teardown (KEEP=1 to skip)"
  kind delete cluster --name "$CLUSTER"
fi
