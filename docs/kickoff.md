# Argus - Session Kickoff Prompt

> Paste the block below as your first message to Claude Code when starting work in this repo.
> Last updated 2026-07-13 (post v0.1.0; Phase 2 complete: #42-#46 shipped, #45 skills
> live-evaled, #47 resolved).

---

You're working in **tenishai/argus** - the observability arm of gojira. Read `CLAUDE.md` first,
then skim `docs/design.md` (decisions tables + resolved questions are current),
`docs/transport-and-routing.md`, and `CHANGELOG.md` [Unreleased] for the latest state.

**Where the project stands:**
- **v0.1.0 shipped** - Phase 1 (Observe) complete and live-proven: `argus platform` stands up the
  solo topology (pinned Helm components via the drift-aware step engine), `argus observe` wires a
  gojira app by class, and the kind smoke test (`make smoke`, tests/smoke/) asserts a 7-point DoD
  through the Perses proxies (metric/logs/trace, dashboard, Sloth SLO rules evaluated by the Mimir
  ruler, redis autodiscovered by label, ruler groups synced).
- **Phase 2 nearly done**: #42 rightsizing feedback (live-proven against a local gojira - evidence
  in, open sizing recommendation out), #43 integrations autodiscovery, #44 cardinality budgets +
  ruler sync, #46 helm drift detection - all merged.
- **Test layers**: `make test` (lint + unit + contract incl. golden files), `make validate`
  (rendered artifacts judged by real validators: otelcol/sloth/kubeconform/percli),
  `make validate-helm` (pinned charts template with our values), `make smoke` (the DoD gate).
  Golden regeneration is `make regen-golden`, reviewed as a PR diff.

**Open work (check `gh issue list` - it is the source of truth):**
- **#52 ACTED ON (Option A), awaiting Shaz**: gojira-sizing-agent is the single evidence
  producer (decided on live interop evidence in the issue). Argus's side is done: `argus
  feedback` is diagnostic-only (`--dry-run` required, POST path deleted) and the upstream
  patch (PROMETHEUS_HEADERS + Argus Mimir recipe, verified git-am onto their main) waits in
  docs/proposals/. Remaining: Shaz reviews/applies the patch, then close #52. If he reshapes
  the header option, only the README recipe here needs to track it.
- **#57**: rules-sync is upsert-only - disabled/renamed SLO groups keep evaluating in the
  ruler forever (found live by the #45 slo eval; fix is reconcile-delete in
  agent/argus/feedback/ruler.py plus a smoke assertion that disabled groups are ABSENT).
- **#53**: Phase 3 candidate - closed-loop telemetry value attribution (Chronosphere-style
  utility scoring in git; the research is in the issue).
- Done and closed, for context: **#45** (Phase 2 skills, 18-run eval program - evidence on the
  issue) and **#47** (exemplar verdict: pipeline carries exemplars end to end and the smoke DoD
  asserts it; Perses 0.53.1 UI cannot render them - design.md resolved question 15). Loose
  threads from the eval program worth a look: metric_labels_strip renders only at gateway
  (silent no-op at other stages - schema-reject or implement); hostmetrics duplicates
  node-exporter (drop rule currently hides the source question, note in baseline.yaml).

**Hard rules that bite:** no em dashes in prose (linted); pin everything + verify latest-stable at
introduction (versions.md); write to argus (and ladon if asked) ONLY - all other tenishai repos
are read-only (pull/read/run locally is fine; gojira especially is a coworker's active repo);
check `make test` exit codes before committing; merges allowed once PR checks are non-empty
all-pass.

**Family integration mechanisms to USE, not reinvent** (verified 2026-07-12): techne v0.9
deposit-and-drain for the ledger-to-memory migration; requirements-sources.txt git-tag pinning
when depending on techne/limenal; daedalus's published canonical bundle schema for the agent
shell; limenal capability-card scope maps for the future argus handler.

**To start:** run `gh issue list`, confirm the state above matches, and continue with the highest
item the user names (default order: #52 if Shaz has replied -> #57 -> #53).
