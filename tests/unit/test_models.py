"""Profile model validation - the schema is the contract's first line of defense."""

import pytest
from pydantic import ValidationError

from argus.profiles.models import SLO, ClassProfile, DropRule, Override


def test_drop_rule_roundtrip() -> None:
    rule = DropRule(name="health-endpoints", stage="agent", path_matches=["/health"])
    assert rule.name == "health-endpoints"
    assert rule.stage == "agent"
    assert rule.enabled is True


def test_raw_ottl_requires_justification() -> None:
    with pytest.raises(ValidationError, match="justification"):
        DropRule(name="custom", stage="gateway", raw_ottl={"logs": ['body == "x"']})


def test_raw_ottl_with_justification_ok() -> None:
    rule = DropRule(
        name="custom",
        stage="gateway",
        raw_ottl={"logs": ['body == "x"']},
        justification="vendor noise, approved 2026-07-11",
    )
    assert rule.raw_ottl == {"logs": ['body == "x"']}


def test_latency_slo_requires_threshold() -> None:
    with pytest.raises(ValidationError, match="latency_threshold"):
        SLO(name="latency-p99", objective=99.0, signal="latency")


def test_unknown_field_on_class_profile_is_error() -> None:
    with pytest.raises(ValidationError):
        ClassProfile.model_validate({"class": "web", "signals": ["metrics"], "surprise": 1})


def test_unknown_field_on_override_warns_and_is_retained() -> None:
    with pytest.warns(UserWarning, match="surprise"):
        override = Override.model_validate({"traces": True, "surprise": 1})
    assert override.model_extra == {"surprise": 1}
