import json

import httpx
import pytest
from fastapi import FastAPI

from app.market import (
    MarketDataService,
    PriceCache,
    PriceUnavailableError,
    create_market_router,
)
from app.market.simulator import SimulatorDataSource
from app.market.stream import price_events


async def test_reconcile_adds_and_removes():
    cache = PriceCache()
    market = MarketDataService(cache, SimulatorDataSource(cache, update_interval=1, seed=1))
    await market.start(["aapl", "tsla"])
    added, removed = await market.reconcile(["AAPL", "nvda"])
    assert (added, removed) == (["NVDA"], ["TSLA"])
    assert market.get_price("nvda") == 800.0
    with pytest.raises(PriceUnavailableError):
        market.require_price("TSLA")
    await market.stop()


async def test_stream_emits_once_per_version():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    ticks = iter([False, False, False, True])

    async def is_disconnected():
        return next(ticks)

    events = [e async for e in price_events(cache, is_disconnected, interval=0)]
    assert events[0] == "retry: 1000\n\n"
    data = [e for e in events if "data:" in e]
    assert len(data) == 1
    payload = json.loads(data[0].split("data: ", 1)[1])
    assert payload["AAPL"]["price"] == 190.0 and payload["AAPL"]["direction"] == "flat"


async def test_rest_routes():
    cache = PriceCache()
    cache.update("AAPL", 190.0, prev_close=188.0)
    market = MarketDataService(cache, SimulatorDataSource(cache))
    app = FastAPI()
    app.include_router(create_market_router(market))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/market/prices/aapl")).json()["day_change"] == 2.0
        assert (await client.get("/api/market/prices/ZZZZ")).status_code == 404
        assert (await client.get("/api/market/prices/bad;ticker")).status_code == 422
        assert (await client.get("/api/market/prices?tickers=bad;ticker")).status_code == 422
        assert set((await client.get("/api/market/prices?tickers=AAPL,MSFT")).json()) == {"AAPL"}
        assert set((await client.get("/api/market/prices")).json()) == {"AAPL"}
        assert (await client.get("/api/market/status")).json()["source"] == "simulator"
        history = (await client.get("/api/market/history/aapl")).json()
        assert history["ticker"] == "AAPL" and len(history["points"]) == 1


async def test_stream_sends_keepalive_when_idle():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    ticks = iter([False, False, False, True])

    async def is_disconnected():
        return next(ticks)

    events = [e async for e in price_events(cache, is_disconnected, interval=0, heartbeat=0)]
    assert events.count(": keep-alive\n\n") == 2


async def test_stream_resends_after_change():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    state = {"n": 0}

    async def is_disconnected():
        state["n"] += 1
        if state["n"] == 2:
            cache.update("AAPL", 191.0)
        return state["n"] > 3

    events = [e async for e in price_events(cache, is_disconnected, interval=0)]
    data = [json.loads(e.split("data: ", 1)[1]) for e in events if "data:" in e]
    assert [d["AAPL"]["price"] for d in data] == [190.0, 191.0]
    assert data[1]["AAPL"]["direction"] == "up"


async def test_service_normalizes_and_filters():
    cache = PriceCache()
    market = MarketDataService(cache, SimulatorDataSource(cache, update_interval=1, seed=1))
    await market.start([" aapl", "MSFT", "AAPL"])
    assert market.source.get_tickers() == ["AAPL", "MSFT"]
    assert set(market.get_prices(["msft"])) == {"MSFT"}
    assert market.require_price("aapl").price == 190.0
    assert market.status().running
    await market.stop()


def test_from_settings_builds_simulator():
    from app.market import MarketSettings

    market = MarketDataService.from_settings(MarketSettings())
    assert isinstance(market.source, SimulatorDataSource)
