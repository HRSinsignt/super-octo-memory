"""
Data models for the JSE Investment Horizon platform.

These are the canonical shapes that flow through the pipeline:
raw data source -> CompanyFinancials/PriceHistory -> ScoreResult -> Report.

Keeping these as plain dataclasses (rather than dicts) means every stage
of the pipeline gets type-checking and autocomplete, and new data sources
just need to produce these shapes to plug into the rest of the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class Sector(str, Enum):
    FINANCE = "finance"
    MANUFACTURING = "manufacturing"
    RETAIL_DISTRIBUTION = "retail_distribution"
    TOURISM_LEISURE = "tourism_leisure"
    TELECOM_MEDIA = "telecom_media"
    REAL_ESTATE = "real_estate"
    ENERGY_UTILITIES = "energy_utilities"
    CONGLOMERATE = "conglomerate"
    OTHER = "other"


@dataclass
class AnnualFinancials:
    """
    One fiscal year (or best-available period) of fundamentals for a company.

    Only fiscal_year, revenue, net_income, eps, total_equity, and total_assets
    are required — these are reliably available from most data sources,
    including Stacks' JSE API. Everything else is Optional because several
    real providers (Stacks included) don't expose it directly: total debt,
    EBITDA, interest expense, operating/free cash flow, current
    assets/liabilities, dividend per share, and shares outstanding.

    Scoring functions must treat these Optional fields as "unknown" (skip
    that sub-component, don't assume 0) — see scoring.py. Fabricating a 0
    for missing debt or EBITDA would silently produce a wrong Financial
    Health score instead of an honestly lower-confidence one.
    """

    fiscal_year: int
    revenue: float
    net_income: float
    eps: float
    total_equity: float
    total_assets: float
    total_debt: Optional[float] = None
    ebitda: Optional[float] = None
    interest_expense: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    free_cash_flow: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    dividend_per_share: Optional[float] = None
    shares_outstanding: Optional[float] = None


@dataclass
class PricePoint:
    trade_date: date
    close: float
    volume: float


@dataclass
class CompanyFinancials:
    """
    Everything the scoring engine needs for one company. `history` should
    be ordered oldest -> newest and cover at minimum 5 fiscal years for
    CAGR/trend calculations to be meaningful; the engine will compute
    partial scores with fewer years but will flag reduced confidence.
    """

    ticker: str
    name: str
    sector: Sector
    history: list[AnnualFinancials] = field(default_factory=list)
    current_price: Optional[float] = None
    price_history: list[PricePoint] = field(default_factory=list)  # for momentum
    market_index_history: list[PricePoint] = field(default_factory=list)  # JSE index, same dates

    def latest(self) -> Optional[AnnualFinancials]:
        return self.history[-1] if self.history else None

    def years_available(self) -> int:
        return len(self.history)


class Horizon(str, Enum):
    LONG_TERM_COMPOUNDER = "Long-Term Compounder"
    LONG_TERM_HOLD = "Long-Term Hold"
    LONG_TERM_WATCH = "Long-Term Watch"
    SHORT_TERM_OPPORTUNITY = "Short-Term Opportunity"
    SPECULATIVE_AVOID = "Speculative / Avoid for Long Term"
    INSUFFICIENT_DATA = "Insufficient Data"


@dataclass
class SubScores:
    business_quality: Optional[float]
    financial_health: Optional[float]
    growth: Optional[float]
    valuation: Optional[float]
    momentum: Optional[float]


@dataclass
class ThesisCondition:
    """A monitorable condition that, if triggered, should degrade a rating."""

    description: str
    triggered: bool
    detail: str


@dataclass
class ScoreResult:
    ticker: str
    name: str
    sub_scores: SubScores
    business_score: Optional[float]
    investment_score: Optional[float]
    horizon: Horizon
    data_confidence: float  # 0-1, based on years of history / completeness
    thesis_conditions: list[ThesisCondition] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
