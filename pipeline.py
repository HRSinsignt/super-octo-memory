"""
Orchestrates the full flow: DataSource -> sub-scores -> composites ->
horizon classification -> thesis conditions -> ScoreResult.

This is the single entry point most callers (CLI, API, scheduled job)
should use rather than calling scoring.py functions directly.
"""

from __future__ import annotations

from typing import Optional

from .data_sources.base import DataSource
from .horizon import classify_horizon, data_confidence
from .models import CompanyFinancials, ScoreResult
from .scoring import compute_business_score, compute_investment_score, compute_sub_scores
from .thesis import evaluate_thesis


def build_sector_context(companies: list[CompanyFinancials]) -> dict[str, dict]:
    """
    Precompute cross-sectional stats (margin medians, growth distributions,
    PE medians) per sector, so each company's Business/Growth/Valuation
    scores are ranked against actual JSE peers rather than fixed absolute
    bands. Call this once per batch run, then pass the relevant slice
    into score_company for each company.
    """
    by_sector: dict[str, list[CompanyFinancials]] = {}
    for c in companies:
        by_sector.setdefault(c.sector.value, []).append(c)

    context: dict[str, dict] = {}
    for sector, group in by_sector.items():
        margins = [
            c.latest().net_income / c.latest().revenue * 100
            for c in group
            if c.latest() and c.latest().revenue
        ]
        pes = [
            c.current_price / c.latest().eps
            for c in group
            if c.current_price and c.latest() and c.latest().eps and c.latest().eps > 0
        ]
        context[sector] = {
            "margin_medians": margins,
            "sector_pe_median": sorted(pes)[len(pes) // 2] if pes else None,
        }
    return context


def _own_history_ranges(
    company: CompanyFinancials,
) -> tuple[Optional[tuple[float, float]], Optional[tuple[float, float]]]:
    """
    Derive a company's own historical P/E and dividend-yield ranges from its
    price history, so valuation can be judged against where the stock itself
    has traded — not only against sector peers (which, with a handful of
    covered names per sector, often collapses to a single-company "median"
    that always scores as a flat, uninformative 50).

    This divides the full price series by the latest reported EPS/DPS
    rather than re-pricing against whatever EPS was current on each
    historical date — an approximation, since most data sources (including
    Stacks) don't reliably expose quarter-by-quarter trailing EPS. It's a
    real, own-history-derived range rather than no range at all, but treat
    it as directional, not precise, especially for companies whose EPS has
    moved a lot over the price window.
    """
    latest = company.latest()
    if latest is None or not company.price_history:
        return None, None

    closes = [p.close for p in company.price_history if p.close]
    if len(closes) < 20:  # too little price history for a meaningful range
        return None, None

    pe_range = None
    if latest.eps and latest.eps > 0:
        pes = [c / latest.eps for c in closes]
        pe_range = (min(pes), max(pes))

    yield_range = None
    if latest.dividend_per_share:
        yields = [(latest.dividend_per_share / c) * 100 for c in closes if c]
        if yields:
            yield_range = (min(yields), max(yields))

    return pe_range, yield_range


def score_company(
    company: CompanyFinancials,
    sector_context: Optional[dict] = None,
    historical_pe_range: Optional[tuple[float, float]] = None,
    historical_yield_range: Optional[tuple[float, float]] = None,
) -> ScoreResult:
    ctx = dict(sector_context or {})
    auto_pe_range, auto_yield_range = _own_history_ranges(company)
    # Explicit ranges (e.g. supplied by a caller with a curated data set)
    # take precedence; otherwise fall back to what we can derive from the
    # company's own price history.
    ctx["historical_pe_range"] = historical_pe_range or auto_pe_range
    ctx["historical_yield_range"] = historical_yield_range or auto_yield_range

    sub_scores = compute_sub_scores(company, ctx)
    business_score = compute_business_score(sub_scores)
    investment_score = compute_investment_score(sub_scores, business_score)
    years = company.years_available()
    horizon = classify_horizon(business_score, investment_score, sub_scores, years)
    conditions = evaluate_thesis(company)

    notes = []
    if years < 5:
        notes.append(
            f"Only {years} year(s) of financial history available — scores carry reduced confidence."
        )
    if any(c.triggered for c in conditions):
        notes.append("One or more thesis-monitoring conditions have been triggered — see thesis_conditions.")

    return ScoreResult(
        ticker=company.ticker,
        name=company.name,
        sub_scores=sub_scores,
        business_score=business_score,
        investment_score=investment_score,
        horizon=horizon,
        data_confidence=data_confidence(years),
        thesis_conditions=conditions,
        notes=notes,
    )


def run_pipeline(source: DataSource) -> list[ScoreResult]:
    companies = source.get_all()
    sector_context_by_sector = build_sector_context(companies)
    results = []
    for company in companies:
        ctx = sector_context_by_sector.get(company.sector.value, {})
        results.append(score_company(company, sector_context=ctx))
    return results
