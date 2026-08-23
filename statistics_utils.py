"""
Small, dependency-free statistics helpers.

Deliberately not pulling in numpy/scipy for these — the calculations are
simple enough that a stdlib-only implementation keeps deployment trivial
(e.g. on a minimal serverless function or a locked-down VPS).
"""

from __future__ import annotations

import statistics
from typing import Sequence


def cagr(start_value: float, end_value: float, years: int) -> float | None:
    """Compound annual growth rate as a percentage. None if inputs are invalid."""
    if years <= 0 or start_value <= 0 or end_value <= 0:
        return None
    return ((end_value / start_value) ** (1 / years) - 1) * 100


def percentile_rank(value: float, universe: Sequence[float]) -> float:
    """
    Where `value` sits in `universe`, as a 0-100 percentile.
    Used to rank a JSE company against JSE peers rather than against
    absolute thresholds calibrated for larger, more liquid markets.
    """
    if not universe:
        return 50.0  # neutral if no comparison data
    sorted_universe = sorted(universe)
    below_or_equal = sum(1 for v in sorted_universe if v <= value)
    return (below_or_equal / len(sorted_universe)) * 100


def coefficient_of_variation(values: Sequence[float]) -> float | None:
    """Std dev / mean. Lower = more consistent. None if undefined."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    mean = statistics.mean(clean)
    if mean == 0:
        return None
    stdev = statistics.pstdev(clean)
    return abs(stdev / mean)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def linear_scale(value: float, low: float, high: float, invert: bool = False) -> float:
    """
    Map `value` linearly onto a 0-100 scale given a [low, high] reference
    range, clamping outside the range. `invert=True` for metrics where
    lower is better (e.g. debt/EBITDA).
    """
    if high == low:
        return 50.0
    scaled = (value - low) / (high - low) * 100
    scaled = clamp(scaled)
    return 100 - scaled if invert else scaled
