"""
The research cycle:

    data changes -> AI creates questions -> investigates -> challenges its
    own conclusion -> records the evidence -> creates an insight -> updates
    the stock's health/valuation assessment.

`research_agent.py`'s `generate_insights()` is a *static* read of a single
ScoreResult — useful the first time a company is ever scored, when there's
nothing yet to compare against. This module is what runs on every
subsequent pass: it diffs the new score against the last one on record
(`snapshot_store`), only engages when something material actually moved,
and produces an auditable trail of *why* — not just a re-stated label.

Each step is a separate, inspectable function on purpose: `detect_changes`
and `challenge` in particular are where you'd tune sensitivity or add new
counter-checks without touching how evidence is gathered or how the final
Insight gets built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import CompanyFinancials, ScoreResult
from .research_agent import QUESTION_TEMPLATES, Insight, ResearchQuestion, generate_insights
from .snapshot_store import ScoreSnapshot, load_snapshot, save_snapshot

# Sub-score point moves smaller than this are treated as noise, not signal —
# tune here rather than scattering magic numbers through the detection logic.
MATERIAL_SUB_SCORE_DELTA = 5.0
MATERIAL_COMPOSITE_DELTA = 5.0

# Maps a sub-score field name to the research-question category it belongs
# to (see QUESTION_TEMPLATES in research_agent.py).
SUB_SCORE_CATEGORY = {
    "business_quality": "business_quality",
    "financial_health": "financial_health",
    "growth": "growth",
    "valuation": "valuation",
    "momentum": "momentum",
}


@dataclass
class ChangeEvent:
    """One concrete thing that moved since the last recorded assessment."""

    field: str  # e.g. "valuation", or "thesis:ROE stays above 12.0%"
    description: str
    old: Optional[float] = None
    new: Optional[float] = None


@dataclass
class ResearchCycleRecord:
    """The full, auditable trail of one research cycle — everything that
    fed into the final Insight, kept around so a person can check the
    AI's reasoning rather than just trusting its summary."""

    ticker: str
    company_name: str
    changes: list[ChangeEvent]
    questions: list[ResearchQuestion]
    evidence: list[str]
    challenge: str
    insight: Optional[Insight]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Step 1: data changes
# ---------------------------------------------------------------------------

def detect_changes(previous: Optional[ScoreSnapshot], current: ScoreResult) -> list[ChangeEvent]:
    """What, concretely, moved since `previous` was recorded. Returns []
    when there's no prior snapshot (nothing to diff against yet) or when
    nothing moved by more than the materiality thresholds above."""
    if previous is None:
        return []

    changes: list[ChangeEvent] = []

    for attr, label in (("business_score", "Business Score"), ("investment_score", "Investment Score")):
        old_v = getattr(previous, attr)
        new_v = getattr(current, attr)
        if old_v is not None and new_v is not None and abs(new_v - old_v) >= MATERIAL_COMPOSITE_DELTA:
            direction = "rose" if new_v > old_v else "fell"
            changes.append(ChangeEvent(attr, f"{label} {direction} from {old_v:.0f} to {new_v:.0f}", old_v, new_v))

    current_sub = {k: getattr(current.sub_scores, k) for k in SUB_SCORE_CATEGORY}
    for key, old_v in previous.sub_scores.items():
        new_v = current_sub.get(key)
        if old_v is not None and new_v is not None and abs(new_v - old_v) >= MATERIAL_SUB_SCORE_DELTA:
            direction = "improved" if new_v > old_v else "weakened"
            label = key.replace("_", " ").title()
            changes.append(ChangeEvent(key, f"{label} {direction} from {old_v:.0f} to {new_v:.0f}", old_v, new_v))

    if previous.horizon != current.horizon.value:
        changes.append(ChangeEvent(
            "horizon", f"Horizon changed from '{previous.horizon}' to '{current.horizon.value}'"
        ))

    current_triggered = {c.description: c.triggered for c in current.thesis_conditions}
    for desc, was_triggered in previous.thesis_triggered.items():
        now_triggered = current_triggered.get(desc)
        if now_triggered is not None and now_triggered != was_triggered:
            verb = "triggered" if now_triggered else "cleared"
            changes.append(ChangeEvent(f"thesis:{desc}", f"Thesis condition {verb}: {desc}"))

    return changes


