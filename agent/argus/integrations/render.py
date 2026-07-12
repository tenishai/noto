"""Catalog -> receiver_creator + k8s_observer config for the agent.

The rule syntax is receiver_creator's, on "port" endpoints - the type whose `endpoint`
expands to ip:port (live-verified: pod.container endpoints carry no port and the
sub-receiver fails with "missing port in address"). Label match OR port match; sub-
receiver configs pass through verbatim (backticks are receiver_creator templating).
"""

from argus.integrations.catalog import Integration


def _rule(integration: Integration) -> str:
    clauses = [
        f'pod.labels["{key}"] == "{value}"' for key, value in integration.match.labels.items()
    ]
    if integration.match.port is not None:
        clauses.append(f"port == {integration.match.port}")
    joined = " or ".join(clauses)
    return f'type == "port" and ({joined})'


def render_receiver_creator(catalog: list[Integration]) -> tuple[dict, dict]:
    """Returns (receiver_creator config, k8s_observer extension config)."""
    creator: dict = {"watch_observers": ["k8s_observer"], "receivers": {}}
    for integration in catalog:
        key = f"{integration.receiver.type}/argus-{integration.name}"
        creator["receivers"][key] = {
            "rule": _rule(integration),
            "config": dict(integration.receiver.config),
        }
    observer = {"auth_type": "serviceAccount", "observe_pods": True, "observe_services": False}
    return creator, observer
