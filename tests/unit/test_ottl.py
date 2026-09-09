"""OTTL compiler: structured rule in, exact OTTL out. These strings are contract."""

import pytest

from argus.profiles.models import DropRule
from argus.rendering.ottl import CompiledFilter, UnsupportedRule, compile_rules


def test_log_level_below() -> None:
    rules = [DropRule(name="debug-logs", stage="agent", log_level_below="info")]
    out = compile_rules(rules, "agent")
    # UNSPECIFIED-severity lines (plain stdout) must survive the floor - a bare
    # `< INFO` silently drops every unparsed log line (found live on GKE).
    assert out.logs == [
        "severity_number != SEVERITY_NUMBER_UNSPECIFIED and "
        "severity_number < SEVERITY_NUMBER_INFO"
    ]
    assert out.metrics == [] and out.traces == []


def test_path_matches_hits_logs_and_traces() -> None:
    rules = [DropRule(name="health", stage="agent", path_matches=["/health", "/ready"])]
    out = compile_rules(rules, "agent")
    expected = 'IsMatch(attributes["url.path"], "^(/health|/ready)")'
    assert out.traces == [expected]
    assert out.logs == [expected]


def test_metric_names() -> None:
    rules = [DropRule(name="ksm", stage="gateway", metric_names=["kube_pod_status_reason"])]
    out = compile_rules(rules, "gateway")
    assert out.metrics == ['IsMatch(name, "^(kube_pod_status_reason)$")']


def test_namespace_exclude_hits_all_signals() -> None:
    rules = [DropRule(name="ks", stage="gateway", namespace_exclude=["kube-system"])]
    out = compile_rules(rules, "gateway")
    expected = 'resource.attributes["k8s.namespace.name"] == "kube-system"'
    assert out.logs == [expected] and out.metrics == [expected] and out.traces == [expected]


def test_stage_filtering() -> None:
    rules = [DropRule(name="gw-only", stage="gateway", metric_names=["x"])]
    assert compile_rules(rules, "agent") == CompiledFilter()


def test_disabled_rule_excluded() -> None:
    rules = [DropRule(name="off", stage="agent", enabled=False, log_level_below="info")]
    assert compile_rules(rules, "agent") == CompiledFilter()


def test_raw_ottl_passthrough() -> None:
    rules = [
        DropRule(
            name="custom",
            stage="ingest",
            raw_ottl={"metrics": ['name == "weird"']},
            justification="vendor quirk",
        )
    ]
    out = compile_rules(rules, "ingest")
    assert out.metrics == ['name == "weird"']


def test_labels_strip_is_not_a_filter() -> None:
    rules = [DropRule(name="strip", stage="gateway", metric_labels_strip=["pod_uid"])]
    with pytest.raises(UnsupportedRule, match="transform"):
        compile_rules(rules, "gateway")
