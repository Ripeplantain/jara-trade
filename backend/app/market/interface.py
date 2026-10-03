"""The contract every market data source implements."""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import SourceStatus


class MarketDataSource(ABC):
    """Produces prices and writes them into a PriceCache.

    Implementations own exactly one background task and are the cache's only
    writer. Nothing downstream reads from a source directly; everything reads
    the cache. Tickers passed in are already normalized (upper-case).
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Seed the cache for `tickers` where possible, then start the background task."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task and release resources. Safe to call twice."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a ticker. No-op if already tracked. Safe before start()."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and drop it from the cache."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers, sorted."""

    @abstractmethod
    def status(self) -> SourceStatus:
        """Current health, for the status endpoint and the UI's data-source badge."""
