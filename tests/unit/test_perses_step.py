"""PersesClient and the ProvisionPerses step, against an injected opener fake -
no sockets, ever. Preserves tests/smoke/provision_perses.py's proven behavior: POST
then PUT on 409, project before datasources before dashboards.
"""

import json
import urllib.error
import urllib.request

import pytest

from argus.perses_client import PersesClient, PersesError
from argus.steps import ProvisionPerses, StepContext


class FakeOpener:
    """Replays one (status, body) response per call, in order; records every request."""

    def __init__(self, responses: list[tuple[int, bytes]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, bytes | None]] = []

    def __call__(self, req: urllib.request.Request) -> tuple[int, bytes]:
        body = req.data
        self.calls.append((req.get_method(), req.full_url, body))
        if not self.responses:
            raise AssertionError(f"no fake response left for {req.get_method()} {req.full_url}")
        return self.responses.pop(0)


def _doc(name: str, project: str | None, kind: str, spec: dict) -> dict:
    metadata = {"name": name}
    if project is not None:
        metadata["project"] = project
    return {"kind": kind, "metadata": metadata, "spec": spec}


# --- PersesClient ---


def test_get_returns_none_on_404() -> None:
    opener = FakeOpener([(404, b"not found")])
    client = PersesClient("http://p:8080", opener=opener)
    assert client.get("/api/v1/projects/c1") is None


def test_get_returns_decoded_body() -> None:
    body = json.dumps({"kind": "Project", "metadata": {"name": "c1"}}).encode()
    opener = FakeOpener([(200, body)])
    client = PersesClient("http://p:8080", opener=opener)
    assert client.get("/api/v1/projects/c1") == {"kind": "Project", "metadata": {"name": "c1"}}


def test_upsert_creates_on_404_then_post_200() -> None:
    doc = _doc("c1", None, "Project", {"display": {"name": "x"}})
    opener = FakeOpener([(404, b""), (200, b"{}")])
    client = PersesClient("http://p:8080", opener=opener)
    assert client.upsert("/api/v1/projects", doc) == "created"
    methods = [m for m, _u, _b in opener.calls]
    assert methods == ["GET", "POST"]


def test_upsert_falls_back_to_put_on_409_conflict() -> None:
    doc = _doc("c1", None, "Project", {"display": {"name": "x"}})
    opener = FakeOpener([(404, b""), (409, b"exists"), (200, b"{}")])
    client = PersesClient("http://p:8080", opener=opener)
    assert client.upsert("/api/v1/projects", doc) == "updated"
    methods = [m for m, _u, _b in opener.calls]
    assert methods == ["GET", "POST", "PUT"]
    put_url = opener.calls[2][1]
    assert put_url == "http://p:8080/api/v1/projects/c1"


def test_upsert_puts_when_spec_differs_from_server() -> None:
    existing = _doc("c1", None, "Project", {"display": {"name": "old"}})
    doc = _doc("c1", None, "Project", {"display": {"name": "new"}})
    opener = FakeOpener([(200, json.dumps(existing).encode()), (200, b"{}")])
    client = PersesClient("http://p:8080", opener=opener)
    assert client.upsert("/api/v1/projects", doc) == "updated"
    methods = [m for m, _u, _b in opener.calls]
    assert methods == ["GET", "PUT"]


def test_upsert_is_unchanged_when_spec_matches_and_sends_nothing_else() -> None:
    doc = _doc("c1", None, "Project", {"display": {"name": "same"}})
    existing = {
        "kind": "Project",
        "metadata": {"name": "c1", "createdAt": "t0", "updatedAt": "t1", "version": 3},
        "spec": {"display": {"name": "same"}},
    }
    opener = FakeOpener([(200, json.dumps(existing).encode())])
    client = PersesClient("http://p:8080", opener=opener)
    assert client.upsert("/api/v1/projects", doc) == "unchanged"
    assert len(opener.calls) == 1  # only the GET; no POST/PUT sent


def test_server_error_raises_perses_error_with_status_and_one_line_excerpt() -> None:
    body = ("boom " * 100 + "\nsecond line").encode()
    opener = FakeOpener([(500, body)])
    client = PersesClient("http://p:8080", opener=opener)
    with pytest.raises(PersesError) as exc_info:
        client.get("/api/v1/projects/c1")
    message = str(exc_info.value)
    assert "500" in message
    assert "\n" not in message
    excerpt_source = body.decode()[:200]
    assert " ".join(excerpt_source.split()) in message


