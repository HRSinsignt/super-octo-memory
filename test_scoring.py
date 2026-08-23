import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jse_platform.horizon import classify_horizon
from jse_platform.models import (
    AnnualFinancials,
    CompanyFinancials,
    Horizon,
    PricePoint,
    Sector,
    SubScores,
)
from jse_platform.pipeline import _own_history_ranges, score_company
from jse_platform.scoring import (
    compute_business_score,
    compute_investment_score,
    compute_sub_scores,
    score_financial_health,
    score_growth,
)
from jse_platform.statistics_utils import cagr, coefficient_of_variation, linear_scale, percentile_rank
from jse_platform.thesis import check_debt_ceiling, check_roe_floor


def make_annual(year, revenue, net_income, eps, debt=1000, ebitda=2000, **overrides):
    defaults = dict(
        fiscal_year=year,
        revenue=revenue,
        net_income=net_income,
        eps=eps,
        total_debt=debt,
        ebitda=ebitda,
        interest_expense=200,
        operating_cash_flow=net_income * 1.1,
        free_cash_flow=net_income * 0.9,
        total_equity=net_income * 8,
        total_assets=net_income * 20,
        current_assets=net_income * 5,
        current_liabilities=net_income * 3,
        dividend_per_share=eps * 0.3,
        shares_outstanding=1000,
    )
    defaults.update(overrides)
    return AnnualFinancials(**defaults)


def make_strong_company(years=5) -> CompanyFinancials:
    history = []
    revenue, net_income, eps = 10000, 1500, 1.5
    for i in range(years):
        history.append(make_annual(2020 + i, revenue, net_income, eps, debt=1500, ebitda=2500))
        revenue *= 1.10
        net_income *= 1.12
        eps *= 1.12
    from datetime import timedelta
    base = date(2024, 1, 1)
    prices = [PricePoint(base + timedelta(days=i), close=50 + i * 0.05, volume=100000) for i in range(1, 300)]
    index = [PricePoint(base + timedelta(days=i), close=420000 + i * 50, volume=0) for i in range(1, 300)]
    return CompanyFinancials(
        ticker="TEST", name="Test Strong Co", sector=Sector.MANUFACTURING,
        history=history, current_price=history[-1].eps * 12,
        price_history=prices, market_index_history=index,
    )


def make_weak_company(years=2) -> CompanyFinancials:
    history = [
        make_annual(2023, 5000, -200, -0.2, debt=8000, ebitda=400),
        make_annual(2024, 4800, -400, -0.4, debt=8500, ebitda=300),
    ][:years]
    return CompanyFinancials(
        ticker="WEAK", name="Weak Co", sector=Sector.OTHER,
        history=history, current_price=2.0,
    )


# --- statistics_utils -------------------------------------------------

def test_cagr_basic():
    assert round(cagr(100, 200, 5), 2) == round(((2) ** (1 / 5) - 1) * 100, 2)


def test_cagr_invalid_inputs_return_none():
    assert cagr(0, 100, 5) is None
    assert cagr(100, 100, 0) is None


def test_percentile_rank_empty_universe_is_neutral():
    assert percentile_rank(50, []) == 50.0


def test_percentile_rank_middle_of_universe():
    assert percentile_rank(50, [10, 20, 50, 80, 90]) == 60.0


def test_linear_scale_clamps():
    assert linear_scale(1000, 0, 100) == 100
    assert linear_scale(-50, 0, 100) == 0


def test_linear_scale_invert():
    low_is_good = linear_scale(0, 0, 100, invert=True)
    high_is_bad = linear_scale(100, 0, 100, invert=True)
    assert low_is_good == 100
    assert high_is_bad == 0


def test_coefficient_of_variation_needs_two_points():
    assert coefficient_of_variation([5]) is None
    assert coefficient_of_variation([5, 5, 5]) == 0.0


# --- scoring ------------------------------------------------------------

def test_growth_score_none_with_insufficient_history():
    company = make_weak_company(years=1)
    assert score_growth(company) is None


