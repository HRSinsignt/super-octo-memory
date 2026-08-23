# JSE Investment Horizon Platform — Scoring Engine

A backend scoring pipeline that answers two separate questions for every
JSE-listed company, instead of collapsing everything into one BUY/SELL call:

1. **Business Score** — is this a fundamentally strong business worth owning for years?
2. **Investment Score** — is the current price attractive enough to buy now?

Those two scores map to a **Horizon** label (Long-Term Compounder, Long-Term
Hold, Long-Term Watch, Short-Term Opportunity, Speculative/Avoid), and every
rated company gets a **thesis monitor** — a set of concrete, falsifiable
conditions ("ROE stays above 12%", "no 2 consecutive quarters of EPS
decline") that flag when the original rating's reasoning has broken down.

## Status

This is the **scoring engine and pipeline**, fully working end-to-end against
CSV data, with zero third-party dependencies in the core (`pytest` only for
tests). It is **not** a live product yet — it has no live JSE data feed,
no database, no web API, and no frontend. Those are the next build phases;
see "What's not built yet" below.

## Architecture

```
jse_platform/
  models.py          Data shapes: AnnualFinancials, CompanyFinancials,
                      ScoreResult, Horizon, ThesisCondition
  config.py           Every weight and threshold, in one place, validated
                      at import time (weight groups must sum to 1.0)
  statistics_utils.py CAGR, percentile ranking, coefficient of variation,
                      linear scaling — the shared math primitives
  scoring.py           The five sub-scores (Business Quality, Financial
                      Health, Growth, Valuation, Momentum) and the two
                      composites (Business Score, Investment Score)
  horizon.py           Maps composites -> Horizon label
  thesis.py            "What would change my mind" condition checks
  pipeline.py          Orchestrates: DataSource -> scores -> horizon ->
                      thesis -> ScoreResult. This is the main entry point.
  reporting.py         ScoreResult -> JSON / human-readable text
  cli.py               Command-line interface
  research_agent.py    Static, single-snapshot insight generation (the
                      baseline read used the first time a company is scored)
  research_cycle.py    The change-driven research loop: detect what moved
                      since the last snapshot, question it, investigate,
                      challenge the conclusion, record evidence, emit an
                      insight, persist the new assessment (see "The
                      research cycle" below)
  snapshot_store.py    Persists each ticker's last-known assessment (JSON
                      file) so research_cycle.py has something to diff
                      against on the next pass
  data_sources/
    base.py             Abstract DataSource interface — implement this
                        for any new data provider
    csv_source.py        Works today, no API keys needed (see sample_data/)
    live_api_source.py   SKELETON ONLY — see "Connecting live data" below
tests/
  test_scoring.py       20 unit tests covering statistics, scoring,
                        horizon classification, and thesis checks
sample_data/             Synthetic example data (3 companies, 5yrs financials,
                        400 days of prices) so you can run the pipeline today
```

**Why it's built this way:** every stage only depends on the plain
dataclasses in `models.py`, and the pipeline only depends on the abstract
`DataSource` interface — not any particular provider. That means swapping
from CSV files to a live JSE feed later is a matter of writing one new
adapter file; nothing in `scoring.py`, `horizon.py`, `thesis.py`, or
`pipeline.py` needs to change.

Scoring functions return `None` (never a fabricated `0`) when they lack the
data to compute a meaningful number. `None` propagates through the weighted
averages by re-normalizing over only the available components — so a
company with 2 years of history gets a **lower-confidence** score, not a
wrong one. `data_confidence` on every `ScoreResult` surfaces this so a UI
can visibly flag low-confidence ratings rather than presenting them with
false authority.

## Quickstart

```bash
cd jse_platform
pip install -e ".[dev]"      # or: pip install pytest
python -m jse_platform.cli --data-dir sample_data --format text
python -m jse_platform.cli --data-dir sample_data --format json --out results.json
python -m jse_platform.cli --data-dir sample_data --ticker NCBFG

# Run tests
pytest tests/ -v
```

## Using your own data (CSV path — works today)

Populate three CSVs in a directory (see `sample_data/` for exact examples):

- **`financials.csv`** — one row per (ticker, fiscal_year): revenue, net
  income, EPS, debt, EBITDA, interest expense, cash flow, equity, assets,
  current assets/liabilities, dividend per share, shares outstanding.
  Provide at least 2 years; 5+ years needed for full-confidence scores.
- **`prices.csv`** — one row per (ticker, date, close, volume), used for
  momentum and current price.
- **`index_prices.csv`** — one row per (date, close) for the JSE index,
  used to compute *relative* (not absolute) momentum.

Then run the CLI or call `run_pipeline(CSVDataSource("path/to/dir"))` directly.

## Connecting live data

`jse_platform/data_sources/live_api_source.py` is a **template, not a working
integration** — its methods raise `NotImplementedError` on purpose. From the
research earlier in this project, here are the realistic options, roughly in
order of effort to integrate:

1. **stacksja.com ("Stacks")** — free, appears to expose prices and financial
   statements for 130+ JSE-listed names with an API. Best starting point for
   an MVP. Confirm current terms of use and exact endpoint/field names
   directly against their docs before building against it — the specifics
   weren't verified in depth here.
2. **JSE's own Market Data Feed (MDF)** — official, JSON-based, but likely
   needs a commercial data agreement. Contact JSE directly for developer
   access and pricing.
3. **ICE Consolidated Feed** — institutional-grade, real-time + up to 15yrs
   historical tick data. Paid, almost certainly overkill for an MVP; worth
   knowing about for a scaled production system later.
4. **Community-maintained scrapers/APIs (e.g. JamStockEx GitHub projects)** —
   fine for prototyping, but unofficial: no SLA, could break silently, and
   licensing/terms of use for scraping JSE's site should be checked before
   relying on this in production.

**To connect any of these:** fill in `_fetch_json` in `live_api_source.py`
with real HTTP calls (the `requests` library, commented in
`requirements.txt`), and adjust the field-mapping in `_map_financials` and
`get_company_financials` to match that provider's actual JSON shape. Nothing
else in the codebase needs to change — `pipeline.py` only calls the
`DataSource` interface methods.

## Web + AI MVP (added in this build)

A FastAPI web layer and responsive dashboard now sit on top of the scoring engine.
The MVP includes: market cards, company scoring, an AI research feed, generated
research questions, thesis-monitoring insights, and a grounded analyst chat endpoint.
The default mode uses `sample_data/` so the site runs without an API key.

Run it with:

```bash
pip install -r requirements.txt
uvicorn web.app:app --reload
```

Then open `http://127.0.0.1:8000`.

For live Stacks data, set `JSE_DATA_MODE=stacks` and `STACKS_API_KEY` in the server environment.
Keep the key on the server; never expose it in browser JavaScript. Stacks documents the public
API at `https://stacksja.com/api/v1/public`, including market data, financials, history,
dividends, order books, trades and an MCP endpoint.

### Background AI research

`research_agent.py` deliberately separates **facts/calculations** from language generation.
It creates repeatable questions about valuation, financial health, growth, dividends, momentum and
what could invalidate a thesis; then it produces auditable insights from the scoring engine. An LLM
can be connected later to turn those verified findings into richer natural-language answers without
letting the model invent market facts.

### The research cycle

`research_cycle.py` is the loop that turns a **change** in a company's scores into a recorded,
self-checked insight, rather than re-stating the same static read every time a page loads:

```
data changes -> AI creates questions -> investigates -> challenges its own
conclusion -> records the evidence -> creates an insight -> updates the
stock's health/valuation assessment
```

Concretely, each step is a separate function so you can tune or extend any one of them
independently:

1. **Data changes** — `snapshot_store.py` persists the last scored assessment per ticker
   (`data/snapshots.json` — a single JSON file, not a database yet; see "What's not built yet").
   `detect_changes()` diffs the new `ScoreResult` against that snapshot and returns only the
   moves past a materiality threshold (5+ points on a sub-score or composite, a horizon change,
   or a thesis condition flipping) — small noise doesn't trigger anything.
2. **Creates questions** — `generate_questions()` picks the subset of `QUESTION_TEMPLATES`
   that's actually relevant to what moved (a valuation change asks the valuation question, not
   all six).
