"""Shared helpers for walking rendered OpenTelemetryCollector CRDs.

Used by both unit tests and the contract invariant suite.
"""

from collections.abc import Iterator


def config_of(crd: dict) -> dict:
    return crd["spec"]["config"]


def iter_pipelines(crd: dict) -> Iterator[tuple[str, dict]]:
    yield from config_of(crd)["service"]["pipelines"].items()


def processors_of(crd: dict, pipeline: str) -> list[str]:
    return config_of(crd)["service"]["pipelines"][pipeline].get("processors", [])
