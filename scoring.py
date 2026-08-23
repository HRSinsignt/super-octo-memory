"""
Scoring engine: turns raw CompanyFinancials into the five sub-scores,
then rolls those up into Business Score and Investment Score.

Every scoring function returns None (not 0) when it lacks the data to
compute a meaningful number. None propagates through the composites as
"reduce confidence, don't fabricate a score" - see `_weighted_average`.
This is deliberate: a company with 2 years of data should never receive
a Growth score that reads as authoritative as one with 10 years.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .config import SUB_SCORE_WEIGHTS as W
from .config import COMPOSITE_WEIGHTS as CW
from .models import CompanyFinancials, SubScores
from .statistics_utils import cagr, clamp, coefficient_of_variation, linear_scale, percentile_rank


def _weighted_average(pairs: Sequence[tuple[Optional[float], float]]) -> Optional[float]:
    """
    Weighted average that re-normalizes over only the available (non-None)
    components, so missing data lowers confidence upstream rather than
    silently zeroing out part of the score.
    """
    available = [(v, w) for v, w in pairs if v is not None]
    if not available:
        return None
    total_weight = sum(w for _, w in available)
    if total_weight == 0:
        return None
    return sum(v * w for v, w in available) / total_weight


# ---------------------------------------------------------------------------
# Business Quality
# ---------------------------------------------------------------------------

def score_business_quality(
    company: CompanyFinancials,
    sector_margin_medians: Optional[Sequence[float]] = None,
) -> Optional[float]:
    history = company.history
    if len(history) < 2:
        return None

    # Competitive position: margin stability + level vs sector peers.
    margins = [h.net_income / h.revenue for h in history if h.revenue]
    margin_level = margins[-1] * 100 if margins else None
    competitive_position = None
    if margin_level is not None:
        if sector_margin_medians:
            competitive_position = percentile_rank(margin_level, sector_margin_medians)
        else:
            # Fall back to an absolute band typical of JSE non-financial names.
            competitive_position = linear_scale(margin_level, low=0, high=20)

    # ROE / ROIC trend (5yr average preferred, uses what's available).
    roes = [h.net_income / h.total_equity * 100 for h in history if h.total_equity]
    roe_avg = sum(roes) / len(roes) if roes else None
    roe_score = linear_scale(roe_avg, low=5, high=25) if roe_avg is not None else None

    # Revenue durability: inverse of revenue volatility (proxy for
    # customer concentration / cyclicality risk, since we don't have
    # direct customer-concentration data from financial statements).
    revenues = [h.revenue for h in history]
    cv = coefficient_of_variation(revenues)
    durability_score = linear_scale(cv, low=0.05, high=0.40, invert=True) if cv is not None else None

    # Capital allocation: dividend consistency + debt discipline trend.
    # Both dividend_per_share and total_debt may be unavailable from some
    # data sources (e.g. Stacks doesn't expose total_debt directly) — treat
    # missing data as "unknown", not "zero/bad".
    dividends = [h.dividend_per_share for h in history if h.dividend_per_share is not None]
    paid_every_year = len(dividends) == len(history) and all(d > 0 for d in dividends) if dividends else False
    debt_values = [h.total_debt for h in history if h.total_debt is not None]
    debt_trend_improving = debt_values[-1] <= debt_values[0] if len(debt_values) >= 2 else None
    capital_allocation_score = 50.0
    if dividends:
        capital_allocation_score += 25 if paid_every_year else 0
    if debt_trend_improving is not None:
        capital_allocation_score += 25 if debt_trend_improving else 0
    capital_allocation_score = clamp(capital_allocation_score)

    return _weighted_average([
        (competitive_position, W.bq_competitive_position),
        (roe_score, W.bq_roe_roic),
        (durability_score, W.bq_revenue_durability),
        (capital_allocation_score, W.bq_capital_allocation),
    ])


# ---------------------------------------------------------------------------
# Financial Health
# ---------------------------------------------------------------------------

def score_financial_health(company: CompanyFinancials) -> Optional[float]:
    """
    Every input here may be unavailable depending on the data source —
    e.g. Stacks' JSE API does not expose total_debt, EBITDA, current
    assets/liabilities, or free cash flow directly. Each component
    returns None (not a fabricated value) when its inputs are missing,
    and _weighted_average renormalizes over whatever is actually
    available. With only EPS-consistency available, this score is real
    but low-confidence — check the resulting ScoreResult.data_confidence.
    """
    latest = company.latest()
    if latest is None:
        return None

    # Debt / EBITDA and interest coverage.
    debt_to_ebitda = (
        latest.total_debt / latest.ebitda
        if latest.total_debt is not None and latest.ebitda
        else None
    )
    debt_score = linear_scale(debt_to_ebitda, low=0.5, high=5.0, invert=True) if debt_to_ebitda is not None else None

    interest_coverage = (
        latest.ebitda / latest.interest_expense
        if latest.ebitda is not None and latest.interest_expense
        else None
    )
    coverage_score = linear_scale(interest_coverage, low=1.0, high=10.0) if interest_coverage is not None else None
    debt_coverage_score = _weighted_average([(debt_score, 0.5), (coverage_score, 0.5)])

    # Free cash flow conversion.
    fcf_conversion = (
        latest.free_cash_flow / latest.net_income
        if latest.free_cash_flow is not None and latest.net_income
        else None
    )
    fcf_score = linear_scale(fcf_conversion, low=0.3, high=1.3) if fcf_conversion is not None else None

    # Liquidity.
    current_ratio = (
        latest.current_assets / latest.current_liabilities
        if latest.current_assets is not None and latest.current_liabilities
        else None
    )
    liquidity_score = linear_scale(current_ratio, low=0.8, high=2.5) if current_ratio is not None else None

    # Earnings consistency across available history — this is the one
    # component that only needs EPS, so it's the most reliably available
    # regardless of data source.
    eps_series = [h.eps for h in company.history]
    cv = coefficient_of_variation(eps_series)
    consistency_score = linear_scale(cv, low=0.05, high=0.60, invert=True) if cv is not None else None

    return _weighted_average([
        (debt_coverage_score, W.fh_debt_coverage),
        (fcf_score, W.fh_fcf_conversion),
        (liquidity_score, W.fh_liquidity),
        (consistency_score, W.fh_earnings_consistency),
    ])


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------

def score_growth(
    company: CompanyFinancials,
    sector_revenue_cagrs: Optional[Sequence[float]] = None,
    sector_eps_cagrs: Optional[Sequence[float]] = None,
) -> Optional[float]:
    history = company.history
    if len(history) < 2:
        return None

    years = history[-1].fiscal_year - history[0].fiscal_year
    revenue_growth = cagr(history[0].revenue, history[-1].revenue, years)
    eps_growth = cagr(history[0].eps, history[-1].eps, years) if history[0].eps > 0 else None

    revenue_score = (
        percentile_rank(revenue_growth, sector_revenue_cagrs)
        if revenue_growth is not None and sector_revenue_cagrs
        else linear_scale(revenue_growth, low=-5, high=15) if revenue_growth is not None
        else None
    )
    eps_score = (
        percentile_rank(eps_growth, sector_eps_cagrs)
        if eps_growth is not None and sector_eps_cagrs
        else linear_scale(eps_growth, low=-5, high=20) if eps_growth is not None
        else None
    )

    return _weighted_average([
        (revenue_score, W.gr_revenue_cagr),
        (eps_score, W.gr_eps_cagr),
    ])


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------

def score_valuation(
    company: CompanyFinancials,
    historical_pe_range: Optional[tuple[float, float]] = None,
    sector_pe_median: Optional[float] = None,
    historical_yield_range: Optional[tuple[float, float]] = None,
) -> Optional[float]:
    latest = company.latest()
    if latest is None or company.current_price is None or latest.eps <= 0:
        return None

    current_pe = company.current_price / latest.eps

    pe_vs_history_score = None
    if historical_pe_range:
        low, high = historical_pe_range
        # Cheap relative to own history = high score, hence invert=True
        # (low PE relative to range -> attractive -> high score).
        pe_vs_history_score = linear_scale(current_pe, low=low, high=high, invert=True)

    pe_vs_sector_score = None
    if sector_pe_median:
        pe_vs_sector_score = linear_scale(current_pe, low=sector_pe_median * 0.5, high=sector_pe_median * 1.5, invert=True)

    dividend_yield = (
        latest.dividend_per_share / company.current_price * 100
        if latest.dividend_per_share is not None and company.current_price
        else None
    )
    yield_score = None
    if dividend_yield is not None and historical_yield_range:
        low, high = historical_yield_range
        yield_score = linear_scale(dividend_yield, low=low, high=high)

    return _weighted_average([
        (pe_vs_history_score, W.va_pe_vs_own_history),
        (pe_vs_sector_score, W.va_pe_vs_sector),
        (yield_score, W.va_dividend_yield_vs_history),
    ])


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------

def score_momentum(company: CompanyFinancials) -> Optional[float]:
    prices = company.price_history
    index = company.market_index_history
    if len(prices) < 2:
        return None

    def relative_return(days: int) -> Optional[float]:
        if len(prices) < 2:
            return None
        target = prices[-1]
        # find the closest point ~days back
        candidates = [p for p in prices if (target.trade_date - p.trade_date).days >= days]
        if not candidates:
            return None
        start = candidates[-1]
        stock_return = (target.close / start.close - 1) * 100 if start.close else None
        if stock_return is None:
            return None
        if index:
            idx_candidates = [p for p in index if (index[-1].trade_date - p.trade_date).days >= days]
            if idx_candidates and index[-1].close and idx_candidates[-1].close:
                index_return = (index[-1].close / idx_candidates[-1].close - 1) * 100
                return stock_return - index_return
        return stock_return  # absolute return if no index to compare

    r3 = relative_return(90)
    r6 = relative_return(180)
    r12 = relative_return(365)

    r3_score = linear_scale(r3, low=-20, high=20) if r3 is not None else None
    r6_score = linear_scale(r6, low=-25, high=25) if r6 is not None else None
    r12_score = linear_scale(r12, low=-30, high=30) if r12 is not None else None

    volume_score = None
    if len(prices) >= 90:
        recent_20 = [p.volume for p in prices[-20:]]
        # Compare recent volume to the 70 days *before* that window, not an
        # overlapping "prior 90" that already contains the recent 20 days —
        # the overlap mutes any real pickup or drop-off in the ratio.
        prior_70 = [p.volume for p in prices[-90:-20]]
        if recent_20 and prior_70:
            avg_20 = sum(recent_20) / len(recent_20)
            avg_70 = sum(prior_70) / len(prior_70)
            volume_ratio = (avg_20 / avg_70) if avg_70 else None
            if volume_ratio is not None:
                volume_score = linear_scale(volume_ratio, low=0.5, high=2.0)

    return _weighted_average([
        (r3_score, W.mo_return_3m),
        (r6_score, W.mo_return_6m),
        (r12_score, W.mo_return_12m),
        (volume_score, W.mo_volume_trend),
    ])


# ---------------------------------------------------------------------------
# Composites
# ---------------------------------------------------------------------------

def compute_sub_scores(
    company: CompanyFinancials,
    sector_context: Optional[dict] = None,
) -> SubScores:
    ctx = sector_context or {}
    return SubScores(
        business_quality=score_business_quality(company, ctx.get("margin_medians")),
        financial_health=score_financial_health(company),
        growth=score_growth(company, ctx.get("revenue_cagrs"), ctx.get("eps_cagrs")),
        valuation=score_valuation(
            company,
            ctx.get("historical_pe_range"),
            ctx.get("sector_pe_median"),
            ctx.get("historical_yield_range"),
        ),
        momentum=score_momentum(company),
    )


def compute_business_score(sub: SubScores) -> Optional[float]:
    return _weighted_average([
        (sub.business_quality, CW.business_from_quality),
        (sub.financial_health, CW.business_from_health),
        (sub.growth, CW.business_from_growth),
    ])


def compute_investment_score(sub: SubScores, business_score: Optional[float]) -> Optional[float]:
    return _weighted_average([
        (sub.valuation, CW.investment_from_valuation),
        (business_score, CW.investment_from_business),
        (sub.momentum, CW.investment_from_momentum),
    ])
