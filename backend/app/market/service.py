"""MarketDataService: the one object the rest of the backend talks to."""
from __future__ import annotations

from collections.abc import Iterable

from .cache import PriceCache
from .config import MarketSettings
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PricePoint, PriceUnavailableError, PriceUpdate, SourceStatus, normalize_ticker


class MarketDataService:
    """Facade over the cache (reads) and the source (which tickers are tracked).

    Routes, trade execution, portfolio valuation and the LLM context all go
    through this. None of them touch MarketDataSource or MASSIVE_API_KEY.
    """

    def __init__(self, cache: PriceCache, source: MarketDataSource) -> None:
        self.cache = cache
        self.source = source

    @classmethod
    def from_settings(cls, settings: MarketSettings | None = None) -> MarketDataService:
        cache = PriceCache()
        return cls(cache, create_market_data_source(cache, settings))

    # ------------------------------------------------------------------ lifecycle

    async def start(self, tickers: Iterable[str]) -> None:
        await self.source.start(sorted({normalize_ticker(t) for t in tickers}))

    async def stop(self) -> None:
        await self.source.stop()

    # ------------------------------------------------------------------ reads (cache only)

    def get_price(self, ticker: str) -> float | None:
        return self.cache.get_price(normalize_ticker(ticker))

    def require_price(self, ticker: str) -> PriceUpdate:
        """The price to fill a trade at. Raises PriceUnavailableError rather than guess."""
        ticker = normalize_ticker(ticker)
        update = self.cache.get(ticker)
        if update is None:
            raise PriceUnavailableError(ticker)
        return update

    def get_prices(self, tickers: Iterable[str] | None = None) -> dict[str, PriceUpdate]:
        """All cached prices, or just the requested ones that have a price."""
        prices = self.cache.get_all()
        if tickers is None:
            return prices
        wanted = {normalize_ticker(t) for t in tickers}
        return {t: u for t, u in prices.items() if t in wanted}

    def history(self, ticker: str) -> list[PricePoint]:
        return self.cache.history(normalize_ticker(ticker))

    def status(self) -> SourceStatus:
        return self.source.status()

    # ------------------------------------------------------------------ tracking

    async def reconcile(self, wanted: Iterable[str]) -> tuple[list[str], list[str]]:
        """Make the tracked set equal `wanted` (watchlist plus held positions).

        The only way routes change tracking, so a ticker that is still held never
        loses its price because it left the watchlist. Returns (added, removed).
        """
        target = {normalize_ticker(t) for t in wanted}
        current = set(self.source.get_tickers())
        added = sorted(target - current)
        removed = sorted(current - target)
        for ticker in added:
            await self.source.add_ticker(ticker)
        for ticker in removed:
            await self.source.remove_ticker(ticker)
        return added, removed