def test_transport_error_raises_perses_error_naming_base_url() -> None:
    def raising_opener(req: urllib.request.Request) -> tuple[int, bytes]:
        raise urllib.error.URLError("connection refused")

    client = PersesClient("http://p:8080", opener=raising_opener)
    with pytest.raises(PersesError) as exc_info:
        client.get("/api/v1/projects/c1")
    assert "http://p:8080" in str(exc_info.value)


# --- ProvisionPerses step ---


def _rendered_docs() -> list[dict]:
    return [
        _doc("c1", None, "Project", {"display": {"name": "Argus · c1"}}),
        _doc("mimir", "c1", "Datasource", {"default": True}),
        _doc("loki", "c1", "Datasource", {"default": True}),
        _doc("c1-agent-health", "c1", "Dashboard", {"duration": "1h"}),
    ]


def _ctx(opener, docs=None, dry_run: bool = False) -> StepContext:
    return StepContext(
        runner=lambda *_a, **_kw: (0, ""),
        rendered={"perses": docs if docs is not None else _rendered_docs()},
        dry_run=dry_run,
        perses_url="http://p:8080",
        perses_opener=opener,
    )


def test_apply_counts_created_updated_unchanged() -> None:
    docs = _rendered_docs()
    responses = []
    for i, doc in enumerate(docs):
        if i == 0:  # project: exists, unchanged
            existing = {**doc, "metadata": {**doc["metadata"], "version": 1}}
            responses.append((200, json.dumps(existing).encode()))
        elif i == 1:  # mimir datasource: missing -> created
            responses.append((404, b""))
            responses.append((200, b"{}"))
        elif i == 2:  # loki datasource: exists but different -> updated
            existing = {**doc, "spec": {"default": False}}
            responses.append((200, json.dumps(existing).encode()))
            responses.append((200, b"{}"))
        else:  # dashboard: missing -> created
            responses.append((404, b""))
            responses.append((200, b"{}"))
    opener = FakeOpener(responses)
    ctx = _ctx(opener, docs=docs)
    step = ProvisionPerses()
    result = step.apply(ctx)
    assert result == "perses: 2 created, 1 updated, 1 unchanged"


def test_apply_upserts_project_before_datasources_before_dashboards() -> None:
    docs = _rendered_docs()
    # every doc: missing -> POST created
    responses = []
    for _ in docs:
        responses.append((404, b""))
        responses.append((200, b"{}"))
    opener = FakeOpener(responses)
    ctx = _ctx(opener, docs=docs)
    ProvisionPerses().apply(ctx)
    posted_paths = [url for method, url, _b in opener.calls if method == "POST"]
    assert posted_paths == [
        "http://p:8080/api/v1/projects",
        "http://p:8080/api/v1/projects/c1/datasources",
        "http://p:8080/api/v1/projects/c1/datasources",
        "http://p:8080/api/v1/projects/c1/dashboards",
    ]


def test_check_returns_skip_reason_only_when_every_doc_unchanged() -> None:
    docs = _rendered_docs()
    responses = [(200, json.dumps({**d, "spec": d["spec"]}).encode()) for d in docs]
    opener = FakeOpener(responses)
    ctx = _ctx(opener, docs=docs)
    reason = ProvisionPerses().check(ctx)
    assert reason is not None
    assert "perses" in reason


def test_check_returns_none_when_any_doc_differs() -> None:
    docs = _rendered_docs()
    responses = []
    for i, d in enumerate(docs):
        if i == len(docs) - 1:
            responses.append((404, b""))  # dashboard missing -> not unchanged
        else:
            responses.append((200, json.dumps(d).encode()))
    opener = FakeOpener(responses)
    ctx = _ctx(opener, docs=docs)
    assert ProvisionPerses().check(ctx) is None


def test_dry_run_sends_nothing_and_describes_the_plan() -> None:
    docs = _rendered_docs()

    def exploding_opener(req: urllib.request.Request) -> tuple[int, bytes]:
        raise AssertionError("dry-run must send nothing")

    ctx = StepContext(
        runner=lambda *_a, **_kw: (0, ""),
        rendered={"perses": docs},
        dry_run=True,
        perses_url="http://p:8080",
        perses_opener=exploding_opener,
    )
    result = ProvisionPerses().apply(ctx)
    assert "would" in result
    assert "4" in result


