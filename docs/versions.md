# Pinned Versions (canonical reference)

Argus pins everything (a hard convention - see [CLAUDE.md](../CLAUDE.md) and
[docs/versioning.md](versioning.md)). This file is the **single source of truth** for the versions we
pin to. When we author the ocb manifest, Helm value pins, and Terraform provider constraints, the
versions come from here, and this file is updated as a deliberate, reviewed change (never floated).

> **Living document.** Versions below are the latest stable as of **2026-06-16**. Bumping any of them
> is a normal `chore`/`feat` change with its own PR and CHANGELOG entry. Do not edit a pin in a feature
> branch as a side effect.

## Reality check: most of this stack is pre-1.0

"Latest and highest" does not mean "stable API" here. The OpenTelemetry collector train, the operators,
Alertmanager, and especially **Perses (breaking changes between minor versions)** are all 0.x. That is
exactly why we pin exact versions and back them with the contract-test layer
([docs/testing.md](testing.md)): an unpinned bump can silently change rendered config or break a chart.

## Collection tier

| Component | Version | Helm chart | Source | Notes |
|---|---|---|---|---|
| OTel Collector core / builder (`ocb`) | 1.60.0 (stable) / 0.154.0 (beta train) | n/a | [collector-releases](https://github.com/open-telemetry/opentelemetry-collector-releases/releases) | ocb + distro binaries ship at tag `v0.154.0`; assembled collector is 0.x |
| OTel Collector Contrib | 0.154.0 | n/a | [contrib releases](https://github.com/open-telemetry/opentelemetry-collector-contrib/releases) | same 2-week train, 0.x |
| OTel Operator | 0.153.0 (app) | 0.115.0 | [otel helm charts](https://github.com/open-telemetry/opentelemetry-helm-charts) | 0.x |
| Prometheus Operator | 0.91.0 (app) | kube-prometheus-stack 86.2.3 | [prometheus-operator](https://github.com/prometheus-operator/prometheus-operator/releases) | verify standalone tag (0.91.0 vs 0.90.1) before pinning |

## Backends

| Component | Version | Helm chart | Source | Notes |
|---|---|---|---|---|
| Grafana Mimir | 3.1.1 | mimir-distributed 6.1.0 | [mimir releases](https://github.com/grafana/mimir/releases) | pin stable, not `-weekly` dev tags |
| Grafana Loki | 3.7.2 | loki 17.4.4 | [loki releases](https://github.com/grafana/loki/releases) | OSS chart **moved to `grafana-community/helm-charts`** (~2026-03); old `grafana/loki` ended at 6.55.0. Confirm chart appVersion at pin time |
| Grafana Tempo | 2.10.7 | tempo-distributed 2.25.2 | [tempo releases](https://github.com/grafana/tempo/releases) | chart also **moved to `grafana-community/helm-charts`** |
| Perses (visualization) | 0.53.1 | ~0.21.x (verify) | [perses releases](https://github.com/perses/perses/releases) | **0.x, breaking changes between minors - pin tightly.** Charts at `perses/helm-charts` (`https://perses.github.io/helm-charts`); confirm exact chart tag at pin time |
| Alertmanager | 0.33.0 | ships in kube-prometheus-stack | [alertmanager releases](https://github.com/prometheus/alertmanager/releases) | 0.x |

## Cluster scrape sources & bootstrap

| Component | Version | Helm chart | Source | Notes |
|---|---|---|---|---|
| kube-state-metrics | 2.19.0 | 7.4.1 (subchart in k-p-s) | [kube-state-metrics](https://github.com/kubernetes/kube-state-metrics/releases) | verify standalone app + chart versions at pin time |
| prometheus node-exporter | 1.11.1 | prometheus-node-exporter 4.55.0 | [node_exporter](https://github.com/prometheus/node_exporter/releases) | standalone chart may be higher than the k-p-s subchart |
| cert-manager | 1.20.2 | v1.20.2 (chart = app) | [cert-manager](https://github.com/cert-manager/cert-manager/releases) | 1.0+, stable. OCI: `oci://quay.io/jetstack/charts/cert-manager:v1.20.2` |

## Cloud / infrastructure

| Component | Version | Source | Notes |
|---|---|---|---|
| Terraform CLI | 1.15.6 | [terraform releases](https://github.com/hashicorp/terraform/releases/latest) | stable |
| `hashicorp/google` provider | 7.37.0 | [registry](https://registry.terraform.io/providers/hashicorp/google/latest) | pin with `~>` minor constraint + committed lockfile |
| `hashicorp/google-beta` provider | 7.37.0 | [registry](https://registry.terraform.io/providers/hashicorp/google-beta/latest) | as above |

## Agent runtime (Python)

The Argus agent is built in **Python** for consistency with the sibling repos (gojira, techne, limenal,
cabal are all Python) and first-class Anthropic SDK support. See decision 8 in
[docs/design.md](design.md).

| Component | Version | Source | Notes |
|---|---|---|---|
| CPython | 3.14.6 | [python.org](https://www.python.org/downloads/) | pin `requires-python` to the 3.14 line |
| uv (env / deps) | 0.11.21 | [astral-sh/uv](https://github.com/astral-sh/uv/releases/latest) | lockfile committed; 0.x |
| `anthropic` (Python SDK) | 0.109.2 | [PyPI](https://pypi.org/project/anthropic/) | GA line (0.x by semver). Default model `claude-opus-4-8` |
| `ruff` (lint / format) | latest stable | [astral-sh/ruff](https://github.com/astral-sh/ruff/releases) | pin exact in `pyproject.toml`; wired into the CI static layer |

## CI (GitHub Actions)

| Action | Version | Notes |
|---|---|---|
| `actions/checkout` | v6 | bumped from v4 |
| `actions/setup-python` | v6 | added when the agent code lands |
| `actions/setup-go` | v6 | only if a Go build step is ever needed (e.g. ocb invocation) |

Runner image is `ubuntu-latest` (the conventional GitHub-hosted label). If we need bit-for-bit
reproducible runs we pin to a dated image (e.g. `ubuntu-24.04`); not required today.

## Verify-before-pinning checklist

Confirm these at the moment we author the pins, since the research left them uncertain:

- [ ] Prometheus Operator standalone tag (0.91.0 vs 0.90.1).
- [ ] kube-state-metrics standalone app + chart versions.
- [ ] Loki / Tempo chart appVersions under `grafana-community/helm-charts` (and update any reference to
      the old `grafana/helm-charts` repo).
- [ ] Perses Helm chart exact tag.
