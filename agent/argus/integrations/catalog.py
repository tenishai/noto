"""Load integrations/<name>/integration.yaml into validated models.

Same posture as profiles: these files are ours, unknown fields are errors, failures
name the file and field.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError


class IntegrationError(ValueError):
    def __init__(self, file: str, detail: str) -> None:
        super().__init__(f"{file}: {detail}")


class Match(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    labels: dict[str, str] = {}
    port: int | None = None


class Receiver(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    config: dict = {}


class Integration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    match: Match
    receiver: Receiver


def load_catalog(root: Path) -> list[Integration]:
    integrations: list[Integration] = []
    seen: set[str] = set()
    for path in sorted(root.glob("*/integration.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
            integration = Integration.model_validate(data)
        except yaml.YAMLError as e:
            raise IntegrationError(str(path), f"invalid YAML: {e}") from e
        except ValidationError as e:
            first = e.errors()[0]
            loc = ".".join(str(p) for p in first["loc"]) or "<root>"
            raise IntegrationError(str(path), f"field {loc}: {first['msg']}") from e
        if integration.name in seen:
            raise IntegrationError(str(path), f"duplicate integration name {integration.name!r}")
        seen.add(integration.name)
        integrations.append(integration)
    return integrations
