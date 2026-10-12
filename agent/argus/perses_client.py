"""A minimal Perses HTTP client: stdlib `urllib` only, no new dependency.

Perses resources are API/HTTP objects, not k8s CRDs (the chart runs plain Perses), so
they are provisioned by POSTing (falling back to PUT on conflict) rather than
kubectl-applied. This is the same dance `tests/smoke/provision_perses.py` proved by
hand; `ProvisionPerses` (agent/argus/steps.py) is the engine step built on top of it.

The `opener` seam exists so tests never open a real socket: it takes a built
`urllib.request.Request` and returns `(status, body_bytes)`, exactly what
`urllib.request.urlopen` gives on success and what an `HTTPError` carries on failure.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable

Opener = Callable[[urllib.request.Request], tuple[int, bytes]]


class PersesError(RuntimeError):
    """A Perses call failed: a non-2xx/404 status, or the server was unreachable."""


class _Unfetched:
    """Sentinel for `upsert(existing=...)`: nobody has asked the server about this
    document yet. `None` cannot say that - it is the real answer for a document the
    server does not have."""

    def __repr__(self) -> str:
        return "<unfetched>"


UNFETCHED = _Unfetched()


def _one_line_excerpt(body: bytes, limit: int = 200) -> str:
    """The first `limit` characters of `body`, decoded and collapsed to one line -
    a raised PersesError must never carry an embedded newline."""
    text = body.decode("utf-8", errors="replace")
    return " ".join(text[:limit].split())


class PersesClient:
    def __init__(self, base_url: str, opener: Opener | None = None, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener or self._urlopen

    def _urlopen(self, req: urllib.request.Request) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _request(self, method: str, path: str, doc: dict | None = None) -> tuple[int, bytes]:
        url = self.base_url + path
        data = json.dumps(doc).encode() if doc is not None else None
        headers = {"Content-Type": "application/json"} if doc is not None else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return self._opener(req)
        except urllib.error.URLError as e:
            raise PersesError(f"perses unreachable at {self.base_url}: {e}") from e

    def _raise_for_status(self, status: int, body: bytes) -> None:
        if not (200 <= status < 300):
            raise PersesError(f"perses returned {status}: {_one_line_excerpt(body)}")

    def get(self, path: str) -> dict | None:
        """The document at `path`, or None on 404. Any other non-2xx status raises."""
        status, body = self._request("GET", path)
        if status == 404:
            return None
        self._raise_for_status(status, body)
        return json.loads(body)

    def upsert(
        self, collection_path: str, doc: dict, existing: dict | _Unfetched | None = UNFETCHED
    ) -> str:
        """Create or update `doc` in `collection_path`, matching the smoke script's
        proven dance: POST, falling back to PUT when the server says 409 (already
        exists). Skips the write entirely - returning "unchanged" - when the server
        already holds a document whose `spec` equals the one being sent (server-managed
        metadata such as createdAt/updatedAt/version is never part of that comparison,
        because metadata is never compared at all).

        `existing` is what the caller already knows the server holds (`None` for a
        document it does not have). A caller that just read the document - the
        ProvisionPerses step's check(), which reads every one of them - passes it so this
        does not read it a second time in the same run.
        """
        name = doc["metadata"]["name"]
        item_path = f"{collection_path}/{name}"
        if isinstance(existing, _Unfetched):
            existing = self.get(item_path)
        if existing is not None and existing.get("spec") == doc.get("spec"):
            return "unchanged"
        if existing is None:
            status, body = self._request("POST", collection_path, doc)
            if status == 409:  # created concurrently since the GET above - fall back
                status, body = self._request("PUT", item_path, doc)
                self._raise_for_status(status, body)
                return "updated"
            self._raise_for_status(status, body)
            return "created"
        status, body = self._request("PUT", item_path, doc)
        self._raise_for_status(status, body)
        return "updated"
