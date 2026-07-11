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

PROXY = "http://localhost:8080/proxy/projects/argus-smoke/datasources"
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
    data = get("http://localhost:8080/api/v1/projects/argus-smoke/dashboards")
    return any(d["metadata"]["name"] == "web-red" for d in data)


def slo_rules_present() -> bool:
    out = subprocess.run(
        [
            "kubectl",
            "--context",
            "kind-argus-smoke",
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
    return "sloth-slo" in out or "argus-slo" in out


def main() -> None:
    print("asserting the Phase 1 DoD (polling, 15m deadline):")
    poll("dashboard-as-code present (web-red)", dashboard_present)
    poll("SLO rules generated + applied", slo_rules_present)
    poll("real metric from the app via Perses->Mimir", metric_from_app)
    poll("real logs via Perses->Loki", logs_flowing)
    poll("real trace from the app via Perses->Tempo", trace_from_app)
    print("definition of done: MET")


if __name__ == "__main__":
    main()
