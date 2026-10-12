"""Distill raw samples into the percentile quantities gojira's recommender consumes.

Pure functions: samples in (floats - cores or bytes), ResourceUsage out. Floors at
1m / 1Mi so a near-idle container never serializes to "0m" (which gojira's quantity
parser would accept but the recommender treats as junk).
"""

import math
from collections.abc import Callable

from noto.feedback.models import ResourceUsage


class DistillError(ValueError):
    pass


def to_cpu_quantity(cores: float) -> str:
    return f"{max(1, round(cores * 1000))}m"


def to_mem_quantity(bytes_value: float) -> str:
    return f"{max(1, round(bytes_value / (1024 * 1024)))}Mi"


def _percentile(sorted_samples: list[float], q: float) -> float:
    if not sorted_samples:
        raise DistillError("no samples")
    idx = min(len(sorted_samples) - 1, max(0, math.ceil(q * len(sorted_samples)) - 1))
    return sorted_samples[idx]


def distill_resource(
    samples: list[float],
    request: float,
    limit: float,
    quantize: Callable[[float], str],
) -> ResourceUsage:
    if not samples:
        raise DistillError("no samples for resource distillation")
    ordered = sorted(samples)
    return ResourceUsage(
        request=quantize(request),
        limit=quantize(limit),
        p50=quantize(_percentile(ordered, 0.50)),
        p95=quantize(_percentile(ordered, 0.95)),
        p99=quantize(_percentile(ordered, 0.99)),
        max=quantize(ordered[-1]),
    )
