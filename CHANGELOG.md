# Changelog

All notable changes to Argus are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/) (see [docs/versioning.md](docs/versioning.md)).

## [Unreleased]

### Changed

- Visualization is now **Perses**, not Grafana. Perses is CNCF, Apache-2.0, and dashboards-as-code
  (native CRDs), and covers metrics (Mimir), traces (Tempo), and logs (Loki) - satisfying the Phase-1
  trace requirement with no Grafana in the stack. Retires the "LGTM" label in favor of naming the
  substrate explicitly (Mimir/Loki/Tempo + Perses). See decision 7 in
  [docs/design.md](docs/design.md).

### Added

- Phase 0 foundations: contributor guide with branching strategy and commit conventions
  ([CONTRIBUTING.md](CONTRIBUTING.md)), versioning and release policy
  ([docs/versioning.md](docs/versioning.md)), testing philosophy and layers
  ([docs/testing.md](docs/testing.md)), a conventions lint, and a CI skeleton.
- Baseline repo scaffold: OTel-style README, project conventions in CLAUDE.md, Apache-2.0 license,
  design and source-capture docs, and the project-local web-design-guidelines skill.