# ---------------------------------------------------------------------------
# Step 2: AI creates questions
# ---------------------------------------------------------------------------

def generate_questions(changes: list[ChangeEvent]) -> list[ResearchQuestion]:
    """Only the questions relevant to what actually moved — not a re-ask
    of all six templates on every pass."""
    categories: set[str] = set()
    for change in changes:
        if change.field.startswith("thesis:"):
            categories.add("thesis")
        else:
            categories.add(SUB_SCORE_CATEGORY.get(change.field, change.field))
    return [q for q in QUESTION_TEMPLATES if q.category in categories]


# ---------------------------------------------------------------------------
# Step 3: investigate
# ---------------------------------------------------------------------------

def investigate(company: CompanyFinancials, current: ScoreResult, changes: list[ChangeEvent]) -> list[str]:
    """The concrete numbers behind each change — what a person would need
    to check the reasoning themselves, not just the score labels."""
    evidence: list[str] = []
    s = current.sub_scores
    latest = company.latest()
    touched = {SUB_SCORE_CATEGORY.get(c.field, c.field) for c in changes if not c.field.startswith("thesis:")}

    if "valuation" in touched and latest and latest.eps and latest.eps > 0 and company.current_price:
        pe = company.current_price / latest.eps
        evidence.append(f"Current P/E proxy: {pe:.2f}x (Valuation score: {_fmt(s.valuation)})")

    if "financial_health" in touched and latest:
        if latest.total_debt is not None and latest.ebitda:
            evidence.append(f"Debt/EBITDA: {latest.total_debt / latest.ebitda:.2f}x")
        if latest.free_cash_flow is not None and latest.net_income:
            evidence.append(f"FCF/Net income: {latest.free_cash_flow / latest.net_income:.2f}x")

    if "growth" in touched and len(company.history) >= 2:
        first, last = company.history[0], company.history[-1]
        evidence.append(f"Revenue: {first.revenue:,.0f} ({first.fiscal_year}) -> {last.revenue:,.0f} ({last.fiscal_year})")
        evidence.append(f"EPS: {first.eps:.2f} ({first.fiscal_year}) -> {last.eps:.2f} ({last.fiscal_year})")

    if "momentum" in touched:
        evidence.append(f"Momentum score: {_fmt(s.momentum)}")

    for change in changes:
        if change.field.startswith("thesis:"):
            desc = change.field[len("thesis:"):]
            matching = next((c for c in current.thesis_conditions if c.description == desc), None)
            if matching:
                evidence.append(f"{matching.description}: {matching.detail}")

    return evidence


# ---------------------------------------------------------------------------
# Step 4: challenge its own conclusion
# ---------------------------------------------------------------------------

def challenge(company: CompanyFinancials, current: ScoreResult, changes: list[ChangeEvent]) -> tuple[str, float]:
    """
    Before treating a detected change as a real signal, check the most
    common ways it could be misleading. Returns (challenge_note,
    confidence_multiplier) — counter-evidence discounts the eventual
    insight's confidence rather than silently vanishing.
    """
    notes: list[str] = []
    multiplier = 1.0

    if current.data_confidence < 0.6:
        notes.append(
            f"Data confidence is only {current.data_confidence:.0%} — this read could shift as more history arrives."
        )
        multiplier *= 0.8

    valuation_changed = any(c.field == "valuation" for c in changes)
    quality_changed = any(c.field == "business_quality" for c in changes)
    if valuation_changed and not quality_changed:
        notes.append(
            "Valuation moved without a matching move in Business Quality — more likely a price move than a change in the underlying business."
        )
        multiplier *= 0.9

    momentum_driven = any(c.field == "momentum" for c in changes)
    fundamentals_driven = any(c.field in ("business_quality", "financial_health", "growth") for c in changes)
    if momentum_driven and not fundamentals_driven:
        notes.append(
            "The move is momentum-led with no accompanying fundamentals change — more likely to reverse than a fundamentals-driven move."
        )
        multiplier *= 0.85

    latest = company.latest()
    if latest and latest.total_equity:
        roe = latest.net_income / latest.total_equity * 100
        for change in changes:
            if change.field.startswith("thesis:") and "ROE" in change.field and abs(roe - 12.0) <= 1.5:
                notes.append(
                    f"ROE ({roe:.1f}%) is close to the 12% threshold — this condition could flip again next period."
                )
                multiplier *= 0.9
                break

    if not notes:
        notes.append("No material counter-evidence found against this change; treating it as a genuine signal.")

    return " ".join(notes), round(max(0.3, min(1.0, multiplier)), 2)


