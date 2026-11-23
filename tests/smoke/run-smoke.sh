#!/usr/bin/env bash
# The Phase 1 DoD gate (testing.md layer 4, issue #24): empty kind cluster -> solo
# platform via the step engine -> sample app observed by class -> a real metric AND
# trace queryable in Perses, dashboard + SLO present. Run: make smoke [KEEP=1]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CLUSTER=noto-smoke
CTX=kind-$CLUSTER
LEDGER="$REPO/.noto/smoke.db"
# Python 3.14 site ignores underscore .pth files -> hatchling editable installs break
# console scripts (see memory/pyproject note). PYTHONPATH is the deterministic fix.
export PYTHONPATH="$REPO/agent"
START=$(date +%s)

step() { echo; echo "=== [$((($(date +%s)-START)/60))m] $*"; }

# On a failure, say what the cluster looked like: a timed-out install names the
# workloads that were not ready, never why.
diagnose() {
  echo; echo "=== smoke failed; pods not ready:"
  kubectl --context "$CTX" get pods -A --no-headers 2>/dev/null | grep -v -e Running -e Completed || true
  kubectl --context "$CTX" get events -A --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -25 || true
  kubectl --context "$CTX" top nodes 2>/dev/null || kubectl --context "$CTX" describe nodes 2>/dev/null | grep -A8 "Allocated resources" || true
}
trap diagnose ERR

step "kind cluster"
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 120s

step "object store image (MinIO publishes no image any more; images/minio rebuilds the pinned release)"
MINIO_TAG=RELEASE.2024-12-18T13-15-44Z
docker build -q -t "noto-smoke/minio:$MINIO_TAG" "$REPO/images/minio"
kind load docker-image "noto-smoke/minio:$MINIO_TAG" --name "$CLUSTER"

step "noto platform (step engine; resumable)"
rm -f "$LEDGER"
uv run noto platform --cluster "$CLUSTER" --env dev --kube-context "$CTX" --ledger "$LEDGER" \
  --set mimir:minio.image.repository=noto-smoke/minio --set "mimir:minio.image.tag=$MINIO_TAG" \
  --set mimir:minio.mcImage.repository=noto-smoke/minio --set "mimir:minio.mcImage.tag=$MINIO_TAG"

step "sample apps (podinfo + traffic; exemplar-demo for the #47 exemplar assertion)"
kubectl --context "$CTX" apply -f "$REPO/tests/smoke/sample-app.yaml"
kubectl --context "$CTX" apply -f "$REPO/tests/smoke/exemplar-app.yaml"

step "perses port-forward (observe provisions dashboards through it, no separate script)"
kubectl --context "$CTX" -n noto rollout status statefulset/noto-perses --timeout=300s
kubectl --context "$CTX" -n noto port-forward svc/noto-perses 8080:8080 >/dev/null 2>&1 &
PF_PERSES=$!
for i in $(seq 1 30); do
  curl -sf http://localhost:8080/api/v1/health >/dev/null 2>&1 && break
  sleep 2
done

step "noto observe (wire podinfo by class: collection, alerts, and dashboards together)"
uv run noto observe --gojira "$REPO/tests/smoke/gojira.yaml" --env dev \
  --kube-context "$CTX" --ledger "$LEDGER" --perses-url http://localhost:8080

step "rules-sync (Mimir ruler, tenant-scoped) + cardinality report"
kubectl --context "$CTX" -n noto port-forward svc/noto-mimir-gateway 9009:80 >/dev/null 2>&1 &
PF_MIMIR=$!
for i in $(seq 1 15); do
  curl -sf -m 2 -H "X-Scope-OrgID: noto-smoke" http://localhost:9009/prometheus/api/v1/labels >/dev/null 2>&1 && break
  sleep 2
done
uv run noto rules-sync --gojira "$REPO/tests/smoke/gojira.yaml" --env dev \
  --mimir-url http://localhost:9009/prometheus --ledger "$LEDGER"
uv run noto cardinality --tenant noto-smoke --mimir-url http://localhost:9009/prometheus --limit 8

step "wait for telemetry, then assert the DoD through the Perses proxy"
uv run python "$REPO/tests/smoke/assert_dod.py"
kill $PF_PERSES 2>/dev/null || true

step "PASS - Phase 1 definition of done met"
if [ "${KEEP:-0}" != "1" ]; then
  step "teardown (KEEP=1 to skip)"
  kind delete cluster --name "$CLUSTER"
fi
