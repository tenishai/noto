@AGENTS.md

# Claude Code specifics

Everything that applies to any agent is in AGENTS.md. What follows is only for Claude Code.

## Skills to use when building this repo

Invoke these via the Skill tool (or `/skill-name`) at the relevant moment. When a task matches a
skill below, use it before hand-rolling the work.

| Skill | Use it when... | Relevance |
|---|---|---|
| **doc-coauthoring** | Writing or revising any doc, proposal, design spec, or decision record (the design doc, runbooks, ADRs). | Core - we are documentation-heavy. |
| **claude-api** | Writing or reviewing any agent code that calls the Claude API / Anthropic SDK, defines tools, or migrates models. Read it before answering LLM/model/pricing questions from memory. | Core - the Argus agent is an LLM app. |
| **mcp-builder** | Building any MCP server (e.g. exposing Argus operations as MCP tools, or the flight-recorder local server). Python (FastMCP) or Node/TS (MCP SDK). | Core when we add MCP surfaces. |
| **webapp-testing** | Verifying any local web behavior with Playwright (e.g. Perses provisioning, a future status page, dashboard round-trip checks). | When a web surface or Perses check exists. |
| **frontend-design** | Designing any web UI surface so it is distinctive and intentional, not templated (e.g. a status page, docs site, dashboard theming). | When/if we build a web surface. |
| **web-artifacts-builder** | Prototyping elaborate multi-component web artifacts (React/Tailwind/shadcn) - e.g. dashboard mockups or an internal console prototype. | Prototyping only; not a core surface. |
| **web-design-guidelines** (Vercel) | Reviewing/auditing any UI code we build against 100+ rules across 11 categories (a11y, focus states, forms, animation, typography, images, i18n, performance). Triggers: "review my UI", "check accessibility", "audit design". | Apply to any web surface we ship. Installed project-local (see below). |
| **skill-creator** | Authoring or evaluating Argus's own Agent Skills (`agent/skills/`): write SKILL.md, draft eval prompts, run with/without, optimize the description, package. | Core - we author skills here. |

Argus's own skills live in [agent/skills/](agent/skills/) and follow the Anthropic format
(lowercase-hyphenated dir = `name`; `SKILL.md` under 500 lines with `name` + `description` frontmatter;
progressive disclosure to `references/`/`scripts/`/`assets/`; imperative voice; `license: AGPL-3.0-or-later`).

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
