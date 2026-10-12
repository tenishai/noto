"""Non-SLO alert packs: collector self-health (always) + per-app scrape-down, plus the
platform's own rules (retention), which belong to the cluster and not to any app.

The scrape-down rule is the portable replacement for gojira's GMP GojiraAppScrapeDown.
SLO burn-rate alerts are NOT here - Sloth generates those (see slo.py).
"""

from collections.abc import Mapping

# The platform's own name, imported rather than re-typed: it is this document's own name
# below, the ruler namespace its group is filed under, and the `app` label the cluster-wide
# groups carry. One spelling, so a rename cannot leave two of them disagreeing.
from noto.feedback.ruler import PLATFORM_RULE_GROUP
from noto.profiles.models import ResolvedProfile
from noto.rendering.context import Context

_COLLECTOR_HEALTH_RULES = [
    {
        "alert": "NotoCollectorMemoryHigh",
        "expr": "otelcol_process_memory_rss / otelcol_process_memory_limit > 0.85",
        "for": "10m",
        "labels": {"severity": "ticket"},
        "annotations": {"summary": "Collector {{ $labels.job }} memory near the limiter threshold"},
    },
    {
        "alert": "NotoCollectorRefusing",
        "expr": (
            "sum by (job) (rate(otelcol_receiver_refused_spans[5m])"
            " + rate(otelcol_receiver_refused_metric_points[5m])"
            " + rate(otelcol_receiver_refused_log_records[5m])) > 0"
        ),
        "for": "5m",
        "labels": {"severity": "page"},
        "annotations": {"summary": "Collector {{ $labels.job }} is refusing telemetry at the door"},
    },
    {
        "alert": "NotoExporterFailing",
        "expr": (
            "sum by (job, exporter) (rate(otelcol_exporter_send_failed_spans[5m])"
            " + rate(otelcol_exporter_send_failed_metric_points[5m])"
            " + rate(otelcol_exporter_send_failed_log_records[5m])) > 0"
        ),
        "for": "5m",
        "labels": {"severity": "page"},
        "annotations": {"summary": "Collector exporter {{ $labels.exporter }} failing sends"},
    },
]


def _owned(rule: dict, owner: dict[str, str]) -> dict:
    """The rule with the owner's labels under its own: a rule's own label wins, so a
    pack can still say something different on purpose."""
    if "alert" not in rule:
        return rule
    return {**rule, "labels": {**owner, **rule.get("labels", {})}}


def _cardinality_rules(p: ResolvedProfile, ctx: Context) -> list[dict]:
    budget = p.budgets.cardinality[ctx.environment]
    total = "sum(cortex_ingester_active_series)"
    return [
        {
            "alert": "NotoCardinalityBudget80",
            "expr": f"{total} > {int(budget * 0.8)}",
            "for": "15m",
            "labels": {"severity": "ticket"},
            "annotations": {
                "summary": f"Tenant series count past 80% of the {budget} budget "
                "(profile budgets.cardinality)"
            },
        },
        {
            "alert": "NotoCardinalityBudgetExceeded",
            "expr": f"{total} > {budget}",
            "for": "5m",
            "labels": {"severity": "page"},
            "annotations": {
                "summary": f"Tenant series count EXCEEDS the {budget} budget - "
                "find offenders with `noto cardinality`"
            },
        },
    ]


_SECONDS_PER_DAY = 86400


def _promise_days(promise: str) -> int:
    """The retention promise as whole days. The alert's window is expressed in days, so a
    promise that is not a whole number of days is refused rather than rounded into a
    silently different promise."""
    value, unit = promise[:-1], promise[-1:]
    if value.isdigit():
        if unit == "d":
            return int(value)
        if unit == "h" and int(value) % 24 == 0:
            return int(value) // 24
    raise ValueError(f"retention promise {promise!r} must be a whole number of days")


