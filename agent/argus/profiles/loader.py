"""Load profile YAML into validated models. Failures name the file and the field."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from argus.profiles.models import BaselineProfile, ClassProfile, Override


class ProfileError(ValueError):
    """A profile file failed to parse or validate."""

    def __init__(self, file: str, detail: str) -> None:
        self.file = file
        self.detail = detail
        super().__init__(f"{file}: {detail}")


def _load(path: Path, model: type) -> object:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ProfileError(str(path), f"invalid YAML: {e}") from e
    try:
        return model.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "<root>"
        raise ProfileError(str(path), f"field {loc}: {first['msg']}") from e


def load_baseline(path: Path) -> BaselineProfile:
    return _load(path, BaselineProfile)  # type: ignore[return-value]


def load_class(path: Path) -> ClassProfile:
    return _load(path, ClassProfile)  # type: ignore[return-value]


def load_override(data: dict | None) -> Override:
    return Override() if data is None else Override.model_validate(data)