3. **Investigates** — `investigate()` pulls the concrete numbers behind each change (P/E,
   Debt/EBITDA, FCF conversion, revenue/EPS trend, the exact thesis-condition detail) so there's
   something to check the reasoning against, not just a re-stated score.
4. **Challenges its own conclusion** — `challenge()` runs a short list of counter-checks before
   trusting the change as a real signal: is data confidence too low to be sure, did valuation move
   without a matching move in the underlying business (price move vs. business change), is the
   move momentum-led with no fundamentals behind it, is a thesis trigger sitting right on its
   threshold and likely to flip back. Each flag discounts the eventual insight's confidence rather
   than silently disappearing.
5. **Records the evidence** — the investigation output plus the challenge note are stored
   together on the returned `ResearchCycleRecord` (and in each `Insight.evidence` list), so the
   full trail is inspectable, not just the final label.
6. **Creates an insight** — a `research_agent.Insight` (same shape the frontend already renders),
   with severity and a confidence score that reflects the challenge step.
7. **Updates the assessment** — `run_cycle_and_persist()` (used by `web/app.py`) always calls
   `snapshot_store.save_snapshot()` on the new `ScoreResult`, whether or not anything changed, so
   the *next* pass has this one to diff against.

The first time a company is ever scored there's no prior snapshot to diff against, so
`run_cycle_and_persist()` falls back to `research_agent.generate_insights()` for a baseline read;
every subsequent scoring pass goes through the full change-driven cycle above. `GET
/api/research/cycle/{ticker}` exposes the full audit trail (changes, questions, evidence,
challenge note) for one ticker's most recent cycle.

