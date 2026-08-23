"""
Persistence for "what did we conclude about this company last time" —
the thing the research cycle diffs the *current* score against to find
out what actually changed.

This is intentionally a single JSON file, not a database — it matches
the MVP's zero-infrastructure story (see README's "What's not built
yet": persistence is still on the roadmap generally). Swap this module
for a real table (ticker, recorded_at, scores JSON) whenever that lands;
`research_cycle.py` only calls `load_snapshot` / `save_snapshot`, so
nothing else needs to change.

Not safe for multiple server processes writing concurrently (e.g. more
than one uvicorn worker) — the file write is atomic per-process but
there's no cross-process locking. Fine for a single-worker MVP deploy;
revisit alongside the database work.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import ScoreResult

_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "snapshots.json"
_lock = threading.Lock()


@dataclass
class ScoreSnapshot:
    """The minimal, comparable slice of a previous ScoreResult — enough
    to detect what moved without needing to reconstruct a full ScoreResult."""

    ticker: str
    business_score: Optional[float]
    investment_score: Optional[float]
    horizon: str
    data_confidence: float
    sub_scores: dict = field(default_factory=dict)
    thesis_triggered: dict = field(default_factory=dict)
    recorded_at: str = ""


def _read_all() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_all(data: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_STORE_PATH)


def load_snapshot(ticker: str) -> Optional[ScoreSnapshot]:
    """The assessment recorded the previous time this ticker was scored,
    or None if this is the first time we've ever seen it."""
    with _lock:
        raw = _read_all().get(ticker.upper())
    if not raw:
        return None
    return ScoreSnapshot(**raw)


def save_snapshot(result: ScoreResult) -> ScoreSnapshot:
    """Record `result` as the new 'current assessment' for its ticker —
    the last step of the research cycle, so the *next* run has something
    to diff against."""
    snapshot = ScoreSnapshot(
        ticker=result.ticker.upper(),
        business_score=result.business_score,
        investment_score=result.investment_score,
        horizon=result.horizon.value,
        data_confidence=result.data_confidence,
        sub_scores={
            k: getattr(result.sub_scores, k)
            for k in ("business_quality", "financial_health", "growth", "valuation", "momentum")
        },
        thesis_triggered={c.description: c.triggered for c in result.thesis_conditions},
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    with _lock:
        data = _read_all()
        data[snapshot.ticker] = asdict(snapshot)
        _write_all(data)
    return snapshot
