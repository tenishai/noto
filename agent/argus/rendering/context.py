"""Context: everything a renderer needs to know beyond the profile.

This is the seam the gojira-reader (#19) fills; #17 constructs it from fixtures.
Tenant defaults to the cluster name (tenant-per-cluster decision). Endpoint defaults
cover the solo topology; regional deployments supply their region's shared endpoints.

Argus was always run by an operator through port-forwards, so `localhost` used to be
the only sane endpoint default. Running in-cluster (the job-mode path) changes that:
the platform's own Service DNS names, in the platform's own namespace, are the right
default there. `solo_endpoints()` is the one place that host:port list is spelled out;
everything else (the `Context` default below, and the CLI's `--mimir-url`/
`--perses-url` derivation) calls it rather than re-deriving the strings.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# The Endpoints model only carries the OTLP ingest side (what the collector tier
# forwards to); the query-side URLs (mimir_query_url, perses_url) are CLI-flag
# defaults, not part of the renderers' Context.
_INGEST_KEYS = ("mimir_otlp_http", "loki_otlp_http", "tempo_otlp_grpc")


def solo_endpoints(namespace: str = "argus") -> dict[str, str]:
    """The in-cluster solo-topology host:port targets for one platform namespace.

    Service names verified against the helm chart templates for the pinned versions
    in `helm/pins.yaml` (release names are `argus-<component>`, per that file's
    comment) and cross-checked against `tests/smoke/run-smoke.sh`'s port-forward
    targets: `argus-mimir-gateway` (mimir-distributed's gateway service, port 80),
    `argus-loki` (the SingleBinary service the loki chart names after the release,
    port 3100 - the loki gateway is disabled in solo, so this is the direct target),
    `argus-tempo` (the monolithic tempo chart's one service, port 4317 for its OTLP
    gRPC receiver), and `argus-perses` (port 8080). All five matched the spec
    unchanged; nothing here needed correcting against the chart.

    `alertmanager_url` targets `alertmanager-operated`, the operator's own governing
    Service, rather than the helm release's Alertmanager service - the release's own
    service name (`argus-kube-prometheus-stac-alertmanager`) is truncated by the chart
    at 63 characters, exactly the kind of name not to hardcode.
    """
    return {
        "mimir_otlp_http": f"http://argus-mimir-gateway.{namespace}.svc:80/otlp",
        "loki_otlp_http": f"http://argus-loki.{namespace}.svc:3100/otlp",
        "tempo_otlp_grpc": f"argus-tempo.{namespace}.svc:4317",
        "mimir_query_url": f"http://argus-mimir-gateway.{namespace}.svc:80/prometheus",
        "perses_url": f"http://argus-perses.{namespace}.svc:8080",
        "alertmanager_url": f"http://alertmanager-operated.{namespace}.svc:9093",
    }


# Back-compat identity every golden file relies on: `solo_endpoints("argus")`, not a
# hand-maintained copy of it. Keep this equality exact - see `test_context.py`.
SOLO_DEFAULTS = solo_endpoints("argus")


class Endpoints(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mimir_otlp_http: str
    loki_otlp_http: str
    tempo_otlp_grpc: str
    kafka_bootstrap: str | None = None


class Context(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    topology: Literal["solo", "regional"]
    cluster: str
    tenant: str = ""
    app: str | None = None
    namespace: str | None = None
    environment: Literal["dev", "qa", "prod"]
    region: str | None = None
    endpoints: Endpoints | None = None
    # The namespace the observability PLATFORM itself lives in (argus-mimir,
    # argus-loki, ...) - unrelated to `namespace` above, which is the app's own.
    # Only consulted when `endpoints` is not supplied for a solo topology; an
    # explicit `endpoints=` always wins, and regional topologies ignore it entirely
    # (their endpoints are always explicit).
    platform_namespace: str = "argus"

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if not data.get("tenant"):
                data["tenant"] = data.get("cluster")
            if data.get("endpoints") is None and data.get("topology") == "solo":
                ns = data.get("platform_namespace") or "argus"
                full = solo_endpoints(ns)
                data["endpoints"] = {k: full[k] for k in _INGEST_KEYS}
        return data

    @model_validator(mode="after")
    def _topology_requirements(self) -> Context:
        if self.topology == "regional":
            if not self.region:
                raise ValueError("regional topology requires region")
            if self.endpoints is None or not self.endpoints.kafka_bootstrap:
                raise ValueError("regional topology requires endpoints.kafka_bootstrap")
        if self.endpoints is None:
            raise ValueError("endpoints could not be defaulted; supply them explicitly")
        return self
