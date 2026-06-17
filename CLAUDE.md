# Argus - Project Guide for Claude Code

Argus is the **observability arm of [gojira](https://github.com/shaz/gojira)**: it takes a deployment
from zero to a full, end-to-end open-source observability platform (OpenTelemetry + the LGTM stack),
automatically, driven by a self-contained agent and Agent Skills. Read [docs/design.md](docs/design.md)
before making structural changes.

## The ecosystem (Argus is one body part)

Argus is not standalone. It is one limb of a shared, opinionated SRE/agent platform - a set of sibling
repos meant to be merged into one GitHub org. Each owns one concern; Argus owns observability:

| Repo | Role | Relationship to Argus |
|---|---|---|
| [gojira](https://github.com/shaz/gojira) | Opinionated deploy platform (identity, CI/CD, manifests, sizing) | **Parent.** Argus continues gojira's lifecycle; reads its contract/state; maps its workload class to an observability profile. |
| [techne](https://github.com/dimartin/techne) | Agentic memory (sessions, knowledge, identity; SQLite) | Argus's SQLite ledger migrates into techne later, so the agent's deploy history becomes its memory. |
| [limenal](https://github.com/dimartin/limenal) | MCP gateway (one connection point dispatching to many tool handlers) | Argus reaches its tools through limenal once integrated; Argus operations can be exposed as a limenal handler. |
| [cabal](https://github.com/dimartin/cabal) | Agent orchestration / A2A broker (LLM personas) | Argus's self-contained agent is built to register into cabal as the observability persona later. |

Build Argus to run **self-contained today** (own runtime, step engine, SQLite ledger) but **compose
with these later** (register into cabal, reach tools via limenal, persist memory into techne). Do not
duplicate what a sibling owns; integrate with it.

## Hard conventions

- **No em dashes** in prose, docs, README, UI copy, or commit messages. Use a spaced hyphen ` - `,
  a comma, or parentheses. (Code and generated config are exempt.)
- **100% open source.** No vendor backend, no proprietary dependency, no phone-home. Apache-2.0.
- **Agent-first, no web UI** as a core surface. The agent is the interface.
- **gojira-native.** gojira's contract and state are the source of truth; do not duplicate them.
- **No third-party GitOps** (no ArgoCD/Flux) in the apply path. Argus applies directly via cloud
  CLI / SDK / Helm and Operator CRDs through an idempotent step engine.
- **README style** mirrors the [OpenTelemetry Collector README](https://github.com/open-telemetry/opentelemetry-collector/blob/main/README.md):
  title block, badges, nav links, Overview/objectives, How it works, Stack, Status, Docs. Keep it short.
- Pin everything (ocb component versions, Helm charts, Terraform providers). No floating `latest`.

## Skills to use when building this repo

Invoke these via the Skill tool (or `/skill-name`) at the relevant moment. When a task matches a
skill below, use it before hand-rolling the work.

| Skill | Use it when... | Relevance |
|---|---|---|
| **doc-coauthoring** | Writing or revising any doc, proposal, design spec, or decision record (the design doc, runbooks, ADRs). | Core - we are documentation-heavy. |
| **claude-api** | Writing or reviewing any agent code that calls the Claude API / Anthropic SDK, defines tools, or migrates models. Read it before answering LLM/model/pricing questions from memory. | Core - the Argus agent is an LLM app. |
| **mcp-builder** | Building any MCP server (e.g. exposing Argus operations as MCP tools, or the flight-recorder local server). Python (FastMCP) or Node/TS (MCP SDK). | Core when we add MCP surfaces. |
| **webapp-testing** | Verifying any local web behavior with Playwright (e.g. Grafana provisioning, a future status page, dashboard round-trip checks). | When a web surface or Grafana check exists. |
| **frontend-design** | Designing any web UI surface so it is distinctive and intentional, not templated (e.g. a status page, docs site, dashboard theming). | When/if we build a web surface. |
| **web-artifacts-builder** | Prototyping elaborate multi-component web artifacts (React/Tailwind/shadcn) - e.g. dashboard mockups or an internal console prototype. | Prototyping only; not a core surface. |
| **web-design-guidelines** (Vercel) | Reviewing/auditing any UI code we build against 100+ rules across 11 categories (a11y, focus states, forms, animation, typography, images, i18n, performance). Triggers: "review my UI", "check accessibility", "audit design". | Apply to any web surface we ship. Installed project-local (see below). |
| **skill-creator** | Authoring or evaluating Argus's own Agent Skills (`agent/skills/`): write SKILL.md, draft eval prompts, run with/without, optimize the description, package. | Core - we author skills here. |

Argus's own skills live in [agent/skills/](agent/skills/) and follow the Anthropic format
(lowercase-hyphenated dir = `name`; `SKILL.md` under 500 lines with `name` + `description` frontmatter;
progressive disclosure to `references/`/`scripts/`/`assets/`; imperative voice; `license: Apache-2.0`).

## Setup: making the skills available

The Anthropic skills are distributed through the Claude Code plugin marketplace:

```
/plugin marketplace add anthropics/skills
/plugin install example-skills@anthropic-agent-skills
/plugin install document-skills@anthropic-agent-skills
```

(See the [anthropics/skills README](https://github.com/anthropics/skills) for the current bundle names;
`skill-creator`, `frontend-design`, `mcp-builder`, `webapp-testing`, `web-artifacts-builder`,
`doc-coauthoring`, and `claude-api` ship in those bundles.)

The Vercel **web-design-guidelines** skill is installed **project-local** (it travels with the repo),
from [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) (MIT):

```
npx skills add https://github.com/vercel-labs/agent-skills --skill web-design-guidelines
```

It lives at `.agents/skills/web-design-guidelines/` with a Claude Code symlink at
`.claude/skills/web-design-guidelines`. Both are committed so the skill is available to anyone working
in this repo, in Claude Code and other agents.

## Repo layout

```
distro/      OTel Collector distribution (ocb manifest + build)
terraform/   cloud-side resources: buckets, IAM/WIF (Terraform/SDK)
helm/        pinned Helm values for in-cluster components
crds/        OTel/Prometheus Operator CRDs (collectors, ServiceMonitors)
profiles/    observability profiles mapped from gojira workload class
content/     dashboards, recording rules, SLOs, alert packs per profile
agent/       self-contained agent: step engine, gojira-reader, renderers
  skills/    Argus Agent Skills (Anthropic format)
docs/        design doc, enablement guide, platform evolution
```
