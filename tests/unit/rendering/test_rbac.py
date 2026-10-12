"""render_observe_rbac: the narrowest identity a provisioner's Job runs `noto observe`
as. See agent/noto/rendering/rbac.py for why the granted resources are exactly
ServiceMonitor/PodMonitor/PrometheusRule plus one named ConfigMap."""

import yaml

from noto.rendering.rbac import render_observe_rbac


def test_returns_five_documents_in_order() -> None:
    docs = render_observe_rbac("noto")
    kinds = [d["kind"] for d in docs]
    assert kinds == [
        "ServiceAccount",
        "ClusterRole",
        "ClusterRoleBinding",
        "Role",
        "RoleBinding",
    ]


def test_names_and_namespaces() -> None:
    sa, cr, crb, role, rb = render_observe_rbac("obs")
    assert sa["metadata"] == {"name": "noto-observe", "namespace": "obs"}
    assert cr["metadata"] == {"name": "noto-observe"}
    assert "namespace" not in cr["metadata"]  # cluster-scoped
    assert crb["metadata"] == {"name": "noto-observe"}
    assert "namespace" not in crb["metadata"]  # cluster-scoped
    assert role["metadata"] == {"name": "noto-observe-platform-info", "namespace": "obs"}
    assert rb["metadata"] == {"name": "noto-observe-platform-info", "namespace": "obs"}


def test_namespace_defaults_to_noto() -> None:
    sa, *_rest = render_observe_rbac()
    assert sa["metadata"]["namespace"] == "noto"


def test_cluster_role_has_exactly_one_rule_with_exactly_these_values() -> None:
    _sa, cr, *_rest = render_observe_rbac()
    assert len(cr["rules"]) == 1
    rule = cr["rules"][0]
    assert rule == {
        "apiGroups": ["monitoring.coreos.com"],
        "resources": ["servicemonitors", "podmonitors", "prometheusrules"],
        "verbs": ["get", "list", "create", "patch", "update"],
    }


def test_cluster_role_binding_subject_is_the_service_account_in_the_given_namespace() -> None:
    _sa, _cr, crb, *_rest = render_observe_rbac("obs")
    assert crb["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "noto-observe",
    }
    assert crb["subjects"] == [
        {"kind": "ServiceAccount", "name": "noto-observe", "namespace": "obs"}
    ]


def test_platform_info_role_grants_only_get_on_the_one_named_configmap() -> None:
    *_rest, role, _rb = render_observe_rbac()
    assert len(role["rules"]) == 1
    rule = role["rules"][0]
    assert rule["apiGroups"] == [""]
    assert rule["resources"] == ["configmaps"]
    assert rule["resourceNames"] == ["noto-platform-info"]
    assert rule["verbs"] == ["get"]


def test_platform_info_role_binding_subject_is_the_service_account_in_the_given_namespace() -> None:
    *_rest, role, rb = render_observe_rbac("obs")
    assert rb["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": "noto-observe-platform-info",
    }
    assert rb["subjects"] == [
        {"kind": "ServiceAccount", "name": "noto-observe", "namespace": "obs"}
    ]
    assert role["metadata"]["namespace"] == rb["metadata"]["namespace"] == "obs"


def test_no_secrets_no_wildcard_no_delete_anywhere_in_the_rendered_yaml() -> None:
    docs = render_observe_rbac("obs")
    dumped = yaml.safe_dump_all(docs)
    assert "secrets" not in dumped
    assert "*" not in dumped
    assert "delete" not in dumped
    assert "deletecollection" not in dumped


def test_namespace_argument_reaches_every_namespaced_document() -> None:
    docs = render_observe_rbac("custom-ns")
    namespaced = [d for d in docs if d["kind"] in ("ServiceAccount", "Role", "RoleBinding")]
    assert len(namespaced) == 3
    for doc in namespaced:
        assert doc["metadata"]["namespace"] == "custom-ns"
    for doc in docs:
        for subject in doc.get("subjects", []):
            assert subject["namespace"] == "custom-ns"
