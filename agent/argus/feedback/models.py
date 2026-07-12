"""Mirror of gojira's UsageProfile wire contract (gojira/sizing/profile.py).

Quantities are Kubernetes quantity strings ("250m", "256Mi") - gojira normalizes on
its side. The shared golden fixture (usage_profile.json, identical in both repos) is
the cross-repo contract test: change the contract -> change the fixture -> both repos
notice.
"""

import json

from pydantic import BaseModel, ConfigDict, Field


class ResourceUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: str
    limit: str
    p50: str
    p95: str
    p99: str
    max: str


class UsageProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    app: str = Field(min_length=1, max_length=63)
    environment: str = Field(min_length=1, max_length=16)
    cluster: str = Field(min_length=1, max_length=63)
    window_days: int = Field(gt=0, le=90)
    samples: int = Field(gt=0)
    cpu: ResourceUsage
    memory: ResourceUsage
    restarts: int = Field(ge=0)
    oom_kills: int = Field(ge=0)
    reported_by: str = Field(min_length=1, max_length=255)
    reported_at: str

    def serialize(self) -> str:
        return json.dumps(self.model_dump(), indent=2)
