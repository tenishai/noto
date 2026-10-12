"""Compile structured drop rules into OTTL statements for the filter processor.

Filter semantics: a statement that evaluates TRUE drops the datapoint. metric_labels_strip
is deliberately not compilable here - stripping a label mutates rather than drops, so it
renders as a transform-processor statement in collectors.py.
"""

from pydantic import BaseModel

from noto.profiles.models import DropRule, Stage


class CompiledFilter(BaseModel):
    logs: list[str] = []
    metrics: list[str] = []
    traces: list[str] = []

    def is_empty(self) -> bool:
        return not (self.logs or self.metrics or self.traces)


class UnsupportedRule(ValueError):
    """A rule the compiler cannot express as a filter drop."""


def compile_rules(rules: list[DropRule], stage: Stage) -> CompiledFilter:
    logs: list[str] = []
    metrics: list[str] = []
    traces: list[str] = []

    for rule in rules:
        if not rule.enabled or rule.stage != stage:
            continue
        if rule.metric_labels_strip:
            raise UnsupportedRule(
                f"rule {rule.name!r}: metric_labels_strip mutates rather than drops; "
                "it renders as a transform-processor statement, not a filter"
            )
        if rule.log_level_below:
            # UNSPECIFIED (0) means the line carried no parseable severity - plain
            # stdout, which is most real pod output. A bare `< INFO` drops all of it
            # (found live: fleet-wide zero logs in Loki). Only drop lines whose
            # severity is actually known to be below the floor.
            logs.append(
                "severity_number != SEVERITY_NUMBER_UNSPECIFIED and "
                f"severity_number < SEVERITY_NUMBER_{rule.log_level_below.upper()}"
            )
        if rule.path_matches:
            alternation = "|".join(rule.path_matches)
            stmt = f'IsMatch(attributes["url.path"], "^({alternation})")'
            traces.append(stmt)
            logs.append(stmt)
        if rule.metric_names:
            alternation = "|".join(rule.metric_names)
            metrics.append(f'IsMatch(name, "^({alternation})$")')
        if rule.namespace_exclude:
            for ns in rule.namespace_exclude:
                stmt = f'resource.attributes["k8s.namespace.name"] == "{ns}"'
                logs.append(stmt)
                metrics.append(stmt)
                traces.append(stmt)
        if rule.raw_ottl:
            for signal, statements in rule.raw_ottl.items():
                {"logs": logs, "metrics": metrics, "traces": traces}[signal].extend(statements)

    return CompiledFilter(logs=logs, metrics=metrics, traces=traces)
