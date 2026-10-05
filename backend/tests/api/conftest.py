"""Fixtures for the watchlist / portfolio API: fixed prices and a temp database."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.market import MarketDataService, MarketDataSource, PriceCache, SourceStatus

PRICES = {"AAPL": 190.0, "MSFT": 420.0, "TSLA": 250.0, "PYPL": 70.0, "NVDA": 800.0}


class FakeSource(MarketDataSource):
    """Prices never move. Tickers missing from PRICES are tracked but never priced."""

    def __init__(self, cache: PriceCache) -> None:
        self.cache = cache
        self.tickers: set[str] = set()

    async def start(self, tickers: list[str]) -> None:
        for ticker in tickers:
            await self.add_ticker(ticker)

    async def stop(self) -> None:
        pass

    async def add_ticker(self, ticker: str) -> None:
        self.tickers.add(ticker)
        if ticker in PRICES:
            self.cache.update(ticker, PRICES[ticker])

    async def remove_ticker(self, ticker: str) -> None:
        self.tickers.discard(ticker)
        self.cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return sorted(self.tickers)

    def status(self) -> SourceStatus:
        return SourceStatus("fake", "simulated", True, len(self.tickers))


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def market() -> MarketDataService:
    cache = PriceCache()
    return MarketDataService(cache, FakeSource(cache))


@pytest.fixture
def client(db, market):
    with TestClient(create_app(market=market, db=db)) as test_client:
        yield test_client


def trade(client, ticker, side, quantity):
    return client.post("/api/trades", json={"ticker": ticker, "side": side, "quantity": quantity})
