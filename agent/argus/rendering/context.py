"""Context: everything a renderer needs to know beyond the profile.

This is the seam the gojira-reader (#19) fills; #17 constructs it from fixtures.
Tenant defaults to the cluster name (tenant-per-cluster decision). Endpoint defaults
cover the solo topology; regional deployments supply their region's shared endpoints.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# The helm-chart service names (helm/pins.yaml releases are named argus-<component>).
SOLO_DEFAULTS = {
    "mimir_otlp_http": "http://argus-mimir-gateway.argus.svc:80/otlp",
    "loki_otlp_http": "http://argus-loki.argus.svc:3100/otlp",
    "tempo_otlp_grpc": "argus-tempo.argus.svc:4317",
}


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

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if not data.get("tenant"):
                data["tenant"] = data.get("cluster")
            if data.get("endpoints") is None and data.get("topology") == "solo":
                data["endpoints"] = dict(SOLO_DEFAULTS)
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
