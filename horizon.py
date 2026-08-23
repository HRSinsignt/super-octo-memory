"""
Maps (Business Score, Investment Score, Momentum) to a Horizon label.

Kept separate from scoring.py so the classification rules — which are
the most opinionated, most likely-to-change part of the system — can be
iterated on without touching the underlying math.
"""

from __future__ import annotations

from typing import Optional

from .config import HORIZON_THRESHOLDS as T
from .models import Horizon, SubScores


def classify_horizon(
    business_score: Optional[float],
    investment_score: Optional[float],
    sub_scores: SubScores,
    years_available: int,
) -> Horizon:
    if years_available < T.min_years_required or business_score is None:
        return Horizon.INSUFFICIENT_DATA

    momentum = sub_scores.momentum if sub_scores.momentum is not None else 0.0
    investment = investment_score if investment_score is not None else 0.0

    if business_score >= T.compounder_business_min and investment >= T.compounder_investment_min:
        return Horizon.LONG_TERM_COMPOUNDER

    if business_score >= T.hold_business_min:
        return Horizon.LONG_TERM_HOLD

    if business_score >= T.watch_business_min and investment >= T.watch_investment_min:
        return Horizon.LONG_TERM_WATCH

    if business_score < T.watch_business_min and momentum >= T.short_term_momentum_min:
        return Horizon.SHORT_TERM_OPPORTUNITY

    return Horizon.SPECULATIVE_AVOID


def data_confidence(years_available: int) -> float:
    """0-1 confidence score based purely on history depth, surfaced in the UI
    so users know when a rating should be treated cautiously."""
    if years_available <= 0:
        return 0.0
    return min(1.0, years_available / T.min_years_for_full_confidence)