def test_growth_score_positive_for_growing_company():
    company = make_strong_company()
    result = score_growth(company)
    assert result is not None
    assert result > 50  # consistently growing revenue/EPS should score above midpoint


def test_financial_health_penalizes_high_debt_low_ebitda():
    weak = make_weak_company()
    strong = make_strong_company()
    weak_score = score_financial_health(weak)
    strong_score = score_financial_health(strong)
    assert strong_score is not None and weak_score is not None
    assert strong_score > weak_score


def test_composite_scores_none_when_all_inputs_missing():
    empty_sub = SubScores(None, None, None, None, None)
    assert compute_business_score(empty_sub) is None
    assert compute_investment_score(empty_sub, None) is None


def test_composite_business_score_uses_available_subset():
    partial = SubScores(business_quality=80, financial_health=None, growth=None, valuation=None, momentum=None)
    # Only business_quality available -> business score should equal it exactly
    # (weighted average renormalizes over the single available component).
    assert compute_business_score(partial) == 80


# --- horizon classification ----------------------------------------------

def test_insufficient_data_with_too_little_history():
    sub = SubScores(70, 70, 70, 70, 70)
    result = classify_horizon(70, 70, sub, years_available=0)
    assert result == Horizon.INSUFFICIENT_DATA


def test_compounder_requires_both_high_business_and_investment():
    sub = SubScores(90, 90, 90, 90, 50)
    result = classify_horizon(business_score=90, investment_score=80, sub_scores=sub, years_available=5)
    assert result == Horizon.LONG_TERM_COMPOUNDER


def test_high_business_low_investment_is_long_term_hold():
    sub = SubScores(90, 90, 90, 30, 20)
    result = classify_horizon(business_score=90, investment_score=40, sub_scores=sub, years_available=5)
    assert result == Horizon.LONG_TERM_HOLD


def test_weak_business_high_momentum_is_short_term_opportunity():
    sub = SubScores(40, 40, 40, 85, 90)
    result = classify_horizon(business_score=40, investment_score=60, sub_scores=sub, years_available=5)
    assert result == Horizon.SHORT_TERM_OPPORTUNITY


def test_weak_everything_is_speculative():
    sub = SubScores(30, 30, 30, 30, 20)
    result = classify_horizon(business_score=30, investment_score=30, sub_scores=sub, years_available=5)
    assert result == Horizon.SPECULATIVE_AVOID


# --- thesis monitoring -----------------------------------------------------

def test_roe_floor_not_triggered_for_healthy_company():
    company = make_strong_company()
    condition = check_roe_floor(company)
    assert condition.triggered is False


def test_debt_ceiling_triggered_for_overleveraged_company():
    company = make_weak_company()
    condition = check_debt_ceiling(company)
    assert condition.triggered is True


def test_full_sub_score_pipeline_runs_without_error():
    company = make_strong_company()
    sub = compute_sub_scores(company)
    business = compute_business_score(sub)
    investment = compute_investment_score(sub, business)
    assert business is not None
    assert investment is not None


# --- own-history valuation ranges -----------------------------------------

def test_own_history_ranges_derived_from_price_series():
    company = make_strong_company()
    pe_range, yield_range = _own_history_ranges(company)
    assert pe_range is not None
    assert pe_range[0] < pe_range[1]
    # Test data pays a dividend, so a yield range should also come back.
    assert yield_range is not None
    assert yield_range[0] < yield_range[1]


def test_own_history_ranges_none_without_price_history():
    company = make_weak_company()  # no price_history supplied
    pe_range, yield_range = _own_history_ranges(company)
    assert pe_range is None
    assert yield_range is None


def test_score_company_valuation_not_flat_fifty_with_only_self_as_sector_peer():
    # A lone company in its "sector" makes sector-relative PE degenerate to
    # a meaningless flat 50 (it's always exactly its own median). Own-history
    # ranges should still give a non-degenerate valuation score.
    company = make_strong_company()
    context = {"margin_medians": [], "sector_pe_median": None}
    result = score_company(company, sector_context=context)
    assert result.sub_scores.valuation is not None
    assert result.sub_scores.valuation != 50.0
