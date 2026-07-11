"""Render OpenTelemetryCollector CRDs for the four roles.

Composition rules (contract-tested): memory_limiter is always first and batch always
last in every pipeline; the agent runs k8sattributes in passthrough mode and the
gateway enriches; tenancy is stamped exactly once (solo: gateway exporters; regional:
ingest exporters); the solo gateway's sending queues are file_storage-backed; statsd
is defined as a legacy-ingest affordance but wired into no default pipeline.

Opinion constants below are pipeline opinions from the enablement guide, not scale
inputs - scale flows from profiles and Context (scale-posture decision).
"""

from typing import Literal

from argus.profiles.models import ResolvedProfile
from argus.rendering.context import Context
from argus.rendering.ottl import CompiledFilter, compile_rules

API_VERSION = "opentelemetry.io/v1beta1"

MEMORY_LIMITER = {"check_interval": "1s", "limit_percentage": 90, "spike_limit_percentage": 10}
BATCH = {"send_batch_size": 1000, "send_batch_max_size": 2000, "timeout": "1s"}

_SIGNAL_EXPORTER = {
    "metrics": "otlphttp/mimir",
    "logs": "otlphttp/loki",
    "traces": "otlp/tempo",
}


def _meta(role: str, ctx: Context) -> dict:
    return {
        "name": f"argus-{role}",
        "namespace": "argus",
        "labels": {
            "argus.dev/managed": "true",
            "argus.dev/role": role,
            "argus.dev/cluster": ctx.cluster,
        },
    }


def _crd(role: str, mode: str, ctx: Context, config: dict, spec_extra: dict | None = None) -> dict:
    spec = {"mode": mode, "config": config}
    if spec_extra:
        spec.update(spec_extra)
    return {
        "apiVersion": API_VERSION,
        "kind": "OpenTelemetryCollector",
        "metadata": _meta(role, ctx),
        "spec": spec,
    }


def _pipeline(receivers: list[str], middle: list[str], exporters: list[str]) -> dict:
    return {
        "receivers": receivers,
        "processors": ["memory_limiter", *middle, "batch"],
        "exporters": exporters,
    }


def _filter_processor(compiled: CompiledFilter) -> dict:
    spec: dict = {"error_mode": "ignore"}
    if compiled.logs:
        spec["logs"] = {"log_record": compiled.logs}
    if compiled.metrics:
        spec["metrics"] = {"metric": compiled.metrics}
    if compiled.traces:
        spec["traces"] = {"span": compiled.traces}
    return spec


def _strip_transform(p: ResolvedProfile, stage: str) -> dict | None:
    labels = [
        label
        for rule in p.drop
        if rule.enabled and rule.stage == stage and rule.metric_labels_strip
        for label in rule.metric_labels_strip
    ]
    if not labels:
        return None
    return {
        "metric_statements": [
            {
                "context": "datapoint",
                "statements": [f'delete_key(attributes, "{label}")' for label in labels],
            }
        ]
    }


def _rules_without_strip(p: ResolvedProfile) -> list:
    return [r for r in p.drop if not r.metric_labels_strip]


def _backend_exporters(ctx: Context, queue_storage: str | None) -> dict:
    def _common(extra: dict) -> dict:
        out = {
            "compression": "zstd",
            "headers": {"X-Scope-OrgID": ctx.tenant},
            "retry_on_failure": {"enabled": True},
            "sending_queue": {"num_consumers": 50, "queue_size": 1000},
            "timeout": "30s",
            **extra,
        }
        if queue_storage:
            out["sending_queue"]["storage"] = queue_storage
        return out

    assert ctx.endpoints is not None
    return {
        "otlphttp/mimir": _common({"endpoint": ctx.endpoints.mimir_otlp_http}),
        "otlphttp/loki": _common({"endpoint": ctx.endpoints.loki_otlp_http}),
        "otlp/tempo": _common(
            {"endpoint": ctx.endpoints.tempo_otlp_grpc, "tls": {"insecure": True}}
        ),
    }


