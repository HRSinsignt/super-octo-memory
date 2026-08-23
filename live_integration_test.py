"""
LIVE integration test for StacksDataSource — makes real HTTP calls.

This is deliberately NOT part of the pytest unit test suite (tests/test_scoring.py),
which must stay network-free and fast. Run this manually, on-demand, whenever
you want to verify the adapter still matches Stacks' actual API behavior:

    export STACKS_API_KEY="pk_your_real_key_here"
    python live_integration_test.py

Requires: `pip install requests` and a real Stacks API key with network access
(neither of which is available in the Claude sandbox this was written in —
this script has NOT been executed against the live API. Treat StacksDataSource
as unverified until this script has been run successfully at least once).

What this checks:
  1. Fetches several real tickers and prints the raw API response alongside
     what the adapter mapped it to, so you can visually verify correctness
     (not just "did it not crash").
  2. Runs full scoring (sub-scores, Business/Investment Score, Horizon,
     Thesis Monitor) on each fetched company.
  3. Exercises failure paths on purpose: bad API key (401/403), a ticker
     that doesn't exist (404), and — if you set STRESS_RATE_LIMIT=1 —
     hammers one endpoint fast enough to trigger a real 429, to confirm
     the adapter's backoff/retry actually fires instead of just crashing.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

try:
    import requests
except ImportError:
    print("ERROR: `requests` is not installed. Run: pip install requests")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jse_platform.data_sources.stacks_source import STACKS_BASE_URL, StacksDataSource
from jse_platform.pipeline import score_company
from jse_platform.reporting import to_text_summary

# Pick a handful of real, currently-listed JSE tickers to test against.
# Adjust this list if any of these have been delisted/renamed by the time
# you run this — check https://stacksja.com or the JSE website for current
# active tickers first.
TEST_TICKERS = ["NCBFG", "GK", "JMMBGL"]


def get_api_key() -> str:
    key = os.environ.get("STACKS_API_KEY")
    if not key:
        print("ERROR: Set STACKS_API_KEY environment variable before running this script.")
        sys.exit(1)
    return key


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def test_happy_path(source: StacksDataSource) -> list:
    section("1. FETCH + SCORE REAL TICKERS")
    results = []
    for ticker in TEST_TICKERS:
        print(f"\n--- {ticker} ---")
        try:
            company = source.get_company_financials(ticker)
        except Exception as e:
            print(f"  FAILED to fetch {ticker}: {e}")
            traceback.print_exc()
            continue

        # Sanity-check what actually came back before trusting it.
        print(f"  Name: {company.name}")
        print(f"  Sector: {company.sector.value}  (Stacks doesn't expose sector — "
              f"confirm this is OTHER unless you've populated SECTOR_OVERRIDES)")
        print(f"  Fiscal years fetched: {[h.fiscal_year for h in company.history]}")
        print(f"  Current price: {company.current_price}")
        print(f"  Price history points: {len(company.price_history)}")
        if company.history:
            latest = company.history[-1]
            print(f"  Latest revenue: {latest.revenue}, net_income: {latest.net_income}, eps: {latest.eps}")
            print(f"  total_debt: {latest.total_debt} (expected None — not in Stacks financials)")
            print(f"  ebitda: {latest.ebitda} (expected None — not in Stacks financials)")
            print(f"  dividend_per_share: {latest.dividend_per_share}")
            print(f"  shares_outstanding: {latest.shares_outstanding}")

        if not company.history:
            print(f"  WARNING: no financial history returned for {ticker} — "
                  f"check whether Stacks actually has filings for this ticker.")
            continue

        result = score_company(company)
        results.append(result)
        print()
        print(to_text_summary(result))

        # This is the assertion that matters most: confirm Financial Health
        # is genuinely lower-confidence given the known data gaps, not
        # silently wrong-looking-right.
        if result.sub_scores.financial_health is not None:
            print(f"\n  [CHECK] Financial Health = {result.sub_scores.financial_health:.1f} "
                  f"— since debt/EBITDA/FCF/liquidity are all unavailable from Stacks, "
                  f"this should be driven almost entirely by earnings consistency. "
                  f"Verify that's plausible given the EPS series above.")

    return results


def test_invalid_ticker(source: StacksDataSource) -> None:
    section("2. INVALID TICKER (expect a clean 404, not a crash)")
    fake_ticker = "ZZZFAKE99"
    try:
        source.get_company_financials(fake_ticker)
        print(f"  UNEXPECTED: no error raised for nonexistent ticker '{fake_ticker}'")
    except requests.exceptions.HTTPError as e:
        print(f"  OK — got expected HTTP error: {e}")
    except Exception as e:
        print(f"  Got an error, but not the expected HTTPError type: {type(e).__name__}: {e}")


def test_bad_api_key() -> None:
    section("3. BAD API KEY (expect a clean 401/403, not a crash)")
    bad_source = StacksDataSource(api_key="pk_definitely_invalid_key_00000")
    try:
        bad_source.list_tickers()
        print("  UNEXPECTED: no error raised with an invalid API key")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        print(f"  OK — got expected HTTP error (status {status}): {e}")
    except Exception as e:
        print(f"  Got an error, but not the expected HTTPError type: {type(e).__name__}: {e}")


def test_rate_limit_backoff(source: StacksDataSource) -> None:
    section("4. RATE LIMIT BACKOFF (opt-in — set STRESS_RATE_LIMIT=1)")
    if os.environ.get("STRESS_RATE_LIMIT") != "1":
        print("  Skipped. Set STRESS_RATE_LIMIT=1 to actually trigger a 429 "
              "against the live API (this will burn real request quota and "
              "take over a minute due to the adapter's 60s backoff).")
        return

    # Bypass the adapter's built-in throttle to force a 429 on purpose.
    source._min_interval = 0.0
    start = time.time()
    errored = False
    try:
        for i in range(80):  # comfortably over the documented 60/min limit
            source._get(f"{STACKS_BASE_URL}/stocks")
    except requests.exceptions.HTTPError as e:
        errored = True
        print(f"  Got HTTPError after {i+1} rapid requests: {e}")
    if not errored:
        elapsed = time.time() - start
        print(f"  Made 80 rapid requests in {elapsed:.1f}s without a 429 — "
              f"either the adapter's backoff absorbed it silently (check logs) "
              f"or the account's limit is higher than documented. Either way, "
              f"re-run with logging to confirm the retry path actually executed.")


def main() -> None:
    api_key = get_api_key()
    source = StacksDataSource(api_key=api_key)

    results = test_happy_path(source)
    test_invalid_ticker(source)
    test_bad_api_key()
    test_rate_limit_backoff(source)

    section("SUMMARY")
    print(f"Successfully scored {len(results)} / {len(TEST_TICKERS)} test tickers.")
    if len(results) < len(TEST_TICKERS):
        print("Some tickers failed — investigate above before trusting this adapter.")
    print("\nManual review checklist before calling this production-ready:")
    print("  [ ] Fundamentals above (revenue/net income/EPS) match what you see")
    print("      on stacksja.com or the company's actual financial statements")
    print("  [ ] Fiscal years returned are genuinely annual, not accidentally")
    print("      mixing quarters (check _select_annual_records heuristic)")
    print("  [ ] Horizon labels look directionally sane for companies you know")
    print("  [ ] Bad-key and invalid-ticker tests above both failed cleanly")
    print("      (raised HTTPError) rather than crashing or returning junk data")
    print("  [ ] Dividend-per-share figures match known dividend history")
    print("      for at least one dividend-paying ticker you can verify by hand")


if __name__ == "__main__":
    main()
