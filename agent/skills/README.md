# agent/skills/

Noto Agent Skills in the Anthropic format (one directory per skill, each with a `SKILL.md`).
Phase 1: `noto-platform`, `noto-observe-app`, `noto-pipeline`. Phase 2 (the judgment layer
over the same surfaces): `noto-slo`, `noto-dashboard`, `noto-cardinality`. Authored and
evaluated via the skill-creator workflow; each skill's eval prompts live in its
`evals/evals.json` (run with/without the skill against a live kind cluster, issue #45).
