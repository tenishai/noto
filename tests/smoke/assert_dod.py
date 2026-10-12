#!/usr/bin/env python3
"""Assert the Phase 1 definition of done through the Perses datasource proxies.

Everything is judged through Perses (the single pane): a real metric from the sample
app queryable from Mimir, a real trace from Tempo, real logs from Loki, the default
dashboard present, and the SLO recording rules present in the cluster. Polls with a
deadline because telemetry needs a scrape/flush cycle.
"""

import json
import subprocess
import time
import urllib.parse
import urllib.request

PROXY = "http://localhost:8080/proxy/projects/noto-smoke/datasources"
DEADLINE = time.time() + 15 * 60


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp)


def poll(name: str, fn) -> None:
    while True:
        try:
            if fn():
                print(f"  PASS {name}")
                return
            reason = "empty result"
        except Exception as e:
            reason = str(e)[:120]
        if time.time() > DEADLINE:
            raise SystemExit(f"  FAIL {name}: {reason}")
        time.sleep(15)


def metric_from_app() -> bool:
    q = urllib.parse.quote('sum(rate(http_requests_total{job="podinfo"}[5m]))')
    data = get(f"{PROXY}/mimir/api/v1/query?query={q}")
    result = data["data"]["result"]
    return bool(result) and float(result[0]["value"][1]) > 0


def trace_from_app() -> bool:
    q = urllib.parse.quote('{resource.service.name="podinfo"}')
    data = get(f"{PROXY}/tempo/api/search?q={q}&limit=3")
    return bool(data.get("traces"))


def logs_flowing() -> bool:
    q = urllib.parse.quote('{k8s_namespace_name="sample"}')
    data = get(f"{PROXY}/loki/loki/api/v1/query_range?query={q}&limit=5")
    return bool(data["data"]["result"])


def dashboard_present() -> bool:
    data = get("http://localhost:8080/api/v1/projects/noto-smoke/dashboards")
    return any(d["metadata"]["name"] == "web-red" for d in data)


def ruler_evaluating() -> bool:
    """#44: the ruler holds and evaluates our synced groups (SLOs + budget alerts)."""
    data = get(f"{PROXY}/mimir/api/v1/rules")
    groups = [g for grp in data["data"]["groups"] for g in [grp["name"]]]
    return "noto-cardinality" in groups and any("sloth" in g or "slo" in g for g in groups)


def redis_autodiscovered() -> bool:
    """#43 acceptance: the labeled redis pod's metrics appear with zero config."""
    q = urllib.parse.quote('redis_clients_connected{k8s_namespace_name="sample"}')
    data = get(f"{PROXY}/mimir/api/v1/query?query={q}")
    return bool(data["data"]["result"])


def exemplars_stored() -> bool:
    """#47: the OTLP path carries exemplars end to end (SDK -> agent -> gateway -> Mimir),
    queryable through the Perses proxy - everything a UI click-through needs, server-side.
    (Perses 0.53.1 cannot render them; docs/spikes/perses/RESULTS.md addendum.)"""
    end = int(time.time())
    data = get(
        f"{PROXY}/mimir/api/v1/query_exemplars"
        f"?query=demo_request_duration_bucket&start={end - 900}&end={end}"
    )
    series = data.get("data") or []
    return any("trace_id" in ex.get("labels", {}) for s in series for ex in s.get("exemplars", []))


def slo_rules_present() -> bool:
    out = subprocess.run(
        [
            "kubectl",
            "--context",
            "kind-noto-smoke",
            "-n",
            "sample",
            "get",
            "prometheusrules",
            "-o",
            "name",
        ],
        capture_output=True,
        text=True,
    ).stdout
    return "sloth-slo" in out or "noto-slo" in out


def main() -> None:
    print("asserting the Phase 1 DoD (polling, 15m deadline):")
    poll("dashboard-as-code present (web-red)", dashboard_present)
    poll("SLO rules generated + applied", slo_rules_present)
    poll("real metric from the app via Perses->Mimir", metric_from_app)
    poll("real logs via Perses->Loki", logs_flowing)
    poll("real trace from the app via Perses->Tempo", trace_from_app)
    poll("redis autodiscovered via integration label (zero config)", redis_autodiscovered)
    poll("ruler evaluating synced groups (SLO + cardinality budget)", ruler_evaluating)
    poll("exemplars stored + queryable via Perses proxy (trace_id attached)", exemplars_stored)
    print("definition of done: MET")


if __name__ == "__main__":
    main()
