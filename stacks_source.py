"""
Live data source backed by the Stacks JSE API (stacksja.com).

Endpoint contracts below are taken from the published docs at
https://stacksja.com/developers.html as of this build. Re-check that
page if anything here starts returning unexpected shapes — Stacks is
an active, evolving product and field names/behavior can change.

IMPORTANT — known data gaps (read before trusting Financial Health scores):
Stacks' /financials endpoint gives revenue, net income, EPS, total
assets/equity/liabilities, and cash — but NOT total debt, EBITDA,
current assets/liabilities, or operating/free cash flow as clean fields.
This adapter leaves those as None rather than approximating them from
total_liabilities (which would silently conflate operating liabilities
with interest-bearing debt and produce a misleadingly precise-looking
but wrong number). Practical effect: Financial Health scores computed
from this source will lean heavily on earnings consistency alone and
should be treated as lower-confidence — see ScoreResult.data_confidence.

If you need real debt/EBITDA/cash-flow figures, the two options are:
  1. Pull the filed PDFs via /stock/{symbol}/documents and extract the
     balance sheet / cash flow statement yourself (more accurate, more work).
  2. Find a second data source that fills these specific gaps and merge
     it with this one (see pipeline.py — nothing stops you combining
     two DataSource outputs for the same ticker before scoring).

Sector is also not exposed by this API. `SECTOR_OVERRIDES` below is a
placeholder for a manually-maintained ticker->sector map; without it,
every company defaults to Sector.OTHER, which weakens sector-relative
percentile ranking in build_sector_context(). Populate it as you confirm
sectors (e.g. from JSE's own listed-companies page) for tickers you cover.

Rate limits (per Stacks docs, free plan): 60 requests/minute per key,
plus a separate daily quota. Financials/directors/filings/PE endpoints
are "Metered" tier; price history is "Moat" tier with a smaller daily
budget. This adapter does simple per-request throttling and retries
once on 429 — for scoring a large universe regularly, track your own
request budget or ask Stacks about a commercial plan.
"""

from __future__ import annotations

import time
from typing import Any, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

from ..models import AnnualFinancials, CompanyFinancials, PricePoint, Sector
from .base import DataSource

STACKS_BASE_URL = "https://stacksja.com/api/v1/public"
STACKS_DIVIDENDS_BASE_URL = STACKS_BASE_URL  # dividends live outside /public

# Placeholder — populate as you confirm each covered ticker's sector.
# Stacks' API does not expose sector directly as of this build.
SECTOR_OVERRIDES: dict[str, Sector] = {
    # "NCBFG": Sector.FINANCE,
    # "GK": Sector.RETAIL_DISTRIBUTION,
}