def _self_scrape_receiver() -> dict:
    return {
        "config": {
            "scrape_configs": [
                {
                    "job_name": "argus-collector-self",
                    "scrape_interval": "15s",
                    "static_configs": [{"targets": ["127.0.0.1:8888"]}],
                }
            ]
        }
    }


def _extensions(extra: dict | None = None) -> tuple[dict, list[str]]:
    ext: dict = {
        "health_check": {"endpoint": "0.0.0.0:13133"},
        "zpages": {},
        "pprof": {},
    }
    if extra:
        ext.update(extra)
    return ext, list(ext)


def render_agent(p: ResolvedProfile, ctx: Context) -> dict:
    compiled = compile_rules(_rules_without_strip(p), "agent")

    receivers: dict = {
        "otlp": {
            "protocols": {
                "grpc": {"endpoint": "0.0.0.0:4317"},
                "http": {"endpoint": "0.0.0.0:4318"},
            }
        },
        # Legacy-ingest affordance (ADR): defined so un-instrumented third-party workloads
        # CAN be caught by an explicit pipeline change; wired into no default pipeline.
        "statsd": {"endpoint": "0.0.0.0:8125"},
    }
    if "logs" in p.signals and p.log_collection == "filelog":
        receivers["filelog"] = {
            "include": ["/var/log/pods/*/*/*.log"],
            "include_file_path": True,
            "operators": [{"type": "container"}],
        }

    processors: dict = {
        "memory_limiter": MEMORY_LIMITER,
        "k8sattributes": {
            "passthrough": True,
            "filter": {"node_from_env_var": "KUBE_NODE_NAME"},
        },
        "resourcedetection": {"detectors": ["env", "system"]},
        "batch": BATCH,
    }
    middle = ["k8sattributes", "resourcedetection"]
    if not compiled.is_empty():
        processors["filter/argus-agent"] = _filter_processor(compiled)
        middle.append("filter/argus-agent")

    exporters = {
        "otlp/gateway": {
            "endpoint": "argus-gateway-collector.argus.svc:4317",
            "tls": {"insecure": True},
            "compression": "zstd",
        }
    }

    pipelines: dict = {}
    for signal in p.signals:
        pipeline_receivers = ["otlp", "filelog"] if signal == "logs" else ["otlp"]
        pipelines[signal] = _pipeline(pipeline_receivers, middle, ["otlp/gateway"])

    extensions, extension_names = _extensions()
    config = {
        "receivers": receivers,
        "processors": processors,
        "exporters": exporters,
        "extensions": extensions,
        "service": {"extensions": extension_names, "pipelines": pipelines},
    }
    spec_extra = {
        # k8sattributes node scoping reads this (crashes without it - smoke-found).
        "env": [
            {"name": "KUBE_NODE_NAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}}
        ]
    }
    return _crd("agent", "daemonset", ctx, config, spec_extra)


def render_gateway(p: ResolvedProfile, ctx: Context) -> dict:
    solo = ctx.topology == "solo"
    compiled = compile_rules(_rules_without_strip(p), "gateway")
    ingest_compiled = compile_rules(_rules_without_strip(p), "ingest") if solo else None

    receivers: dict = {
        "otlp": {
            "protocols": {
                "grpc": {"endpoint": "0.0.0.0:4317"},
                "http": {"endpoint": "0.0.0.0:4318"},
            }
        },
        "prometheus/self": _self_scrape_receiver(),
    }

    processors: dict = {
        "memory_limiter": MEMORY_LIMITER,
        "k8sattributes": {},  # full enrichment via the connection source
        "resource/default": {
            "attributes": [
                {"key": "environment", "value": ctx.environment, "action": "upsert"},
                {"key": "cluster", "value": ctx.cluster, "action": "upsert"},
                *(
                    [{"key": "region", "value": ctx.region, "action": "upsert"}]
                    if ctx.region
                    else []
                ),
            ]
        },
        "resource/self": {
            "attributes": [{"key": "cluster", "value": ctx.cluster, "action": "upsert"}]
        },
        "groupbyattrs": {"keys": ["k8s.namespace.name", "k8s.pod.name", "k8s.container.name"]},
        "batch": BATCH,
    }

    middle_common = ["k8sattributes", "resource/default"]
    strip = _strip_transform(p, "gateway")
    if strip:
        processors["transform/labels-strip"] = strip
    if not compiled.is_empty():
        processors["filter/argus-gateway"] = _filter_processor(compiled)
    if solo and ingest_compiled and not ingest_compiled.is_empty():
        processors["filter/argus-ingest"] = _filter_processor(ingest_compiled)

    def _middle(signal: Literal["metrics", "logs", "traces"]) -> list[str]:
        parts = list(middle_common)
        if signal == "metrics":
            parts.append("groupbyattrs")
            if strip:
                parts.append("transform/labels-strip")
        if not compiled.is_empty():
            parts.append("filter/argus-gateway")
        if solo and ingest_compiled and not ingest_compiled.is_empty():
            parts.append("filter/argus-ingest")
        return parts

    if solo:
        exporters = _backend_exporters(ctx, queue_storage="file_storage")
        extensions, extension_names = _extensions(
            {"file_storage": {"directory": "/var/lib/otelcol/queue", "create_directory": True}}
        )
        # The collector container is non-root with a read-only rootfs: the durable
        # sending queue needs a writable volume (smoke-found). Solo uses emptyDir
        # (survives container restarts within the pod); regional gateways get PVCs.
        gateway_spec_extra = {
            "volumes": [{"name": "queue", "emptyDir": {}}],
            "volumeMounts": [{"name": "queue", "mountPath": "/var/lib/otelcol"}],
        }
    else:
        assert ctx.endpoints is not None
        exporters = {
            "kafka": {
                "brokers": [ctx.endpoints.kafka_bootstrap],
                "protocol_version": "2.6.0",
            }
        }
        extensions, extension_names = _extensions()
        gateway_spec_extra = None

    def _exporter_for(signal: str) -> list[str]:
        return [_SIGNAL_EXPORTER[signal]] if solo else ["kafka"]

    pipelines: dict = {}
    for signal in p.signals:
        pipelines[signal] = _pipeline(["otlp"], _middle(signal), _exporter_for(signal))
    pipelines["metrics/self"] = _pipeline(
        ["prometheus/self"], ["resource/self"], _exporter_for("metrics")
    )

    config = {
        "receivers": receivers,
        "processors": processors,
        "exporters": exporters,
        "extensions": extensions,
        "service": {"extensions": extension_names, "pipelines": pipelines},
    }
    return _crd("gateway", "deployment", ctx, config, gateway_spec_extra)


def render_ingest(p: ResolvedProfile, ctx: Context) -> dict:
    if ctx.topology != "regional":
        raise ValueError("the ingest role exists only in the regional topology")
    assert ctx.endpoints is not None

    compiled = compile_rules(_rules_without_strip(p), "ingest")

    receivers = {
        "kafka": {
            "brokers": [ctx.endpoints.kafka_bootstrap],
            "protocol_version": "2.6.0",
        }
    }
    processors: dict = {"memory_limiter": MEMORY_LIMITER, "batch": BATCH}
    middle: list[str] = []
    if not compiled.is_empty():
        processors["filter/argus-ingest"] = _filter_processor(compiled)
        middle.append("filter/argus-ingest")

    exporters = _backend_exporters(ctx, queue_storage=None)

    pipelines: dict = {}
    for signal in p.signals:
        pipelines[signal] = _pipeline(["kafka"], middle, [_SIGNAL_EXPORTER[signal]])

    extensions, extension_names = _extensions()
    config = {
        "receivers": receivers,
        "processors": processors,
        "exporters": exporters,
        "extensions": extensions,
        "service": {"extensions": extension_names, "pipelines": pipelines},
    }
    return _crd("ingest", "deployment", ctx, config)


def render_targetallocator(p: ResolvedProfile, ctx: Context) -> dict:
    processors = {
        "memory_limiter": MEMORY_LIMITER,
        "groupbyattrs": {"keys": ["k8s.namespace.name", "k8s.pod.name", "k8s.container.name"]},
        "batch": BATCH,
    }
    receivers = {
        "prometheus": {
            "config": {"scrape_configs": []},
            # The TargetAllocator shards ServiceMonitor/PodMonitor targets across
            # scraper replicas; this is the canonical operator wiring.
            "target_allocator": {
                "endpoint": "http://argus-scraper-targetallocator",
                "interval": "30s",
                "collector_id": "${env:POD_NAME}",
            },
        }
    }
    exporters = {
        "otlp/gateway": {
            "endpoint": "argus-gateway-collector.argus.svc:4317",
            "tls": {"insecure": True},
            "compression": "zstd",
        }
    }
    pipelines = {"metrics": _pipeline(["prometheus"], ["groupbyattrs"], ["otlp/gateway"])}
    extensions, extension_names = _extensions()
    config = {
        "receivers": receivers,
        "processors": processors,
        "exporters": exporters,
        "extensions": extensions,
        "service": {"extensions": extension_names, "pipelines": pipelines},
    }
    spec_extra = {
        "targetAllocator": {
            "enabled": True,
            "serviceAccount": "argus-targetallocator",
            "prometheusCR": {
                "enabled": True,
                # Explicit select-all: without these the operator selects nothing
                # (smoke-found). Argus-managed monitors are the only ones present.
                "serviceMonitorSelector": {},
                "podMonitorSelector": {},
            },
        }
    }
    return _crd("scraper", "statefulset", ctx, config, spec_extra)


def render_targetallocator_rbac(ctx: Context) -> list[dict]:
    """RBAC the operator does NOT create (smoke-found): the TargetAllocator's SA (reads
    ServiceMonitors/PodMonitors and discovers targets cluster-wide) and the k8sattributes
    ClusterRole for the gateway collector's operator-created SA (pod/namespace metadata
    enrichment - the enablement guide's "RBAC required" made concrete)."""
    name = "argus-targetallocator"
    k8sattributes = [
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": "argus-k8sattributes"},
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": ["pods", "namespaces", "nodes"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": ["apps"],
                    "resources": ["replicasets", "deployments", "statefulsets", "daemonsets"],
                    "verbs": ["get", "list", "watch"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": "argus-k8sattributes"},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "argus-k8sattributes",
            },
            "subjects": [
                # Operator-created collector SAs (<collector-name>-collector).
                {"kind": "ServiceAccount", "name": "argus-gateway-collector", "namespace": "argus"},
                {"kind": "ServiceAccount", "name": "argus-agent-collector", "namespace": "argus"},
                {"kind": "ServiceAccount", "name": "argus-ingest-collector", "namespace": "argus"},
            ],
        },
    ]
    return [
        *k8sattributes,
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": name, "namespace": "argus"},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": name},
            "rules": [
                {
                    "apiGroups": ["monitoring.coreos.com"],
                    "resources": ["servicemonitors", "podmonitors", "scrapeconfigs", "probes"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": [""],
                    "resources": [
                        "nodes",
                        "nodes/metrics",
                        "services",
                        "endpoints",
                        "pods",
                        "namespaces",
                        "configmaps",
                        "secrets",
                    ],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": ["discovery.k8s.io"],
                    "resources": ["endpointslices"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["ingresses"],
                    "verbs": ["get", "list", "watch"],
                },
                {"nonResourceURLs": ["/metrics"], "verbs": ["get"]},
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": name},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": name,
            },
            "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": "argus"}],
        },
    ]
