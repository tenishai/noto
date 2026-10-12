"""The rightsizing feedback loop: measure in Mimir, distill, diagnose.

The production half of the loop lives in gojira-sizing-agent (issue #52, Option A):
gojira deploys it, it measures every managed app and POSTs the evidence to gojira,
gojira opens the reviewed rightsizing PR. Noto keeps the tenant-aware measuring
half as `noto feedback --dry-run`, a diagnostic that prints the distilled profile
for comparison against the agent's, and still owns ruler sync and cardinality.
"""