def _fmt(v: Optional[float]) -> str:
    return "N/A" if v is None else f"{v:.0f}/100"


def _severity_for_changes(changes: list[ChangeEvent]) -> str:
    if any(c.field.startswith("thesis:") and "triggered" in c.description for c in changes):
        return "negative"
    if any(c.field == "financial_health" and c.new is not None and c.old is not None and c.new < c.old for c in changes):
        return "negative"
    if any(c.new is not None and c.old is not None and c.new > c.old for c in changes):
        return "positive"
    return "warning"


# ---------------------------------------------------------------------------
# Orchestration: steps 1-6, then the caller persists step 7 (the updated
# assessment) via snapshot_store.save_snapshot
# ---------------------------------------------------------------------------

def run_research_cycle(
    company: CompanyFinancials,
    previous: Optional[ScoreSnapshot],
    current: ScoreResult,
) -> Optional[ResearchCycleRecord]:
    """
    Runs the full loop and returns a ResearchCycleRecord, or None when
    nothing material changed since `previous` (including when `previous`
    is None — the first time a company is scored, use
    research_agent.generate_insights() for a baseline read instead).

    Callers are responsible for the final step — call
    snapshot_store.save_snapshot(current) afterwards either way, so the
    *next* cycle has this pass to compare against.
    """
    changes = detect_changes(previous, current)
    if not changes:
        return None

    questions = generate_questions(changes)
    evidence = investigate(company, current, changes)
    challenge_note, confidence_multiplier = challenge(company, current, changes)
    recorded_evidence = evidence + [f"Self-check: {challenge_note}"]

    single = len(changes) == 1
    title = "Assessment updated: " + changes[0].description if single else f"Assessment updated: {len(changes)} material changes"
    category = "thesis" if any(c.field.startswith("thesis:") for c in changes) else "reassessment"

    insight = Insight(
        ticker=company.ticker,
        company_name=company.name,
        category=category,
        title=title,
        summary="; ".join(c.description for c in changes),
        severity=_severity_for_changes(changes),
        confidence=round(current.data_confidence * confidence_multiplier, 2),
        evidence=recorded_evidence,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    return ResearchCycleRecord(
        ticker=company.ticker,
        company_name=company.name,
        changes=changes,
        questions=questions,
        evidence=evidence,
        challenge=challenge_note,
        insight=insight,
    )


def run_cycle_and_persist(
    company: CompanyFinancials,
    current: ScoreResult,
) -> tuple[list[Insight], Optional[ResearchCycleRecord]]:
    """
    The convenience entry point for callers (the web API, a scheduled
    job) that just want "give me whatever's new for this company, and
    make sure it's recorded as the current assessment":

      - No prior snapshot -> this is the first time we've scored this
        company; return a baseline read (research_agent.generate_insights)
        since there's nothing yet to diff against.
      - Prior snapshot + material change -> run the full cycle and return
        its insight.
      - Prior snapshot + no material change -> nothing new to report.

    `current` is saved as the new snapshot in every case, so the next
    call has this pass to compare against — this is the "updates the
    stock's health/valuation assessment" step.
    """
    previous = load_snapshot(company.ticker)
    cycle = run_research_cycle(company, previous, current)
    if previous is None:
        insights = generate_insights(company, current)
    elif cycle is not None:
        insights = [cycle.insight]
    else:
        insights = []
    save_snapshot(current)
    return insights, cycle
