import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jse_platform.models import (
    AnnualFinancials,
    CompanyFinancials,
    Horizon,
    PricePoint,
    Sector,
    ScoreResult,
    SubScores,
)
from jse_platform import snapshot_store
from jse_platform.research_cycle import (
    challenge,
    detect_changes,
    generate_questions,
    investigate,
    run_cycle_and_persist,
    run_research_cycle,
)
from jse_platform.snapshot_store import ScoreSnapshot


def make_company() -> CompanyFinancials:
    history = [
        AnnualFinancials(
            fiscal_year=2022 + i, revenue=10000 * (1.1 ** i), net_income=1500 * (1.12 ** i),
            eps=1.5 * (1.12 ** i), total_equity=12000, total_assets=30000,
            total_debt=3000, ebitda=2500, interest_expense=200,
            free_cash_flow=1400, dividend_per_share=0.4, shares_outstanding=1000,
        )
        for i in range(3)
    ]
    base = date(2024, 1, 1)
    prices = [PricePoint(base + timedelta(days=i), close=50 + i * 0.05, volume=10000) for i in range(1, 100)]
    return CompanyFinancials(
        ticker="TEST", name="Test Co", sector=Sector.MANUFACTURING,
        history=history, current_price=20.0, price_history=prices,
    )


def make_result(business=70.0, investment=60.0, valuation=55.0, momentum=50.0) -> ScoreResult:
    sub = SubScores(business_quality=75.0, financial_health=65.0, growth=60.0, valuation=valuation, momentum=momentum)
    return ScoreResult(
        ticker="TEST", name="Test Co", sub_scores=sub, business_score=business, investment_score=investment,
        horizon=Horizon.LONG_TERM_HOLD, data_confidence=1.0, thesis_conditions=[], notes=[],
    )


def make_snapshot(**overrides) -> ScoreSnapshot:
    defaults = dict(
        ticker="TEST", business_score=70.0, investment_score=60.0, horizon="Long-Term Hold",
        data_confidence=1.0,
        sub_scores={"business_quality": 75.0, "financial_health": 65.0, "growth": 60.0, "valuation": 55.0, "momentum": 50.0},
        thesis_triggered={}, recorded_at="2024-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ScoreSnapshot(**defaults)


# --- detect_changes ---------------------------------------------------------

def test_no_previous_snapshot_means_no_changes():
    assert detect_changes(None, make_result()) == []


def test_small_moves_are_not_material():
    previous = make_snapshot()
    current = make_result(investment=62.0, valuation=57.0)  # +2 each, below threshold
    assert detect_changes(previous, current) == []


def test_material_valuation_move_is_detected():
    previous = make_snapshot()
    current = make_result(investment=45.0, valuation=20.0)
    changes = detect_changes(previous, current)
    fields = [c.field for c in changes]
    assert "valuation" in fields
    assert "investment_score" in fields


def test_thesis_flip_is_detected():
    previous = make_snapshot(thesis_triggered={"ROE stays above 12.0%": False})
    current = make_result()
    current.thesis_conditions = [__import__("jse_platform.models", fromlist=["ThesisCondition"]).ThesisCondition(
        "ROE stays above 12.0%", triggered=True, detail="Current ROE: 8.0%"
    )]
    changes = detect_changes(previous, current)
    assert any(c.field.startswith("thesis:") for c in changes)


# --- generate_questions ------------------------------------------------------

def test_questions_match_changed_categories_only():
    previous = make_snapshot()
    current = make_result(investment=45.0, valuation=20.0)
    changes = detect_changes(previous, current)
    questions = generate_questions(changes)
    categories = {q.category for q in questions}
    assert "valuation" in categories
    assert "momentum" not in categories  # momentum didn't move


# --- investigate --------------------------------------------------------------

def test_investigate_returns_evidence_for_touched_categories():
    company = make_company()
    current = make_result(investment=45.0, valuation=20.0)
    previous = make_snapshot()
    changes = detect_changes(previous, current)
    evidence = investigate(company, current, changes)
    assert any("P/E" in e for e in evidence)


# --- challenge -----------------------------------------------------------------

def test_challenge_flags_valuation_move_without_quality_move():
    company = make_company()
    current = make_result(investment=45.0, valuation=20.0)
    previous = make_snapshot()
    changes = detect_changes(previous, current)
    note, multiplier = challenge(company, current, changes)
    assert "price move" in note or "Business Quality" in note
    assert multiplier < 1.0


def test_challenge_no_flags_when_business_quality_moves_together():
    company = make_company()
    previous = make_snapshot()
    current = make_result(business=90.0, investment=80.0, valuation=85.0)
    current.sub_scores.business_quality = 92.0  # moves with valuation
    changes = detect_changes(previous, current)
    note, multiplier = challenge(company, current, changes)
    assert multiplier == 1.0
    assert "No material counter-evidence" in note


# --- full cycle ------------------------------------------------------------------

def test_run_research_cycle_returns_none_without_material_change():
    company = make_company()
    previous = make_snapshot()
    current = make_result(investment=61.0)  # +1, immaterial
    assert run_research_cycle(company, previous, current) is None


def test_run_research_cycle_produces_insight_on_material_change():
    company = make_company()
    previous = make_snapshot()
    current = make_result(investment=45.0, valuation=20.0)
    record = run_research_cycle(company, previous, current)
    assert record is not None
    assert record.insight is not None
    assert record.changes
    assert record.evidence
    assert record.challenge


def test_run_cycle_and_persist_baseline_then_change_then_quiet():
    snapshot_store._STORE_PATH = Path(tempfile.mkdtemp()) / "snapshots.json"
    company = make_company()

    # First pass: no prior snapshot -> baseline insights, no cycle record.
    result1 = make_result()
    insights1, cycle1 = run_cycle_and_persist(company, result1)
    assert cycle1 is None

    # Second pass, identical score -> nothing new to report.
    result2 = make_result()
    insights2, cycle2 = run_cycle_and_persist(company, result2)
    assert insights2 == []
    assert cycle2 is None

    # Third pass, material change -> a cycle record and one insight.
    result3 = make_result(investment=40.0, valuation=15.0)
    insights3, cycle3 = run_cycle_and_persist(company, result3)
    assert len(insights3) == 1
    assert cycle3 is not None
    assert cycle3.changes
