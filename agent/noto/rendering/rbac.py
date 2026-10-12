"""RBAC for the job identity `noto observe` runs as when a provisioner launches it as a
one-shot in-cluster Job (born-observed, task 6).

This is a different concern from `render_targetallocator_rbac` in `collectors.py`: that
grants the TargetAllocator and gateway collector SAs (both operator-managed, cluster-tier
identities) what they need to discover scrape targets and enrich telemetry. This module
grants the ONE identity the provisioner's Job itself runs as - the narrowest role that
lets `observe` server-side apply its per-app artifacts and read the platform's own
record. Nothing else: no secrets, no wildcard, no delete, no workload access.

The trust scope, stated rather than left to be discovered: `noto-observe` is trusted
platform-wide, across every app namespace including ones that do not exist yet. It has to
be - gojira creates app namespaces dynamically, so a namespace-scoped grant could not
cover the next app - and it means this identity is not sandboxed per tenant: whatever can
run a Job as it can write monitors and rules for any app in the cluster. That is accepted
scope for a platform provisioner, not an oversight. The narrowing that is real is the
verb and kind list below, and who is allowed to create Jobs in the platform namespace.

The three granted resources cover every kind the per-app families can render (see the
contract invariant in tests/contract/test_invariants.py): scrape -> ServiceMonitor,
PodMonitor; alerts -> PrometheusRule. `slo` renders a Sloth CR (PrometheusServiceLevel)
as its OWN intermediate representation, but that document is never applied to the
cluster - `_generate_slo_rules` (agent/noto/cli.py) runs the pinned `sloth generate`
binary as a local subprocess and hands ApplyRendered the resulting PrometheusRule
documents instead. Sloth is a generator here, not an in-cluster operator, so the
ServiceAccount never needs permission on `sloth.slok.dev` kinds.

Dashboards (the `perses` family) go over HTTP to the Perses API, never through
kubectl - no Kubernetes permission for those either.
"""

API_VERSION_RBAC = "rbac.authorization.k8s.io/v1"

SA_NAME = "noto-observe"
PLATFORM_INFO_CONFIGMAP = "noto-platform-info"
PLATFORM_INFO_ROLE_NAME = f"{SA_NAME}-platform-info"


def render_observe_rbac(namespace: str = "noto") -> list[dict]:
    """The provisioner's Job runs as ServiceAccount `noto-observe`. Returns, in order:

    1. ServiceAccount `noto-observe` (in `namespace`)
    2. ClusterRole `noto-observe` - one rule: get/list/create/patch/update on
       servicemonitors/podmonitors/prometheusrules (monitoring.coreos.com). Cluster-wide
       because gojira creates app namespaces dynamically (same reasoning as
       `render_targetallocator_rbac`).
    3. ClusterRoleBinding `noto-observe` - binds the ClusterRole to the ServiceAccount.
    4. Role `noto-observe-platform-info` (in `namespace`) - `get` on `configmaps`,
       `resourceNames: ["noto-platform-info"]` only (Task 3 review amendment: the job
       may read the platform's own record - no `list`, no other ConfigMap).
    5. RoleBinding `noto-observe-platform-info` - binds that Role to the ServiceAccount.
    """
    service_account = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": SA_NAME, "namespace": namespace},
    }
    cluster_role = {
        "apiVersion": API_VERSION_RBAC,
        "kind": "ClusterRole",
        "metadata": {"name": SA_NAME},
        "rules": [
            {
                "apiGroups": ["monitoring.coreos.com"],
                "resources": ["servicemonitors", "podmonitors", "prometheusrules"],
                "verbs": ["get", "list", "create", "patch", "update"],
            }
        ],
    }
    cluster_role_binding = {
        "apiVersion": API_VERSION_RBAC,
        "kind": "ClusterRoleBinding",
        "metadata": {"name": SA_NAME},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": SA_NAME,
        },
        "subjects": [{"kind": "ServiceAccount", "name": SA_NAME, "namespace": namespace}],
    }
    platform_info_role = {
        "apiVersion": API_VERSION_RBAC,
        "kind": "Role",
        "metadata": {"name": PLATFORM_INFO_ROLE_NAME, "namespace": namespace},
        "rules": [
            {
                "apiGroups": [""],
                "resources": ["configmaps"],
                "resourceNames": [PLATFORM_INFO_CONFIGMAP],
                "verbs": ["get"],
            }
        ],
    }
    platform_info_role_binding = {
        "apiVersion": API_VERSION_RBAC,
        "kind": "RoleBinding",
        "metadata": {"name": PLATFORM_INFO_ROLE_NAME, "namespace": namespace},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": PLATFORM_INFO_ROLE_NAME,
        },
        "subjects": [{"kind": "ServiceAccount", "name": SA_NAME, "namespace": namespace}],
    }
    return [
        service_account,
        cluster_role,
        cluster_role_binding,
        platform_info_role,
        platform_info_role_binding,
    ]
