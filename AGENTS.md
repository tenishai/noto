# AGENTS.md - argus

Instructions for AI coding agents working in this repo. Claude Code loads this via
`CLAUDE.md` (`@AGENTS.md`); other AGENTS.md-aware tools read it directly.

Argus is the **observability arm of [gojira](https://github.com/tenishai/gojira)**: it takes a deployment
from zero to a full, end-to-end open-source observability platform (OpenTelemetry + Mimir/Loki/Tempo + Perses),
automatically, driven by a self-contained agent and Agent Skills. Read [docs/design.md](docs/design.md)
before making structural changes.

## The ecosystem (argus is one body part)

Argus is not standalone. It is one limb of a shared, opinionated SRE/agent platform - a set of sibling
repos meant to be merged into one GitHub org. Each owns one concern; Argus owns observability:

| Repo | Role | Relationship to Argus |
|---|---|---|
| [gojira](https://github.com/tenishai/gojira) | Opinionated deploy platform (identity, CI/CD, manifests, sizing) | **Parent.** Argus continues gojira's lifecycle; reads its contract/state; maps its workload class to an observability profile. |
| [techne](https://github.com/tenishai/techne) | Agentic memory (sessions, knowledge, identity; SQLite) | Argus's SQLite ledger migrates into techne later, so the agent's deploy history becomes its memory. |
| [liminal](https://github.com/tenishai/liminal) | MCP gateway (one connection point dispatching to many tool handlers) | Argus reaches its tools through liminal once integrated; Argus operations can be exposed as a liminal handler. |
| [cabal](https://github.com/tenishai/cabal) | Agent orchestration / A2A broker (LLM personas) | Argus registers as the observability persona later (requires a cabal `PERSONA_REGISTRY` PR; the daedalus bundle's cabal peer surface is the nearer path). |
| [daedalus](https://github.com/tenishai/daedalus) | Agent construction kit ("an agent is a directory": card + persona + wiring) | The Argus agent's shell is a daedalus bundle; Argus keeps a thin CLI fallback while daedalus is v0.1. |
| [ladon](https://github.com/tenishai/ladon) | Secrets / vault management on OpenBao + External Secrets | Argus onboards as a ladon "team"; ESO syncs OpenBao values into plain K8s Secrets that collectors/backends mount. |

Build Argus to run **self-contained today** (own runtime, step engine, SQLite ledger) but **compose
with these later** (register into cabal, reach tools via liminal, persist memory into techne). Do not
duplicate what a sibling owns; integrate with it.

## Hard conventions

- **No em dashes** in prose, docs, README, UI copy, or commit messages. Use a spaced hyphen ` - `,
  a comma, or parentheses. (Code and generated config are exempt.)
- **100% open source.** No vendor backend, no proprietary dependency, no phone-home. Licensed
  **AGPL-3.0-or-later**, matching the tenishai family (gojira, techne, liminal, cabal, daedalus); this
  permits library-level embedding of the siblings without a license boundary.
- **Agent-first, no web UI** as a core surface. The agent is the interface.
- **gojira-native.** gojira's contract and state are the source of truth; do not duplicate them.
- **No third-party GitOps** (no ArgoCD/Flux) in the apply path. Argus applies directly via cloud
  CLI / SDK / Helm and Operator CRDs through an idempotent step engine.
- **README style** mirrors the [OpenTelemetry Collector README](https://github.com/open-telemetry/opentelemetry-collector/blob/main/README.md):
  title block, badges, nav links, Overview/objectives, How it works, Stack, Status, Docs. Keep it short.
- Pin everything (ocb component versions, Helm charts, OpenTofu providers). No floating `latest`.
- **Latest-stable on introduction.** Whenever you add a new dependency, tool, chart, provider, action,
  base image, or language/runtime, first verify its current latest stable version from the authoritative
  source (GitHub releases, official registry/docs) - do not trust memory, web-check it - then pin to that
  exact version and record it in [docs/versions.md](docs/versions.md), the single source of truth for
  pins. Prefer the highest stable release; if a project is pre-1.0 (much of our stack is), pin tightly
  and note it. Bumping a pin is its own reviewed `chore`/`feat` PR with a CHANGELOG entry, never a
  silent float. When in doubt about whether something is current, check before pinning.

## Repo layout

```
distro/      OTel Collector distribution (ocb manifest + build)
terraform/   cloud-side resources: buckets, IAM/WIF (OpenTofu/SDK; HCL dir name kept)
helm/        pinned Helm values for in-cluster components
crds/        OTel/Prometheus Operator CRDs (collectors, ServiceMonitors)
profiles/    observability profiles mapped from gojira workload class
content/     dashboards, recording rules, SLOs, alert packs per profile
agent/       self-contained agent: step engine, gojira-reader, renderers
  skills/    Argus Agent Skills (Anthropic format)
docs/        design doc, enablement guide, platform evolution
```

## Architecture cards

`ARCHITECTURE.md` is the repo card: what this repo is, its entry points, and an index of its seams. Each seam that has a card lists it under `SEAMS → card:`, pointing at `docs/arch/<seam>.md`. Read the card before changing the code it describes.

Every card pins the file it describes with an `anchor: <path> sha=<git blob sha>` line in its frontmatter. When you change an anchored file, update the card in the same change: fix the text, set `updated:`, and replace the sha with `git hash-object <path>`. To add a seam card, add its line to the `SEAMS → card:` index too.

Check before you push:

```
python3 scripts/check_arch_cards.py
```

It fails when an anchored file has changed without its card, or when a card and the index disagree.