This currently runs **on request** (whenever `/api/research` or `/api/stocks/{ticker}` is hit),
not on a schedule — see "Scheduled thesis re-evaluation" below for turning this into a cron/worker
that runs it automatically whenever new data lands.

## Deployment

The repository now includes `render.yaml`, `Dockerfile`, and `start_web.bat`. A platform such as Render can run the FastAPI backend; GitHub Pages alone cannot run this Python backend. Keep `STACKS_API_KEY` as a server-side secret.

## What's not built yet (next phases)

- **Web API** — wrap `pipeline.py` in a FastAPI/Flask app so a frontend can
  request scores over HTTP instead of via CLI.
- **Persistence** — `snapshot_store.py` now persists the last assessment per ticker so the
  research cycle can detect changes, but it's a single JSON file, not a database — no history of
  every past score (just the most recent one), no multi-process safety. A real table (ticker,
  recorded_at, scores JSON) would let you keep the full history and power "82 → 68 over the last
  quarter" style charts, not just the latest delta.
- **Scheduled thesis re-evaluation** — the research cycle (see above) currently runs when
  `/api/research` or `/api/stocks/{ticker}` is requested, not automatically. A cron/worker that
  re-runs it whenever new financials or prices land — and pushes newly-triggered changes as
  alerts, rather than waiting for someone to load the page — is the natural next step.
- **Sector peer data** — `build_sector_context()` currently computes peer
  medians from whatever's in your own dataset; with only 3 sample companies
  this isn't meaningful yet. Real peer-relative valuation and growth
  percentiles need your full JSE universe loaded. (Valuation no longer
  depends solely on this: `pipeline._own_history_ranges()` now derives a
  P/E and dividend-yield range from each company's own price history, so a
  single-company "sector" doesn't collapse Valuation to a flat, uninformative
  50. It's still worth loading the full universe once you have it — sector
  percentile ranking is more meaningful than the own-history approximation.)
- **Frontend** — none exists yet; happy to build a demo UI against this
  engine's JSON output whenever useful.
- **Threshold calibration** — the numbers in `config.py` (e.g. what counts
  as a "compounder," debt/EBITDA ceilings) are reasonable starting points,
  not backtested against actual JSE outcomes. Expect to tune these once you
  have real coverage running.

## Legal/disclaimer note

This tool produces an *analysis*, not investment advice. Before shipping
this to end users, get clarity on how Jamaica's Financial Services
Commission treats tools that output ratings like this, and make sure the UI
carries appropriate disclaimers. This wasn't researched in depth as part of
this build — flagging it so it doesn't get missed.