def render_platform_rules(
    retention: Mapping[str, str], born_epoch: int, namespace: str = "noto"
) -> dict:
    """The platform's own PrometheusRule: one cluster-wide group, applied by `noto
    platform` and never by `observe`.

    `NotoRetentionWindowShort` asks the only question that actually tests the retention
    promise: is there still a sample from just inside the far edge of the window? For a
    promise of N days it looks one day inside that edge (offset N-1 d), so a compactor that
    is deleting early, or object storage that ran out and stopped accepting blocks, shows
    up as a real gap rather than as a setting nobody rechecked.

    The second half is the age gate: a cluster born ten minutes ago has no 29-day-old
    samples and never will yet, so without `born + N days` every new platform would page
    about retention for its first month. `born_epoch` comes from the `born` key in the
    platform's own record (steps.EnsurePlatformInfo), which is written once and never
    overwritten.

    `vector(time())`, not the bare `time()`: PromQL refuses a comparison between two
    scalars without the `bool` modifier ("comparisons between scalars must use BOOL
    modifier", verified with promtool against the pinned Prometheus), and `bool` would
    turn the age gate into a constant 0/1 rather than the filter `and on()` needs.

    A missing `metrics` promise is refused with a sentence rather than a bare KeyError:
    `retention_helm_sets` tolerates a signal with no promise (the chart keeps its own
    default), so the two sides of the same profile key must at least fail legibly on the
    same input.
    """
    promise = retention.get("metrics")
    if not promise:
        raise ValueError("retention has no metrics promise")
    days = _promise_days(promise)
    age_gate = born_epoch + days * _SECONDS_PER_DAY
    expr = f"absent_over_time(up[1h] offset {days - 1}d) and on() (vector(time()) > {age_gate})"
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {
            "name": PLATFORM_RULE_GROUP,
            "namespace": namespace,
            "labels": {"noto.dev/managed": "true", "noto.dev/class": "platform"},
        },
        "spec": {
            "groups": [
                {
                    "name": "noto-platform-retention",
                    "rules": [
                        {
                            "alert": "NotoRetentionWindowShort",
                            "expr": expr,
                            "for": "1h",
                            # Nothing else owns this alert: it names itself so it can be
                            # routed the same way every per-app alert can (design Part 1.3).
                            "labels": {"severity": "warning", "app": PLATFORM_RULE_GROUP},
                            "annotations": {
                                "summary": (
                                    f"Metrics older than {days - 1}d are gone, but the "
                                    f"profile promises {promise}"
                                ),
                                "description": (
                                    "Nothing was stored at the far edge of the retention "
                                    f"window, so queries over the promised {promise} now "
                                    "return less than they should. Check Mimir's compactor "
                                    "retention setting (limits."
                                    "compactor_blocks_retention_period) against the profile "
                                    "promise, and check that object storage has capacity "
                                    "and is still accepting blocks."
                                ),
                            },
                        }
                    ],
                }
            ]
        },
    }


def render_alert_rules(p: ResolvedProfile, ctx: Context) -> dict:
    if not ctx.app or not ctx.namespace:
        raise ValueError("alert packs are per-app: Context.app and Context.namespace are required")
    app_group = f"noto-app-{ctx.app}"
    groups = [
        {"name": "noto-collector-health", "rules": list(_COLLECTOR_HEALTH_RULES)},
        {"name": "noto-cardinality", "rules": _cardinality_rules(p, ctx)},
    ]
    # No scrape, no scrape-down: with the app's monitors suppressed the up{} series never
    # exists, and an alert over a series that cannot exist is an alert that can never fire.
    if p.app_scrape:
        groups.append(
            {
                "name": app_group,
                "rules": [
                    {
                        "alert": "NotoScrapeDown",
                        "expr": f'up{{job="{ctx.app}"}} == 0',
                        "for": "5m",
                        "labels": {"severity": "page"},
                        "annotations": {
                            "summary": f"{ctx.app} scrape target down (no /metrics for 5m)"
                        },
                    }
                ],
            }
        )
    # The app's own alerts say whose they are (design Part 1.3), so they can be routed by
    # owner later. These are rule labels: they are attached after evaluation and override a
    # series label of the same name, which is harmless here because for `up{job="<app>"}`
    # the series' own job and namespace carry exactly these values. Recording rules (no
    # `alert` key) are untouched by `_owned`.
    owner = {
        "app": ctx.app,
        "namespace": ctx.namespace,
        "class": p.class_name,
        "environment": ctx.environment,
    }
    # The other two groups are cluster-wide conditions, not this app's: collector health
    # belongs to the shared collector tier and the cardinality budget to the whole tenant.
    # Every app renders them identically into its own pack, so they carry one added label
    # and it is the platform's. Owner labels here would make each app's copy a distinct
    # alert - N notifications for one fault, and later, every team paged for it.
    shared = {"app": PLATFORM_RULE_GROUP}
    groups = [
        {
            "name": g["name"],
            "rules": [_owned(r, owner if g["name"] == app_group else shared) for r in g["rules"]],
        }
        for g in groups
    ]
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {
            "name": f"noto-alerts-{ctx.app}",
            "namespace": ctx.namespace,
            "labels": {"noto.dev/managed": "true", "noto.dev/class": p.class_name},
        },
        "spec": {"groups": groups},
    }
