"""POST the evidence to gojira's /api/v1/usage (Bearer <cluster-scoped token>)."""

import json
import urllib.request
from collections.abc import Callable

from argus.feedback.models import UsageProfile

Http = Callable[[str, bytes, dict], tuple[int, str]]
"""(url, body, headers) -> (status, response_text)."""


class GojiraPostError(RuntimeError):
    pass


def _urllib_http(url: str, data: bytes, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def post_profile(
    base_url: str, token: str, profile: UsageProfile, http: Http = _urllib_http
) -> int:
    url = base_url.rstrip("/") + "/api/v1/usage"
    status, body = http(
        url,
        profile.serialize().encode(),
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    if status != 201:
        raise GojiraPostError(f"gojira rejected the profile: {status} {body[:300]}")
    return json.loads(body)["profile_id"]
