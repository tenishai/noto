# Argus - Session Kickoff Prompt

> Paste the block below as your first message to Claude Code when starting work in this repo.

---

You're working in the **argus** repo. Argus is the **observability arm of gojira** - it takes a gojira
deployment from zero to a full, end-to-end, 100% open-source observability platform, automatically. It
is NOT a standalone "gojira of observability"; the input is a gojira deployment and the output is that
same deployment fully observed.

Argus is one body part of a shared, opinionated SRE/agent platform - sibling repos meant to merge into
one GitHub org, each owning one concern:
- **gojira** (https://github.com/shaz/gojira) - deploy platform. PARENT: Argus continues its lifecycle
  and maps its workload class to an observability profile.
- **techne** (https://github.com/dimartin/techne) - agentic memory. Argus's SQLite ledger migrates here
  later (deploy history becomes memory).
- **limenal** (https://github.com/dimartin/limenal) - MCP gateway. Argus reaches tools through it; Argus
  ops can be exposed as a limenal handler.
- **cabal** (https://github.com/dimartin/cabal) - agent orchestration / personas. Argus's agent registers
  here later as the observability persona.

Build Argus self-contained today (own runtime, step engine, SQLite ledger) but composable with these
later. Do not duplicate what a sibling owns; integrate with it.

**First, read these in order before doing anything:**
1. `CLAUDE.md` - repo conventions and the skills you must use
2. `docs/design.md` - the full design plan (architecture, decisions, deployment model, phased roadmap)
3. `docs/otel-collector-enablement-guide.md` - the collector patterns and pipeline opinions Argus ships
   as defaults
4. `docs/observability-platform-design.md` - the agent topology and "integrations that just work" north
   star

**Hard conventions (from CLAUDE.md):**
- No em dashes in prose/docs/commits (use spaced hyphens).
- 100% open source, Apache-2.0, no vendor backend, no phone-home.
- Agent-first, no web UI as a core surface.
- gojira-native: gojira's contract/state is the source of truth, don't duplicate it.
- No third-party GitOps (no ArgoCD/Flux). Apply directly via cloud CLI / SDK / Helm + Operator CRDs
  through an idempotent step engine.
- Pin everything (ocb components, Helm charts, Terraform providers).
- README style mirrors the OpenTelemetry Collector README.

**The stack we target:** OTel Collector (custom `ocb` distro) for collection - Agent (DaemonSet) +
Gateway (Deployment) + TargetAllocator (StatefulSet) - into Mimir (metrics), Loki (logs), Tempo
(traces), with Perses for visualization and Alertmanager for alerting; cluster sources
kube-state-metrics + node-exporter + kubelet/cAdvisor.
The opinion is **observability profiles mapped from gojira workload class** (web->HTTP RED, worker->queue,
cron->job health, heavy->USE, gpu->accelerator). Decisions: per-cluster self-hosted backends, SQLite
ledger now (techne later), self-contained agent in this repo, GKE/GCP first.

**Skills loaded - use them:**
- `skill-creator` - author/eval Argus's own skills in `agent/skills/`
- `doc-coauthoring` - any doc/spec/ADR work
- `claude-api` - any agent code that calls the Claude API
- `mcp-builder` - any MCP server (incl. the Phase-4 flight-recorder)
- `webapp-testing`, `frontend-design`, `web-artifacts-builder`, `web-design-guidelines` (Vercel,
  project-local) - only if/when we build a web surface

**Repo layout (already scaffolded with stub READMEs):** `distro/`, `terraform/`, `helm/`, `crds/`,
`profiles/`, `content/`, `agent/skills/`, `docs/`.

**Phased roadmap:** Phase 1 "Observe" (ocb distro, profiles, step engine, gojira-reader,
`argus-platform`/`argus-observe-app`/`argus-pipeline` skills, default dashboards/SLOs, synthetic
round-trip validation) -> Phase 2 "Curate" (declarative integrations catalog + autodiscovery) -> Phase 3
"Guard" (policy enforcement + drift->PR) -> Phase 4 "Reason" (runtime-context flight recorder).

**To start:** confirm you've read the four files and understand the design, then propose a concrete
Phase-1 plan. I want to begin with either (a) the first `argus-platform` SKILL.md authored via
`skill-creator`, or (b) pinning the Phase-1 `ocb` distro manifest in `distro/`. Recommend an order and
let's go.