def test_dry_run_check_sends_nothing_either() -> None:
    """`--dry-run` is the mode that must work with nothing reachable at all - a laptop
    with no port-forward, a preview of a cluster that is not up yet. check() used to ask
    Perses over real HTTP even here, so a dry run failed on a connection error before it
    ever reached the apply() that describes the plan."""

    def exploding_opener(req: urllib.request.Request) -> tuple[int, bytes]:
        raise AssertionError("dry-run must send nothing")

    ctx = StepContext(
        runner=lambda *_a, **_kw: (0, ""),
        rendered={"perses": _rendered_docs()},
        dry_run=True,
        perses_url="http://p:8080",
        perses_opener=exploding_opener,
    )
    assert ProvisionPerses().check(ctx) is None


def test_check_then_apply_asks_perses_about_each_document_once() -> None:
    """check() already fetched every document; apply() reuses what it saw instead of
    fetching the same documents a second time. One GET per document per run, not two."""
    docs = _rendered_docs()
    responses = [(200, json.dumps(d).encode()) for d in docs[:-1]]
    responses.append((404, b""))  # the dashboard is missing, so check() cannot skip
    responses.append((200, b"{}"))  # the POST that creates it
    opener = FakeOpener(responses)
    ctx = _ctx(opener, docs=docs)
    step = ProvisionPerses()

    assert step.check(ctx) is None
    result = step.apply(ctx)

    assert result == "perses: 1 created, 0 updated, 3 unchanged"
    gets = [url for method, url, _b in opener.calls if method == "GET"]
    assert len(gets) == len(docs)
    assert len(gets) == len(set(gets))
    assert [m for m, _u, _b in opener.calls if m != "GET"] == ["POST"]


def test_a_document_check_never_saw_is_still_fetched_by_apply() -> None:
    """check() stops at the first drifted document, so apply() inherits a partial view -
    every document it was not told about must still be asked for."""
    docs = _rendered_docs()
    opener = FakeOpener(
        [
            (404, b""),  # check(): the project is missing -> stop right there
            (200, b"{}"),  # apply(): the project's POST, straight from the cache
            *[r for _d in docs[1:] for r in ((404, b""), (200, b"{}"))],
        ]
    )
    ctx = _ctx(opener, docs=docs)
    step = ProvisionPerses()

    assert step.check(ctx) is None
    assert step.apply(ctx) == "perses: 4 created, 0 updated, 0 unchanged"
    # one GET for the cached project (check's), three for the documents check never saw
    assert len([m for m, _u, _b in opener.calls if m == "GET"]) == 4


def test_the_cache_does_not_outlive_the_apply_that_used_it() -> None:
    """A second run of the same step object must re-ask Perses: the world moved on
    between runs, and a stale cache would silently skip a drifted dashboard."""
    docs = [_doc("c1", None, "Project", {"display": {"name": "x"}})]
    opener = FakeOpener([(404, b""), (200, b"{}")])
    step = ProvisionPerses()
    assert step.check(_ctx(opener, docs=docs)) is None
    step.apply(_ctx(opener, docs=docs))

    second = FakeOpener([(404, b""), (200, b"{}")])
    assert step.apply(_ctx(second, docs=docs)) == "perses: 1 created, 0 updated, 0 unchanged"
    assert [m for m, _u, _b in second.calls] == ["GET", "POST"]


def test_apply_trusts_the_check_time_read_even_if_the_document_changed_since() -> None:
    """The accepted trade-off named next to `_seen`'s field docstring in steps.py:
    apply() reuses whatever check() already read rather than re-asking Perses, so a
    write that lands on the server between check() and apply() for a document check()
    already matched is invisible to THIS run - it is reported unchanged and nothing is
    sent. The next run's check() reads Perses fresh and heals it (the same healing
    `test_the_cache_does_not_outlive_the_apply_that_used_it` proves)."""
    doc = _doc("c1", None, "Project", {"display": {"name": "same"}})
    opener = FakeOpener([(200, json.dumps(doc).encode())])
    ctx = _ctx(opener, docs=[doc])
    step = ProvisionPerses()

    assert step.check(ctx) == "perses: 1 unchanged"

    # An external write lands on the server right after check() read it: if apply()
    # asked again, it would see a different spec.
    drifted = {**doc, "spec": {"display": {"name": "changed-by-someone-else"}}}
    opener.responses.append((200, json.dumps(drifted).encode()))

    result = step.apply(ctx)

    assert result == "perses: 0 created, 0 updated, 1 unchanged"
    # apply() never asked again: the drifted response above was never consumed.
    assert len(opener.responses) == 1
    assert [m for m, _u, _b in opener.calls] == ["GET"]
