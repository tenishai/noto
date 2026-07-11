"""Parse the real gojira.yaml contract (verified against gojira's template).

The contract is gojira's, not ours: required fields failing is loud (a gojira.yaml
without a class is broken), but unknown fields WARN and are retained - gojira's
contract may be newer than this reader (forward compatibility, same posture as the
override block). The proposed `observability:` block is read when present and feeds
`argus.profiles.loader.load_override` unchanged.
"""

import warnings
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class GojiraReadError(ValueError):
    def __init__(self, file: str, detail: str) -> None:
        self.file = file
        self.detail = detail
        super().__init__(f"{file}: {detail}")


class GojiraEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    cluster: str
    namespace: str


class GojiraApp(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    app: str
    class_name: str = Field(alias="class")
    environments: list[GojiraEnvironment]
    schedule: str | None = None
    overrides: dict | None = None  # gojira's sizing overrides - not ours to interpret
    observability: dict | None = None  # the PROPOSED block; feeds load_override verbatim

    @model_validator(mode="after")
    def _warn_unknown(self) -> GojiraApp:
        for key in self.model_extra or {}:
            warnings.warn(
                f"unknown gojira.yaml field {key!r} (retained; gojira's contract may be newer)",
                UserWarning,
                stacklevel=2,
            )
        return self

    def environment(self, name: str) -> GojiraEnvironment:
        for env in self.environments:
            if env.name == name:
                return env
        raise KeyError(f"no environment {name!r} in gojira.yaml for app {self.app!r}")


class ClusterInfo(BaseModel):
    """The shape of gojira's cluster-registry rows (name, project, location, cluster)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    gcp_project: str
    location: str
    gke_cluster: str
    ar_repo: str | None = None


def read_gojira_yaml(path: Path) -> GojiraApp:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise GojiraReadError(str(path), f"invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise GojiraReadError(str(path), "expected a mapping at the top level")
    try:
        return GojiraApp.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "<root>"
        raise GojiraReadError(str(path), f"field {loc}: {first['msg']}") from e
