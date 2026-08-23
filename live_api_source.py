"""
Template adapter for a live JSE data API (e.g. Stacks, the official JSE
Market Data Feed, or the ICE Consolidated Feed).

This is intentionally a skeleton, not a finished integration: every
provider's exact endpoints, auth, field names, and rate limits differ,
and none should be hard-coded without first confirming them against
that provider's current docs and terms of use. Fill in `_fetch_json`
and the two mapping functions once you've confirmed the actual API
contract, and everything else in the pipeline works unchanged because
it only depends on the `DataSource` interface.

Do NOT ship this file as-is - the method bodies raise NotImplementedError
on purpose.
"""

from __future__ import annotations

from typing import Any

from ..models import AnnualFinancials, CompanyFinancials, PricePoint, Sector
from .base import DataSource


class LiveAPIDataSource(DataSource):
    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _fetch_json(self, path: str, params: dict | None = None) -> Any:
        """
        Replace with an actual HTTP call once the provider's endpoint
        contract is confirmed, e.g.:

            import requests
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            resp = requests.get(f"{self.base_url}{path}", params=params,
                                 headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        Left unimplemented deliberately - wire this up against the
        specific provider you choose (see README for the shortlist).
        """
        raise NotImplementedError(
            "Implement _fetch_json against your chosen JSE data provider's "
            "confirmed API contract before using LiveAPIDataSource."
        )

    def list_tickers(self) -> list[str]:
        data = self._fetch_json("/tickers")
        return [row["symbol"] for row in data]

    def _map_financials(self, raw: dict) -> AnnualFinancials:
        """
        Map one fiscal-year record from the provider's JSON shape into
        AnnualFinancials. Field names below are placeholders - adjust
        to match the real response once confirmed.
        """
        return AnnualFinancials(
            fiscal_year=raw["fiscal_year"],
            revenue=raw["revenue"],
            net_income=raw["net_income"],
            eps=raw["eps"],
            total_debt=raw["total_debt"],
            ebitda=raw["ebitda"],
            interest_expense=raw["interest_expense"],
            operating_cash_flow=raw["operating_cash_flow"],
            free_cash_flow=raw["free_cash_flow"],
            total_equity=raw["total_equity"],
            total_assets=raw["total_assets"],
            current_assets=raw["current_assets"],
            current_liabilities=raw["current_liabilities"],
            dividend_per_share=raw["dividend_per_share"],
            shares_outstanding=raw["shares_outstanding"],
        )

    def get_company_financials(self, ticker: str) -> CompanyFinancials:
        profile = self._fetch_json(f"/companies/{ticker}")
        financials_raw = self._fetch_json(f"/companies/{ticker}/financials")
        prices_raw = self._fetch_json(f"/companies/{ticker}/prices")
        index_raw = self._fetch_json("/index/prices")

        history = sorted(
            (self._map_financials(r) for r in financials_raw),
            key=lambda h: h.fiscal_year,
        )
        prices = [
            PricePoint(trade_date=p["date"], close=p["close"], volume=p.get("volume", 0))
            for p in prices_raw
        ]
        index_prices = [
            PricePoint(trade_date=p["date"], close=p["close"], volume=0.0)
            for p in index_raw
        ]

        return CompanyFinancials(
            ticker=ticker,
            name=profile.get("name", ticker),
            sector=Sector(profile["sector"]) if profile.get("sector") in Sector._value2member_map_ else Sector.OTHER,
            history=history,
            current_price=prices[-1].close if prices else None,
            price_history=prices,
            market_index_history=index_prices,
        )
