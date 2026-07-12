# gojira Rightsizing Feedback Loop - Implementation Plan (issue #42)

> Compact plan (pattern of #18/#19): the contract is gojira's, verified from its code.
> POST /api/v1/usage, Bearer <cluster token>, body = UsageProfile (gojira/sizing/profile.py).
> gojira's own docstring names the cross-repo test: tests/fixtures/usage_profile.json is
> the golden fixture BOTH repos assert against - copied here verbatim.

**Design decisions:**
- Everything measured comes from Mimir (tenant-aware): cpu = rate(container_cpu_usage_seconds_total[5m]),
  memory = container_memory_working_set_bytes, declared request/limit =
  kube_pod_container_resource_{requests,limits}, restarts = increase(kube_pod_container_status_restarts_total),
  oom_kills = OOMKilled last-terminated-reason count. No kubectl in the loop.
- Percentiles computed client-side from query_range samples (5m step): p50/p95/p99/max,
  serialized as k8s quantity strings (cpu -> millicores "760m"; memory -> "300Mi").
- reported_by = "argus/<cluster>"; window default 14d (gojira caps at 90).
- Injectable HTTP poster + query function - tests never touch the network.

**Tasks (TDD):**
1. models.py: UsageProfile/ResourceUsage mirror + `serialize()` -> the EXACT golden fixture
   (cross-repo contract test: fixture in, fixture out, byte-identical JSON).
2. distill.py: samples -> percentiles -> quantity strings (table-driven: cores/bytes edges,
   rounding, empty-series error).
3. mimir.py: query_range client (X-Scope-OrgID, urlencode, parse) against fixture responses.
4. gojira_client.py: post_profile(url, token, profile, http=<injectable>) -> profile_id;
   asserts Bearer header + 201 handling + error surfacing.
5. cli.py: `argus feedback --gojira <file> --env <env> [--window-days 14] [--mimir-url] 
   [--gojira-url] [--dry-run]`; token from GOJIRA_USAGE_TOKEN env (never a flag - ps leaks).
   Dry-run prints the evidence JSON and posts nothing. Wire into main().
6. CHANGELOG + PR.

**Acceptance (issue):** dry-run produces correct evidence from fixtures; live post deferred to
a gojira dev instance session (needs their token mint - noted, not silently skipped).
