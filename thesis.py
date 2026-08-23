"""
'What would change my mind' thesis monitoring.

Each function checks one concrete, falsifiable condition against a
company's latest data and returns a ThesisCondition. Run `evaluate_thesis`
on a company each time new financials or prices land (e.g. after each
earnings release) to detect thesis decay, rather than only re-scoring
on a fixed schedule.

Thresholds live in config.py so they can be tuned per-sector later
without touching this logic.
"""

from __future__ import annotations

from .config import THESIS_THRESHOLDS as T
from .models import CompanyFinancials, ThesisCondition


def check_roe_floor(company: CompanyFinancials) -> ThesisCondition:
    latest = company.latest()
    if latest is None or not latest.total_equity:
        return ThesisCondition("ROE stays above floor", triggered=False, detail="Insufficient data")
    roe = latest.net_income / latest.total_equity * 100
    triggered = roe < T.min_roe_pct
    return ThesisCondition(
        description=f"ROE stays above {T.min_roe_pct}%",
        triggered=triggered,
        detail=f"Current ROE: {roe:.1f}%",
    )


def check_consecutive_eps_declines(company: CompanyFinancials) -> ThesisCondition:
    history = company.history
    if len(history) < T.max_consecutive_eps_declines + 1:
        return ThesisCondition(
            description=f"No {T.max_consecutive_eps_declines}+ consecutive quarterly/annual EPS declines",
            triggered=False,
            detail="Insufficient history",
        )
    recent = history[-(T.max_consecutive_eps_declines + 1):]
    declines = sum(1 for i in range(1, len(recent)) if recent[i].eps < recent[i - 1].eps)
    triggered = declines >= T.max_consecutive_eps_declines
    return ThesisCondition(
        description=f"No {T.max_consecutive_eps_declines}+ consecutive periods of EPS decline",
        triggered=triggered,
        detail=f"{declines} declining period(s) in the last {len(recent) - 1}",
    )


def check_debt_ceiling(company: CompanyFinancials) -> ThesisCondition:
    latest = company.latest()
    if latest is None or latest.total_debt is None or not latest.ebitda:
        return ThesisCondition("Debt/EBITDA stays under ceiling", triggered=False, detail="Data not available from source")
    ratio = latest.total_debt / latest.ebitda
    triggered = ratio > T.max_debt_to_ebitda
    return ThesisCondition(
        description=f"Debt/EBITDA stays under {T.max_debt_to_ebitda}x",
        triggered=triggered,
        detail=f"Current: {ratio:.2f}x",
    )


def check_dividend_coverage(company: CompanyFinancials) -> ThesisCondition:
    latest = company.latest()
    if (
        latest is None
        or not latest.dividend_per_share
        or not latest.shares_outstanding
        or latest.free_cash_flow is None
    ):
        return ThesisCondition("Dividend stays covered by free cash flow", triggered=False, detail="No dividend or data not available from source")
    total_dividends_paid = latest.dividend_per_share * latest.shares_outstanding
    coverage = latest.free_cash_flow / total_dividends_paid if total_dividends_paid else None
    if coverage is None:
        return ThesisCondition("Dividend stays covered by free cash flow", triggered=False, detail="Insufficient data")
    triggered = coverage < T.min_dividend_coverage
    return ThesisCondition(
        description=f"FCF covers dividend by at least {T.min_dividend_coverage}x",
        triggered=triggered,
        detail=f"Current coverage: {coverage:.2f}x",
    )


def evaluate_thesis(company: CompanyFinancials) -> list[ThesisCondition]:
    """Run all standard thesis checks. Extend this list with company- or
    sector-specific conditions as your coverage grows."""
    return [
        check_roe_floor(company),
        check_consecutive_eps_declines(company),
        check_debt_ceiling(company),
        check_dividend_coverage(company),
    ]
