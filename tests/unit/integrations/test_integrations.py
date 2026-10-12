"""The integrations catalog: declarative integration.yaml -> receiver_creator config.

The north star (design.md): a labeled pod lights up with zero per-app config. The
rendered receiver_creator rule syntax is contract - exact strings asserted here.
"""

from pathlib import Path

import pytest

from noto.integrations.catalog import IntegrationError, load_catalog
from noto.integrations.render import render_receiver_creator

REPO = Path(__file__).parents[3]


def test_shipped_catalog_loads() -> None:
    catalog = load_catalog(REPO / "integrations")
    names = {i.name for i in catalog}
    assert {"redis", "nginx"} <= names
    redis = next(i for i in catalog if i.name == "redis")
    assert redis.match.labels == {"noto.dev/integration": "redis"}
    assert redis.match.port == 6379
    assert redis.receiver.type == "redis"


def test_receiver_creator_rendering() -> None:
    catalog = load_catalog(REPO / "integrations")
    creator, observer = render_receiver_creator(catalog)
    assert observer == {
        "auth_type": "serviceAccount",
        "observe_pods": True,
        "observe_services": False,
    }
    assert creator["watch_observers"] == ["k8s_observer"]
    redis = creator["receivers"]["redis/noto-redis"]
    # label OR port match - the receiver_creator rule syntax is contract
    assert redis["rule"] == (
        'type == "port" and (pod.labels["noto.dev/integration"] == "redis" or port == 6379)'
    )
    assert redis["config"]["endpoint"] == "`endpoint`"


def test_unknown_field_is_error(tmp_path: Path) -> None:
    bad = tmp_path / "weird"
    bad.mkdir()
    (bad / "integration.yaml").write_text(
        "name: weird\nsurprise: 1\nmatch: {port: 1}\nreceiver: {type: x}\n"
    )
    with pytest.raises(IntegrationError, match=r"weird.*surprise"):
        load_catalog(tmp_path)


def test_duplicate_names_rejected(tmp_path: Path) -> None:
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "integration.yaml").write_text(
            "name: same\nmatch: {port: 1}\nreceiver: {type: x}\n"
        )
    with pytest.raises(IntegrationError, match="duplicate"):
        load_catalog(tmp_path)
