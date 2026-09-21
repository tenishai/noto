"""Retention: the profile's promise becomes the backends' own retention setting, and an
alert fires when the window on disk turns out to be shorter than the promise.

Every helm values key asserted here was verified against the PINNED chart (helm/pins.yaml)
by templating the chart with this repo's values plus the --set, and then feeding the
rendered config to the backend's own binary. See the task report for the transcripts.
"""

import pytest

from argus.rendering.alerts import render_platform_rules
from argus.steps import helm_set_values, retention_helm_sets

# profiles/baseline.yaml's budgets.retention, spelled out so a baseline edit shows up
# here as a deliberate test change rather than a silently re-derived expectation.
BASELINE = {"metrics": "30d", "logs": "7d", "traces": "7d"}

BORN = 1_700_000_000  # an arbitrary but fixed birth instant


def _only_rule(doc: dict) -> dict:
    groups = doc["spec"]["groups"]
    assert len(groups) == 1, groups
    rules = groups[0]["rules"]
    assert len(rules) == 1, rules
    return rules[0]


def test_mimir_gets_the_metrics_promise_as_its_block_retention() -> None:
    assert retention_helm_sets(BASELINE)["mimir"] == [
        "--set",
        "mimir.structuredConfig.limits.compactor_blocks_retention_period=30d",
    ]


def test_loki_gets_the_logs_promise_and_the_switch_that_makes_it_delete() -> None:
    """retention_period alone is inert: Loki only enforces it when the compactor's
    retention loop is turned on."""
    assert retention_helm_sets(BASELINE)["loki"] == [
        "--set",
        "loki.limits_config.retention_period=7d",
        "--set",
        "loki.compactor.retention_enabled=true",
    ]


def test_tempo_gets_hours_because_a_go_duration_has_no_day_unit() -> None:
    """Live-found against the pinned chart: tempo 2.10.7 rejects `7d` outright
    ("cannot unmarshal !!str `7d` into time.Duration"), so the promise is converted."""
    assert retention_helm_sets(BASELINE)["tempo"] == ["--set", "tempo.retention=168h"]


def test_a_signal_with_no_promise_gets_no_sets() -> None:
    sets = retention_helm_sets({"metrics": "30d"})
    assert set(sets) == {"mimir"}


def test_a_missing_metrics_promise_fails_the_same_way_on_both_sides() -> None:
    """`retention_helm_sets` tolerates a signal with no promise (the chart keeps its own
    default); the alert cannot, since there is no window to check. It must at least say so
    in a sentence rather than as a bare KeyError."""
    assert retention_helm_sets({"logs": "7d"}) == {
        "loki": [
            "--set",
            "loki.limits_config.retention_period=7d",
            "--set",
            "loki.compactor.retention_enabled=true",
        ]
    }
    with pytest.raises(ValueError, match="retention has no metrics promise"):
        render_platform_rules({"logs": "7d"}, 0)


def test_an_overridden_promise_reaches_the_chart() -> None:
    sets = retention_helm_sets({"metrics": "90d", "logs": "30d", "traces": "14d"})
    assert sets["mimir"] == [
        "--set",
        "mimir.structuredConfig.limits.compactor_blocks_retention_period=90d",
    ]
    assert sets["loki"][1] == "loki.limits_config.retention_period=30d"
    assert sets["tempo"] == ["--set", "tempo.retention=336h"]


def test_a_promise_already_in_hours_is_left_alone() -> None:
    assert retention_helm_sets({"traces": "36h"})["tempo"] == ["--set", "tempo.retention=36h"]


def test_an_unparseable_promise_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="retention"):
        retention_helm_sets({"traces": "a week"})


def test_helm_set_values_is_the_mapping_helm_install_fingerprints() -> None:
    """HelmInstall carries its sets as a mapping (it hashes them into the release
    description), so the two forms meet in exactly one place."""
    assert helm_set_values(["--set", "a.b.c=1", "--set", "d=x=y"]) == {"a.b.c": "1", "d": "x=y"}
    assert helm_set_values([]) == {}


# --- the alarm ---------------------------------------------------------------


def test_the_retention_alert_is_a_platform_rule_in_the_platform_namespace() -> None:
    doc = render_platform_rules(BASELINE, BORN, namespace="obs")
    assert doc["kind"] == "PrometheusRule"
    assert doc["metadata"]["name"] == "argus-platform"
    assert doc["metadata"]["namespace"] == "obs"
    assert doc["metadata"]["labels"]["argus.dev/managed"] == "true"


def test_the_expression_for_the_baseline_thirty_day_promise() -> None:
    rule = _only_rule(render_platform_rules(BASELINE, BORN))
    assert rule["alert"] == "ArgusRetentionWindowShort"
    assert rule["expr"] == (
        "absent_over_time(up[1h] offset 29d) and on() (vector(time()) > 1702592000)"
    )
    assert rule["for"] == "1h"
    assert rule["labels"]["severity"] == "warning"


def test_a_ninety_day_promise_moves_the_offset_and_the_age_gate() -> None:
    rule = _only_rule(render_platform_rules({"metrics": "90d"}, BORN))
    assert rule["expr"] == (
        "absent_over_time(up[1h] offset 89d) and on() (vector(time()) > 1707776000)"
    )


def test_the_age_gate_is_born_plus_the_whole_promise() -> None:
    """Without it, every freshly born cluster alerts for its first month."""
    rule = _only_rule(render_platform_rules({"metrics": "30d"}, 0))
    assert "vector(time()) > 2592000" in rule["expr"]


def test_the_annotations_say_what_it_means_and_what_to_check() -> None:
    rule = _only_rule(render_platform_rules(BASELINE, BORN))
    text = " ".join(rule["annotations"].values()).lower()
    assert "30d" in text
    assert "compactor" in text
    assert "object storage" in text


def test_a_metrics_promise_that_is_not_days_is_refused() -> None:
    with pytest.raises(ValueError, match="retention"):
        render_platform_rules({"metrics": "forever"}, BORN)


def test_the_retention_alert_says_it_belongs_to_the_platform() -> None:
    """Nothing else owns this alert, so it carries its own name as its `app` rather than
    going unlabeled while every per-app alert says whose it is."""
    rule = _only_rule(render_platform_rules(BASELINE, BORN))
    assert rule["labels"]["app"] == "argus-platform"
