"""
Every data source (CSV, Stacks API, official JSE feed, ICE feed, a
scraper) implements this interface. The rest of the pipeline only ever
talks to `DataSource`, so swapping where data comes from means writing
one new adapter file - nothing else in the codebase changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import CompanyFinancials


class DataSource(ABC):
    @abstractmethod
    def list_tickers(self) -> list[str]:
        """Return every ticker this source can provide data for."""
        raise NotImplementedError

    @abstractmethod
    def get_company_financials(self, ticker: str) -> CompanyFinancials:
        """Return a fully populated CompanyFinancials for one ticker."""
        raise NotImplementedError

    def get_all(self) -> list[CompanyFinancials]:
        return [self.get_company_financials(t) for t in self.list_tickers()]
