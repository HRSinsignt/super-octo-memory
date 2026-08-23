"""Background JSE research agent.

The agent does not invent facts. It generates structured research questions,
fetches data through a DataSource, calculates signals with the scoring engine,
and stores concise, auditable insights. An LLM can later be placed on top of
these verified facts for richer language, but the facts remain deterministic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from .models import CompanyFinancials, ScoreResult
from .pipeline import score_company


@dataclass
class ResearchQuestion:
    category: str
    question: str


@dataclass
class Insight:
    ticker: str
    company_name: str
    category: str
    title: str
    summary: str
    severity: str
    confidence: float
    evidence: list[str]
    created_at: str


QUESTION_TEMPLATES = [
    ResearchQuestion("valuation", "Is the current valuation attractive relative to earnings and peers?"),
    ResearchQuestion("business_quality", "Has the underlying business's competitive position or capital discipline changed?"),
    ResearchQuestion("financial_health", "Has the company's financial health strengthened or weakened?"),
    ResearchQuestion("growth", "Are revenue and earnings showing durable growth?"),
    ResearchQuestion("dividend", "Is the dividend profile attractive and consistent?"),
    ResearchQuestion("momentum", "Has price or volume momentum changed materially?"),
    ResearchQuestion("thesis", "What could invalidate the current investment thesis?"),
]


def _score_label(value: Optional[float]) -> str:
    if value is None:
        return "insufficient data"
    if value >= 75:
        return "strong"
    if value >= 55:
        return "moderate"
    return "weak"


def generate_insights(company: CompanyFinancials, result: ScoreResult) -> list[Insight]:
    """Generate auditable insights from a scored company."""
    now = datetime.now(timezone.utc).isoformat()
    s = result.sub_scores
    out: list[Insight] = []

    if s.valuation is not None and s.valuation >= 75:
        evidence = [f"Valuation score: {s.valuation:.0f}/100"]
        if company.current_price and company.latest() and company.latest().eps > 0:
            pe = company.current_price / company.latest().eps
            evidence.append(f"Current P/E proxy: {pe:.2f}x")
        out.append(Insight(company.ticker, company.name, "valuation", "Potentially attractive valuation",
                           "The scoring model currently finds the stock's valuation attractive relative to the available comparison data.",
                           "positive", result.data_confidence, evidence, now))
    elif s.valuation is not None and s.valuation <= 35:
        out.append(Insight(company.ticker, company.name, "valuation", "Valuation needs caution",
                           "The current valuation score is weak; price may be demanding relative to the available earnings and peer data.",
                           "warning", result.data_confidence, [f"Valuation score: {s.valuation:.0f}/100"], now))

    if s.financial_health is not None and s.financial_health <= 40:
        out.append(Insight(company.ticker, company.name, "financial_health", "Financial health weakening",
                           "The financial-health score is below the platform's caution threshold. Investigate the underlying statements before relying on the rating.",
                           "negative", result.data_confidence, [f"Financial health: {s.financial_health:.0f}/100"], now))
    elif s.financial_health is not None and s.financial_health >= 75:
        out.append(Insight(company.ticker, company.name, "financial_health", "Strong financial health",
                           "The available financial indicators are scoring strongly.", "positive", result.data_confidence,
                           [f"Financial health: {s.financial_health:.0f}/100"], now))

    if s.growth is not None and s.growth >= 75:
        out.append(Insight(company.ticker, company.name, "growth", "Growth signal is strong",
                           "Revenue and/or earnings growth are scoring strongly against the available benchmarks.", "positive",
                           result.data_confidence, [f"Growth score: {s.growth:.0f}/100"], now))
    elif s.growth is not None and s.growth <= 35:
        out.append(Insight(company.ticker, company.name, "growth", "Growth signal is weak",
                           "Growth indicators are currently below the platform's caution threshold.", "warning",
                           result.data_confidence, [f"Growth score: {s.growth:.0f}/100"], now))

    triggered = [c.description for c in result.thesis_conditions if c.triggered]
    if triggered:
        out.append(Insight(company.ticker, company.name, "thesis", "Investment thesis monitor triggered",
                           "One or more conditions that could weaken the current thesis have been triggered.", "negative",
                           result.data_confidence, triggered, now))

    return out


def research_questions_for_company(company: CompanyFinancials) -> list[ResearchQuestion]:
    return QUESTION_TEMPLATES.copy()


def serialize_insights(insights: list[Insight]) -> list[dict]:
    return [asdict(i) for i in insights]
