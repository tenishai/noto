# Eval prompts for noto-observe-app

Run with-skill vs baseline per the skill-creator workflow; graded during the Phase 1
smoke work (#24) where a live cluster exists to run against.

1. Trigger accuracy: does the description fire on its natural phrasings and stay quiet
   on the other two skills' phrasings?
2. A realistic task transcript exercising the happy path end to end.
3. A failure-mode transcript (helm error / missing justification / hand-edit request)
   verifying the skill's guardrail language is followed.
