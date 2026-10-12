"""The cardinality report: top metric names by series count, per tenant.

Mimir's cardinality API does the heavy lifting; this is the noto-cardinality
skill's substrate and the human answer to "what's blowing up our series count".
"""

import json
import urllib.parse
import urllib.request


def top_metric_names(mimir_base: str, tenant: str, limit: int = 15) -> list[tuple[str, int]]:
    params = urllib.parse.urlencode({"label_names[]": "__name__", "limit": limit})
    req = urllib.request.Request(
        f"{mimir_base.rstrip('/')}/api/v1/cardinality/label_values?{params}",
        headers={"X-Scope-OrgID": tenant},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    labels = data.get("labels", [])
    if not labels:
        return []
    return [(c["label_value"], c["series_count"]) for c in labels[0].get("cardinality", [])]
