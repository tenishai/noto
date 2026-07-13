# agent/skills/

Argus Agent Skills in the Anthropic format (one directory per skill, each with a `SKILL.md`).
Phase 1: `argus-platform`, `argus-observe-app`, `argus-pipeline`. Phase 2 (the judgment layer
over the same surfaces): `argus-slo`, `argus-dashboard`, `argus-cardinality`. Authored and
evaluated via the skill-creator workflow; each skill's eval prompts live in its
`evals/evals.json` (run with/without the skill against a live kind cluster, issue #45).