class StacksDataSource(DataSource):
    def __init__(
        self,
        api_key: str,
        min_seconds_between_requests: float = 1.05,  # keeps well under 60/min
        timeout: float = 15.0,
    ):
        if requests is None:
            raise ImportError(
                "The 'requests' package is required for StacksDataSource. "
                "Install it with: pip install requests"
            )
        self.api_key = api_key
        self.timeout = timeout
        self._min_interval = min_seconds_between_requests
        self._last_request_time = 0.0

    # -- low-level request handling ---------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _get(self, url: str, params: Optional[dict] = None) -> Any:
        self._throttle()
        headers = {"X-API-Key": self.api_key}
        resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        self._last_request_time = time.monotonic()

        if resp.status_code == 429:
            # Simple one-shot backoff and retry, per Stacks' documented
            # per-minute limit. For production batch runs, prefer tracking
            # your own request budget instead of relying on this.
            time.sleep(60)
            resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)

        resp.raise_for_status()
        return resp.json()

    # -- DataSource interface ----------------------------------------------

    def list_tickers(self, market: Optional[str] = None) -> list[str]:
        """market: None for all, or 'main' / 'junior' per Stacks' filter."""
        params = {"market": market} if market else None
        data = self._get(f"{STACKS_BASE_URL}/stocks", params=params)
        return [s["symbol"] for s in data.get("stocks", [])]

    def _get_shares_outstanding(self, ticker: str) -> Optional[float]:
        """Shares outstanding isn't on /financials, but it is on /pe-ratios."""
        data = self._get(f"{STACKS_BASE_URL}/pe-ratios")
        for row in data.get("pe_ratios", []):
            if row.get("symbol") == ticker:
                return row.get("shares_outstanding")
        return None

    def _get_annual_dividend(self, ticker: str, fiscal_year: int) -> Optional[float]:
        """
        Sum declared dividends per share for a given fiscal year from the
        dividends endpoint. Note: this sums by declaration_date year, which
        is a reasonable proxy but won't always line up exactly with a
        company's fiscal year if it doesn't run calendar-year.
        """
        data = self._get(f"{STACKS_DIVIDENDS_BASE_URL}/stocks/{ticker}/dividends", params={"limit": 50})
        total = 0.0
        found = False
        for d in data.get("dividends", []):
            decl_date = d.get("declaration_date", "")
            if decl_date.startswith(str(fiscal_year)):
                total += float(d.get("amount_per_share", 0) or 0)
                found = True
        return total if found else None

    def _map_financial_record(self, record: dict) -> Optional[AnnualFinancials]:
        """
        Map one record from GET /stock/{symbol}/financials into
        AnnualFinancials, using the 'current' period only (the
        'comparative' period is the same metric a year prior, used
        by Stacks for period-over-period display, not needed here
        since we assemble our own multi-year history from separate
        records).
        """
        current = record.get("periods", {}).get("current")
        if not current:
            return None
        revenue = current.get("revenue")
        net_income = current.get("net_income")
        eps = current.get("eps_basic")
        total_equity = current.get("total_equity")
        total_assets = current.get("total_assets")
        if None in (revenue, net_income, eps, total_equity, total_assets):
            return None  # can't build a usable record without the required fields

        return AnnualFinancials(
            fiscal_year=record["fiscal_year"],
            revenue=revenue,
            net_income=net_income,
            eps=eps,
            total_equity=total_equity,
            total_assets=total_assets,
            interest_expense=abs(current["finance_costs"]) if current.get("finance_costs") is not None else None,
            # total_debt, ebitda, operating/free cash flow, current
            # assets/liabilities intentionally left as None — see module
            # docstring for why these aren't approximated from what's here.
        )

    def _select_annual_records(self, raw_financials: list[dict]) -> list[dict]:
        """
        Stacks returns a mix of quarterly and annual filings per company,
        not guaranteed to include a full run of clean annual statements
        for every ticker. Prefer records that look like full-year filings;
        this is a heuristic, not a guarantee — spot-check results per
        company, especially ones that only file quarterlies with Stacks
        so far.
        """
        annual_like = [
            r for r in raw_financials
            if r.get("period_type") in ("FY", "Annual", "Q4")  # Q4 often approximates full-year for JSE filers
        ]
        return annual_like if annual_like else raw_financials

    def get_company_financials(self, ticker: str) -> CompanyFinancials:
        snapshot = self._get(f"{STACKS_BASE_URL}/stock/{ticker}")
        financials_raw = self._get(f"{STACKS_BASE_URL}/stock/{ticker}/financials")
        history_raw = self._get(f"{STACKS_BASE_URL}/stock/{ticker}/history", params={"limit": 365})

        records = self._select_annual_records(financials_raw.get("financials", []))
        history: list[AnnualFinancials] = []
        seen_years: set[int] = set()
        for record in sorted(records, key=lambda r: r.get("fiscal_year", 0)):
            year = record.get("fiscal_year")
            if year in seen_years:
                continue  # keep first (already sorted by year; avoids dupes across quarters)
            annual = self._map_financial_record(record)
            if annual is None:
                continue
            annual.dividend_per_share = self._get_annual_dividend(ticker, year)
            history.append(annual)
            seen_years.add(year)

        shares_outstanding = self._get_shares_outstanding(ticker)
        if shares_outstanding is not None and history:
            history[-1].shares_outstanding = shares_outstanding

        prices = [
            PricePoint(
                trade_date=_parse_date(p["trade_date"]),
                close=_parse_price(p["closing_price"]),
                volume=_parse_price(p.get("volume", 0)),
            )
            for p in history_raw.get("history", [])
        ]
        prices.sort(key=lambda p: p.trade_date)

        current_price = _parse_price(snapshot.get("closing_price")) if snapshot.get("closing_price") else None
        sector = SECTOR_OVERRIDES.get(ticker, Sector.OTHER)

        return CompanyFinancials(
            ticker=ticker,
            name=snapshot.get("company_name", ticker),
            sector=sector,
            history=history,
            current_price=current_price,
            price_history=prices,
            market_index_history=[],  # Stacks docs don't expose a documented
            # index-history REST endpoint as of this build; momentum will
            # fall back to absolute (not index-relative) returns — see
            # scoring.score_momentum.
        )


def _parse_date(value: str):
    from datetime import datetime
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_price(value) -> float:
    """Stacks returns some numeric fields as strings with commas
    (e.g. volume: "2,598,715") — normalize before casting to float."""
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).replace(",", ""))
