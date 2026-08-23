"""
Formats ScoreResult objects for output: JSON (for an API/frontend to
consume) and a human-readable text summary (for CLI use / quick review).
"""

from __future__ import annotations

import json
from dataclasses import asdict

from .models import ScoreResult


def to_dict(result: ScoreResult) -> dict:
    d = asdict(result)
    d["horizon"] = result.horizon.value
    return d


def to_json(results: list[ScoreResult], indent: int = 2) -> str:
    return json.dumps([to_dict(r) for r in results], indent=indent, default=str)


def _fmt(value) -> str:
    return f"{value:.1f}" if isinstance(value, (int, float)) else "N/A"


def to_text_summary(result: ScoreResult) -> str:
    lines = [
        f"{result.name} ({result.ticker})",
        "=" * (len(result.name) + len(result.ticker) + 3),
        f"Horizon: {result.horizon.value}",
        f"Business Score: {_fmt(result.business_score)}   Investment Score: {_fmt(result.investment_score)}",
        f"Data confidence: {result.data_confidence * 100:.0f}%",
        "",
        "Sub-scores:",
        f"  Business Quality : {_fmt(result.sub_scores.business_quality)}",
        f"  Financial Health : {_fmt(result.sub_scores.financial_health)}",
        f"  Growth           : {_fmt(result.sub_scores.growth)}",
        f"  Valuation        : {_fmt(result.sub_scores.valuation)}",
        f"  Momentum         : {_fmt(result.sub_scores.momentum)}",
    ]
    if result.thesis_conditions:
        lines.append("")
        lines.append("Thesis conditions:")
        for c in result.thesis_conditions:
            flag = "🚨 TRIGGERED" if c.triggered else "✓ OK"
            lines.append(f"  [{flag}] {c.description} — {c.detail}")
    if result.notes:
        lines.append("")
        lines.append("Notes:")
        for n in result.notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)
