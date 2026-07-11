#!/usr/bin/env python3
"""Provision the rendered Perses docs (project, datasources, dashboards) via the API.

Perses resources are API/HTTP objects, not k8s CRDs (the chart runs plain Perses), so
`observe` does not kubectl-apply them; this posts the rendered docs, idempotently.
"""

import json
import sys
import urllib.error
import urllib.request

import yaml

BASE = "http://localhost:8080/api/v1"


def post(path: str, body: dict) -> None:
    data = json.dumps(body).encode()
    for method in ("POST", "PUT"):
        url = BASE + path if method == "POST" else BASE + path + "/" + body["metadata"]["name"]
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method=method
        )
        try:
            with urllib.request.urlopen(req) as resp:
                print(f"  {method} {path} {body['metadata']['name']}: {resp.status}")
                return
        except urllib.error.HTTPError as e:
            if method == "POST" and e.code == 409:
                continue  # exists -> PUT
            print(f"  {method} {path} failed: {e.code} {e.read().decode()[:200]}")
            raise
    raise RuntimeError("unreachable")


def main(rendered_path: str) -> None:
    with open(rendered_path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    project = next(d for d in docs if d["kind"] == "Project")
    post("/projects", project)
    pname = project["metadata"]["name"]
    for d in docs:
        if d["kind"] == "Datasource":
            post(f"/projects/{pname}/datasources", d)
        elif d["kind"] == "Dashboard":
            post(f"/projects/{pname}/dashboards", d)
    print(f"provisioned {len(docs)} perses docs into project {pname}")


if __name__ == "__main__":
    main(sys.argv[1])
