"""Loader behavior: valid files load, invalid files fail loud with file and field named."""

from pathlib import Path

import pytest

from argus.profiles.loader import ProfileError, load_baseline, load_class, load_override

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


def test_loads_valid_baseline_and_class() -> None:
    baseline = load_baseline(FIXTURES / "baseline.yaml")
    cls = load_class(FIXTURES / "classes" / "web.yaml")
    assert baseline.budgets.retention["metrics"] == "30d"
    assert cls.class_name == "web"
    assert cls.slos[0].objective == 99.9


def test_invalid_yaml_syntax_names_the_file() -> None:
    with pytest.raises(ProfileError, match=r"bad-syntax\.yaml"):
        load_class(FIXTURES / "classes" / "bad-syntax.yaml")


def test_unknown_field_names_file_and_field() -> None:
    with pytest.raises(ProfileError, match=r"bad-unknown\.yaml.*surprise"):
        load_class(FIXTURES / "classes" / "bad-unknown.yaml")


def test_load_override_none_is_empty() -> None:
    override = load_override(None)
    assert override.traces is None
    assert override.model_extra in (None, {})
