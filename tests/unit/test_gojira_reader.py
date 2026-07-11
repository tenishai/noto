"""gojira-reader: parse the REAL gojira contract (fixtures copied from gojira's own
golden tests, so the two repos cannot drift silently)."""

from pathlib import Path

import pytest

from argus.gojira.reader import ClusterInfo, GojiraReadError, read_gojira_yaml

FIXTURES = Path(__file__).parent / "fixtures" / "gojira"


@pytest.mark.parametrize(
    ("fixture", "app", "class_name"),
    [
        ("web", "clips-api", "web"),
        ("worker", "clips-worker", "worker"),
        ("cron", "clips-sweeper", "cron"),
        ("multiproject", "clips-api", "web"),
    ],
)
def test_real_gojira_fixtures_parse(fixture: str, app: str, class_name: str) -> None:
    spec = read_gojira_yaml(FIXTURES / f"{fixture}.yaml")
    assert spec.app == app
    assert spec.class_name == class_name
    assert spec.environments, "every gojira app has environments"
    env = spec.environment("dev")
    assert env.cluster and env.namespace


def test_cron_schedule_parsed() -> None:
    spec = read_gojira_yaml(FIXTURES / "cron.yaml")
    assert spec.schedule == "0 3 * * *"


def test_per_env_cluster_bindings() -> None:
    spec = read_gojira_yaml(FIXTURES / "multiproject.yaml")
    assert spec.environment("dev").cluster == "dev-cluster"
    assert spec.environment("prod").cluster == "prod-cluster"
    with pytest.raises(KeyError):
        spec.environment("staging")


def test_observability_block_and_unknown_fields() -> None:
    with pytest.warns(UserWarning, match="future_gojira_field"):
        spec = read_gojira_yaml(FIXTURES / "web-observability.yaml")
    assert spec.observability == {"traces": True, "retention": {"metrics": "90d"}}
    # feeds straight into the profile layer's Override
    from argus.profiles.loader import load_override

    override = load_override(spec.observability)
    assert override.traces is True


def test_missing_observability_is_none() -> None:
    spec = read_gojira_yaml(FIXTURES / "web.yaml")
    assert spec.observability is None


def test_invalid_yaml_and_missing_fields_fail_loud(tmp_path: Path) -> None:
    bad = tmp_path / "gojira.yaml"
    bad.write_text("app: x\nenvironments: []\n")  # no class
    with pytest.raises(GojiraReadError, match="class"):
        read_gojira_yaml(bad)


def test_cluster_registry_shape() -> None:
    info = ClusterInfo.model_validate(
        {
            "name": "dev-cluster",
            "gcp_project": "acme-dev",
            "location": "us-east4",
            "gke_cluster": "dev-gke",
        }
    )
    assert info.location == "us-east4"
