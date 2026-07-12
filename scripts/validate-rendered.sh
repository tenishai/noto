#!/usr/bin/env bash
# Validate rendered artifacts against the REAL consumers' own validators.
# This is the layer our self-referential tests cannot cover: something outside our
# own expectations judges the output. Versions are pinned (docs/versions.md).
#
# Tools + what they judge:
#   otelcol-contrib validate  - collector configs (the big one)
#   sloth generate            - SLO specs (proves Sloth accepts them AND emits rules)
#   kubeconform               - CRD shapes against published schemas (CRDs-catalog)
#   percli lint               - Perses resources
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$REPO/.argus/tools"
RENDERED="$REPO/.argus/rendered"

OTELCOL_VERSION=0.154.0
SLOTH_VERSION=0.16.0
KUBECONFORM_VERSION=0.8.0
PERSES_VERSION=0.53.1

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) OS=darwin; ARCH=arm64 ;;
  Linux-x86_64) OS=linux; ARCH=amd64 ;;
  *) echo "unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$TOOLS"

fetch() { # url dest
  [ -x "$2" ] && return 0
  echo "==> fetching $(basename "$2")"
  curl -sSfL "$1" -o "$2.download"
  mv "$2.download" "$2"
  chmod +x "$2"
}

fetch_tar() { # url binary-name dest
  [ -x "$3" ] && return 0
  echo "==> fetching $(basename "$3")"
  local tmp; tmp=$(mktemp -d)
  curl -sSfL "$1" -o "$tmp/a.tar.gz"
  tar -xzf "$tmp/a.tar.gz" -C "$tmp"
  find "$tmp" -name "$2" -type f -exec mv {} "$3" \;
  chmod +x "$3"
  rm -rf "$tmp"
}

OTELCOL="$TOOLS/otelcol-contrib-$OTELCOL_VERSION"
SLOTH="$TOOLS/sloth-$SLOTH_VERSION"
KUBECONFORM="$TOOLS/kubeconform-$KUBECONFORM_VERSION"
PERCLI="$TOOLS/percli-$PERSES_VERSION"

fetch_tar "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v$OTELCOL_VERSION/otelcol-contrib_${OTELCOL_VERSION}_${OS}_${ARCH}.tar.gz" otelcol-contrib "$OTELCOL"
fetch "https://github.com/slok/sloth/releases/download/v$SLOTH_VERSION/sloth-$OS-$ARCH" "$SLOTH"
fetch_tar "https://github.com/yannh/kubeconform/releases/download/v$KUBECONFORM_VERSION/kubeconform-$OS-$ARCH.tar.gz" kubeconform "$KUBECONFORM"
fetch_tar "https://github.com/perses/perses/releases/download/v$PERSES_VERSION/perses_${PERSES_VERSION}_${OS}_${ARCH}.tar.gz" percli "$PERCLI"

echo "==> rendering artifacts"
rm -rf "$RENDERED"
uv run python "$REPO/scripts/render-for-validation.py" "$RENDERED"

echo "==> otelcol validate ($(ls "$RENDERED/otelcol" | wc -l | tr -d ' ') configs)"
export KUBE_NODE_NAME=validation-node
export POD_NAME=validation-pod
# kubeletstats (serviceAccount auth) reads the in-cluster SA cert at config-build
# time. Create the expected files when we can (Linux CI: sudo works); otherwise the
# agent configs are validated in CI and live in the smoke test - skip LOUDLY here.
SA_DIR=/var/run/secrets/kubernetes.io/serviceaccount
SKIP_AGENT=0
if [ ! -f "$SA_DIR/ca.crt" ]; then
  PLACEHOLDER_CERT=$(mktemp)
  openssl req -x509 -newkey rsa:2048 -nodes -keyout /dev/null -out "$PLACEHOLDER_CERT" \
    -days 1 -subj "/CN=argus-validation-placeholder" 2>/dev/null
  if sudo -n mkdir -p "$SA_DIR" 2>/dev/null && sudo -n cp "$PLACEHOLDER_CERT" "$SA_DIR/ca.crt" 2>/dev/null && sudo -n tee "$SA_DIR/token" >/dev/null 2>&1 <<<"validation-placeholder" && sudo -n chmod -R a+rX /var/run/secrets 2>/dev/null; then
    echo "    (created placeholder in-cluster SA files for kubeletstats validation)"
  else
    SKIP_AGENT=1
    echo "    WARNING: cannot create $SA_DIR (no passwordless sudo) - agent configs"
    echo "    SKIPPED locally; they are fully validated in CI (linux) and live in smoke."
  fi
fi
for cfg in "$RENDERED"/otelcol/*.yaml; do
  if [ "$SKIP_AGENT" = "1" ] && [[ "$(basename "$cfg")" == *-agent.yaml ]]; then
    echo "    SKIPPED (local, no SA files): $(basename "$cfg")"
    continue
  fi
  "$OTELCOL" validate --config="$cfg"
  echo "    ok: $(basename "$cfg")"
done

echo "==> sloth generate"
for spec in "$RENDERED"/sloth/*.yaml; do
  "$SLOTH" generate -i "$spec" -o /dev/null >/dev/null
  echo "    ok: $(basename "$spec")"
done

echo "==> kubeconform (CRDs-catalog schemas)"
"$KUBECONFORM" -strict -summary \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  "$RENDERED"/k8s/*.yaml

echo "==> percli lint"
for doc in "$RENDERED"/perses/*.yaml; do
  "$PERCLI" lint -f "$doc"
  echo "    ok: $(basename "$doc")"
done

echo "==> validate-rendered: all real-tool validations passed"
