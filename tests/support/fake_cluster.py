"""A fake runner that also holds the platform's own record.

`tests/unit/test_cli.py`'s FakeRunner answers every probe the same way; this one adds the
one thing the platform record tests turn on: a test can say "the platform was born as
tenant X" (or "there is no record at all") and watch what `platform` and `observe` do
about it.
"""

import json

import yaml

# tests/unit/fixtures/gojira/web.yaml's dev environment cluster string.
CONTRACT_CLUSTER = "dev-cluster"

# A platform that is already born, writing as a tenant the contract above does not name.
RECORDED = {"born": "2026-01-01T00:00:00Z", "tenant": "real-name", "environment": "dev"}


CONFIGMAP = "argus-platform-info"
_NOT_FOUND = f'Error from server (NotFound): configmaps "{CONFIGMAP}" not found'
_ALREADY_EXISTS = f'Error from server (AlreadyExists): configmaps "{CONFIGMAP}" already exists'


class InfoRunner:
    """Stateful about the record only: a `kubectl create`/`patch` of it lands in `info`,
    so a test can run `platform` twice and ask what the second run did to the first run's
    birth instant. Everything else answers the same way `FakeRunner` does."""

    def __init__(self, info: dict[str, str] | None = None, readable: bool = True) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.info = info
        self.readable = readable

    def __call__(self, cmd: list[str], stdin_text: str | None) -> tuple[int, str]:
        self.calls.append((cmd, stdin_text))
        joined = " ".join(cmd)
        if f"get configmap {CONFIGMAP}" in joined:
            if not self.readable:
                return 1, "Error from server (Forbidden)"
            if self.info is None:
                return 1, _NOT_FOUND
            return 0, json.dumps({"kind": "ConfigMap", "data": self.info})
        if f"create configmap {CONFIGMAP}" in joined:
            if self.info is not None:
                return 1, _ALREADY_EXISTS
            self.info = {}
            for arg in cmd:
                if arg.startswith("--from-literal="):
                    key, _, value = arg.removeprefix("--from-literal=").partition("=")
                    self.info[key] = value
            return 0, f"configmap/{CONFIGMAP} created"
        if f"patch configmap {CONFIGMAP}" in joined:
            if self.info is None:
                return 1, _NOT_FOUND
            self.info.update(json.loads(cmd[cmd.index("-p") + 1]).get("data", {}))
            return 0, f"configmap/{CONFIGMAP} patched"
        if " get namespace" in joined or "helm status" in joined:
            return 1, "not found"
        return 0, "{}" if "-o json" not in joined else '{"items": []}'

    @property
    def applies(self) -> list[tuple[list[str], str | None]]:
        return [(c, s) for c, s in self.calls if "apply" in c]

    @property
    def creates(self) -> list[list[str]]:
        return [c for c, _s in self.calls if c[:3] == ["kubectl", "create", "configmap"]]

    @property
    def patches(self) -> list[list[str]]:
        return [c for c, _s in self.calls if c[:3] == ["kubectl", "patch", "configmap"]]

    def applied_docs(self) -> list[dict]:
        docs: list[dict] = []
        for _cmd, stdin_text in self.applies:
            docs.extend(d for d in yaml.safe_load_all(stdin_text or "") if d)
        return docs


def fake_perses_opener(req) -> tuple[int, bytes]:
    """Every GET 404s, every POST/PUT succeeds: enough for a run where ProvisionPerses is
    only riding along (its own behavior is covered by test_perses_step.py)."""
    return (404, b"") if req.get_method() == "GET" else (200, b"{}")
