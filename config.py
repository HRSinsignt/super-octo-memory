"""
Central place for every weight and threshold used by the scoring engine.

Keeping these out of scoring.py means you can tune the model (or expose
these as user-adjustable sliders in a UI later) without touching logic.
Every weight group should sum to 1.0 - validated at import time below.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SubScoreWeights:
    # Business Quality inputs
    bq_competitive_position: float = 0.30
    bq_roe_roic: float = 0.25
    bq_revenue_durability: float = 0.25
    bq_capital_allocation: float = 0.20

    # Financial Health inputs
    fh_debt_coverage: float = 0.35
    fh_fcf_conversion: float = 0.25
    fh_liquidity: float = 0.20
    fh_earnings_consistency: float = 0.20

    # Growth inputs
    gr_revenue_cagr: float = 0.50
    gr_eps_cagr: float = 0.50

    # Valuation inputs
    va_pe_vs_own_history: float = 0.40
    va_pe_vs_sector: float = 0.30
    va_dividend_yield_vs_history: float = 0.30

    # Momentum inputs
    mo_return_3m: float = 0.30
    mo_return_6m: float = 0.30
    mo_return_12m: float = 0.20
    mo_volume_trend: float = 0.20


@dataclass(frozen=True)
class CompositeWeights:
    # Business Score = weighted roll-up of quality/health/growth
    business_from_quality: float = 0.40
    business_from_health: float = 0.35
    business_from_growth: float = 0.25

    # Investment Score = valuation + business quality + momentum
    investment_from_valuation: float = 0.55
    investment_from_business: float = 0.30
    investment_from_momentum: float = 0.15


@dataclass(frozen=True)
class HorizonThresholds:
    compounder_business_min: float = 75.0
    compounder_investment_min: float = 65.0
    hold_business_min: float = 75.0
    watch_business_min: float = 55.0
    watch_investment_min: float = 55.0
    short_term_momentum_min: float = 70.0
    min_years_for_full_confidence: int = 5
    min_years_required: int = 2  # below this, refuse to score


@dataclass(frozen=True)
class ThesisAlertThresholds:
    """Default triggers for the 'what would change my mind' monitor."""

    min_roe_pct: float = 12.0
    max_consecutive_eps_declines: int = 2
    max_debt_to_ebitda: float = 3.5
    min_dividend_coverage: float = 1.2  # FCF / dividends paid


def _validate() -> None:
    w = SubScoreWeights()
    groups = {
        "business_quality": [w.bq_competitive_position, w.bq_roe_roic, w.bq_revenue_durability, w.bq_capital_allocation],
        "financial_health": [w.fh_debt_coverage, w.fh_fcf_conversion, w.fh_liquidity, w.fh_earnings_consistency],
        "growth": [w.gr_revenue_cagr, w.gr_eps_cagr],
        "valuation": [w.va_pe_vs_own_history, w.va_pe_vs_sector, w.va_dividend_yield_vs_history],
        "momentum": [w.mo_return_3m, w.mo_return_6m, w.mo_return_12m, w.mo_volume_trend],
    }
    for name, values in groups.items():
        total = round(sum(values), 6)
        if total != 1.0:
            raise ValueError(f"Weight group '{name}' sums to {total}, expected 1.0")

    c = CompositeWeights()
    if round(c.business_from_quality + c.business_from_health + c.business_from_growth, 6) != 1.0:
        raise ValueError("Business composite weights must sum to 1.0")
    if round(c.investment_from_valuation + c.investment_from_business + c.investment_from_momentum, 6) != 1.0:
        raise ValueError("Investment composite weights must sum to 1.0")


_validate()

SUB_SCORE_WEIGHTS = SubScoreWeights()
COMPOSITE_WEIGHTS = CompositeWeights()
HORIZON_THRESHOLDS = HorizonThresholds()
THESIS_THRESHOLDS = ThesisAlertThresholds()
