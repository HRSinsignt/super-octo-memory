"""
CSV-based data source. This is what lets you run the whole pipeline
today, before any live JSE API access is wired up: export financials
into the two CSV formats below (see sample_data/ for examples) and the
scoring engine runs exactly as it would against a live feed.

Expected files in `data_dir`:
  financials.csv  - one row per (ticker, fiscal_year)
  prices.csv      - one row per (ticker, date, close, volume)
  index_prices.csv - one row per (date, close) for the JSE index, used
                      for momentum's relative-return calculation

See sample_data/ for the exact column layout.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..models import AnnualFinancials, CompanyFinancials, PricePoint, Sector
from .base import DataSource


def _opt_float(row: dict, key: str) -> float | None:
    """Parse a CSV field to float, treating blank/missing as None rather
    than crashing or silently becoming 0 — matches the new Optional
    fields on AnnualFinancials."""
    value = row.get(key, "")
    if value is None or str(value).strip() == "":
        return None
    return float(value)


class CSVDataSource(DataSource):
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._financials_path = self.data_dir / "financials.csv"
        self._prices_path = self.data_dir / "prices.csv"
        self._index_path = self.data_dir / "index_prices.csv"
        self._names: dict[str, str] = {}
        self._sectors: dict[str, Sector] = {}
        self._current_prices: dict[str, float] = {}

    def list_tickers(self) -> list[str]:
        tickers = set()
        with open(self._financials_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tickers.add(row["ticker"])
        return sorted(tickers)

    def _load_index_prices(self) -> list[PricePoint]:
        if not self._index_path.exists():
            return []
        points = []
        with open(self._index_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                points.append(
                    PricePoint(
                        trade_date=datetime.strptime(row["date"], "%Y-%m-%d").date(),
                        close=float(row["close"]),
                        volume=0.0,
                    )
                )
        points.sort(key=lambda p: p.trade_date)
        return points

    def get_company_financials(self, ticker: str) -> CompanyFinancials:
        history: list[AnnualFinancials] = []
        name = ticker
        sector = Sector.OTHER

        with open(self._financials_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["ticker"] != ticker:
                    continue
                name = row.get("name", ticker)
                sector = Sector(row["sector"]) if row.get("sector") in Sector._value2member_map_ else Sector.OTHER
                history.append(
                    AnnualFinancials(
                        fiscal_year=int(row["fiscal_year"]),
                        revenue=float(row["revenue"]),
                        net_income=float(row["net_income"]),
                        eps=float(row["eps"]),
                        total_equity=float(row["total_equity"]),
                        total_assets=float(row["total_assets"]),
                        total_debt=_opt_float(row, "total_debt"),
                        ebitda=_opt_float(row, "ebitda"),
                        interest_expense=_opt_float(row, "interest_expense"),
                        operating_cash_flow=_opt_float(row, "operating_cash_flow"),
                        free_cash_flow=_opt_float(row, "free_cash_flow"),
                        current_assets=_opt_float(row, "current_assets"),
                        current_liabilities=_opt_float(row, "current_liabilities"),
                        dividend_per_share=_opt_float(row, "dividend_per_share"),
                        shares_outstanding=_opt_float(row, "shares_outstanding"),
                    )
                )
        history.sort(key=lambda h: h.fiscal_year)

        prices: list[PricePoint] = []
        current_price = None
        if self._prices_path.exists():
            with open(self._prices_path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row["ticker"] != ticker:
                        continue
                    prices.append(
                        PricePoint(
                            trade_date=datetime.strptime(row["date"], "%Y-%m-%d").date(),
                            close=float(row["close"]),
                            volume=float(row.get("volume", 0) or 0),
                        )
                    )
            prices.sort(key=lambda p: p.trade_date)
            if prices:
                current_price = prices[-1].close

        return CompanyFinancials(
            ticker=ticker,
            name=name,
            sector=sector,
            history=history,
            current_price=current_price,
            price_history=prices,
            market_index_history=self._load_index_prices(),
        )
