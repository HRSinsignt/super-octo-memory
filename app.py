from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jse_platform.data_sources.csv_source import CSVDataSource
from jse_platform.pipeline import run_pipeline, score_company, build_sector_context
from jse_platform.research_agent import research_questions_for_company, serialize_insights
from jse_platform.research_cycle import run_cycle_and_persist

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA = ROOT / "sample_data"
app = FastAPI(title="JSE AI Intelligence Platform", version="0.2.0")
app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")

# MVP uses the existing deterministic CSV engine. Set JSE_DATA_MODE=stacks and
# STACKS_API_KEY to switch the provider once a key is available.
def get_source():
    if os.getenv("JSE_DATA_MODE", "csv").lower() == "stacks":
        from jse_platform.data_sources.stacks_source import StacksDataSource
        key = os.getenv("STACKS_API_KEY")
        if not key:
            raise RuntimeError("STACKS_API_KEY is required when JSE_DATA_MODE=stacks")
        return StacksDataSource(key)
    return CSVDataSource(str(SAMPLE_DATA))


def all_companies():
    return get_source().get_all()


class ChatRequest(BaseModel):
    message: str
    ticker: str | None = None


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": os.getenv("JSE_DATA_MODE", "csv"), "ai_background_research": True}


@app.get("/api/market")
def market():
    results = run_pipeline(get_source())
    return {"stocks": [result_to_dict(r) for r in results]}


@app.get("/api/stocks/{ticker}")
def stock(ticker: str):
    companies = all_companies()
    company = next((c for c in companies if c.ticker.upper() == ticker.upper()), None)
    if not company:
        raise HTTPException(404, "Stock not found")
    context = build_sector_context(companies).get(company.sector.value, {})
    result = score_company(company, context)
    insights, cycle = run_cycle_and_persist(company, result)
    return {"company": company_to_dict(company), "score": result_to_dict(result),
            "questions": [q.__dict__ for q in research_questions_for_company(company)],
            "insights": serialize_insights(insights),
            "research_cycle": cycle_to_dict(cycle)}


@app.get("/api/research")
def research():
    companies = all_companies()
    context = build_sector_context(companies)
    insights = []
    for company in companies:
        result = score_company(company, context.get(company.sector.value, {}))
        company_insights, _ = run_cycle_and_persist(company, result)
        insights.extend(company_insights)
    insights.sort(key=lambda x: x.created_at, reverse=True)
    return {"insights": serialize_insights(insights), "questions_per_company": len(research_questions_for_company(companies[0])) if companies else 0}


@app.get("/api/research/cycle/{ticker}")
def research_cycle_detail(ticker: str):
    """Full audit trail for one ticker's most recent research cycle: what
    changed, what questions that raised, the evidence gathered, and the
    self-challenge applied before the insight was recorded. Useful for
    checking the AI's reasoning rather than just its conclusion."""
    companies = all_companies()
    company = next((c for c in companies if c.ticker.upper() == ticker.upper()), None)
    if not company:
        raise HTTPException(404, "Stock not found")
    context = build_sector_context(companies).get(company.sector.value, {})
    result = score_company(company, context)
    _, cycle = run_cycle_and_persist(company, result)
    if cycle is None:
        return {"ticker": company.ticker, "changed": False,
                "message": "No material change since the last recorded assessment (or this is the first time this ticker has been scored)."}
    return {"ticker": company.ticker, "changed": True, **cycle_to_dict(cycle)}


def cycle_to_dict(cycle):
    if cycle is None:
        return None
    return {
        "changes": [{"field": c.field, "description": c.description, "old": c.old, "new": c.new} for c in cycle.changes],
        "questions": [q.__dict__ for q in cycle.questions],
        "evidence": cycle.evidence,
        "challenge": cycle.challenge,
        "insight": serialize_insights([cycle.insight])[0] if cycle.insight else None,
        "created_at": cycle.created_at,
    }


@app.post("/api/ai/chat")
def chat(body: ChatRequest):
    """Grounded MVP analyst. Later an LLM can be inserted after data retrieval."""
    companies = all_companies()
    target = None
    if body.ticker:
        target = next((c for c in companies if c.ticker.upper() == body.ticker.upper()), None)
    if target is None:
        text = body.message.lower()
        target = next((c for c in companies if c.ticker.lower() in text or c.name.lower() in text), None)
    if target is None:
        return {"answer": "I can analyze a JSE company. Tell me the ticker or company name, for example: 'Analyze GK'.", "grounded": True}
    result = score_company(target, build_sector_context(companies).get(target.sector.value, {}))
    s = result.sub_scores
    answer = (f"{target.name} ({target.ticker}) has a Business Score of {fmt(result.business_score)}, "
              f"an Investment Score of {fmt(result.investment_score)}, and is classified as '{result.horizon.value}'. "
              f"Valuation is {fmt(s.valuation)}, financial health is {fmt(s.financial_health)}, "
              f"growth is {fmt(s.growth)}, and momentum is {fmt(s.momentum)}. "
              f"Data confidence is {result.data_confidence:.0%}. "
              "These are analytical signals, not a guaranteed prediction or personal investment advice.")
    return {"answer": answer, "grounded": True, "ticker": target.ticker, "score": result_to_dict(result)}


def fmt(v):
    return "N/A" if v is None else f"{v:.0f}/100"


def result_to_dict(r):
    return {"ticker": r.ticker, "name": r.name, "business_score": r.business_score,
            "investment_score": r.investment_score, "horizon": r.horizon.value,
            "data_confidence": r.data_confidence,
            "sub_scores": {k: getattr(r.sub_scores, k) for k in ("business_quality", "financial_health", "growth", "valuation", "momentum")},
            "thesis_conditions": [{"description": c.description, "triggered": c.triggered, "detail": c.detail} for c in r.thesis_conditions],
            "notes": r.notes}


def company_to_dict(c):
    latest = c.latest()
    return {"ticker": c.ticker, "name": c.name, "sector": c.sector.value,
            "current_price": c.current_price,
            "latest_financials": latest.__dict__ if latest else None,
            "price_history": [{"date": p.trade_date.isoformat(), "close": p.close, "volume": p.volume} for p in c.price_history]}
