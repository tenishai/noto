#!/usr/bin/env bash
# Validate every helm/pins.yaml component: fetch the PINNED chart version and
# `helm template` it with our values. Real-tool validation for the Helm layer -
# a values key the chart does not accept, or a chart/version that does not exist,
# fails here instead of at apply time.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PINS="$REPO/helm/pins.yaml"

uv run python - "$PINS" <<'EOF' > /tmp/noto-helm-matrix.txt
import sys
import yaml

pins = yaml.safe_load(open(sys.argv[1]))
for name, c in pins["components"].items():
    print(name, c["repo"], c["chart"], c["version"], c["values"])
EOF

while read -r name repo chart version values; do
  echo "==> $name ($chart $version)"
  helm template "noto-$name" "$chart" \
    --repo "$repo" --version "$version" \
    -f "$REPO/helm/$values" \
    --namespace noto --kube-version 1.36.0 >/dev/null
  echo "    ok"
done < /tmp/noto-helm-matrix.txt

echo "==> validate-helm: every pinned chart templates cleanly with our values"
