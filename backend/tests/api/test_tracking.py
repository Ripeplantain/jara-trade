"""The market service must always track exactly watchlist ∪ held positions."""
from __future__ import annotations

import asyncio

from app.market.seed_prices import DEFAULT_WATCHLIST
from app.tracking import TrackingSync

from .conftest import trade


def tracked(market) -> list[str]:
    return market.source.get_tickers()


async def test_sync_adds_missing_and_drops_stale_tickers(db, market):
    db.init(["AAPL", "MSFT"])
    with db.transaction() as conn:
        conn.execute("INSERT INTO positions VALUES ('TSLA', 1, 250)")
    await market.start(["NVDA", "AAPL"])  # NVDA is stale, MSFT and TSLA are missing
    assert market.get_price("NVDA") == 800.0

    await TrackingSync(db, market).sync()
    assert tracked(market) == ["AAPL", "MSFT", "TSLA"]
    assert market.get_price("NVDA") is None  # its price left the cache with it
    assert market.get_price("TSLA") == 250.0


async def test_sync_is_idempotent_and_safe_to_overlap(db, market):
    db.init(["AAPL", "MSFT"])
    sync = TrackingSync(db, market)
    await asyncio.gather(*(sync.sync() for _ in range(5)))
    assert tracked(market) == ["AAPL", "MSFT"]
    version = market.cache.version
    await sync.sync()
    assert tracked(market) == ["AAPL", "MSFT"]
    assert market.cache.version == version  # nothing re-added, no SSE wake-up


def test_tracked_set_follows_the_database_through_a_session(client, market, db):
    def check() -> list[str]:
        assert tracked(market) == db.tracked_tickers()
        return tracked(market)

    assert check() == sorted(DEFAULT_WATCHLIST)

    client.post("/api/watchlist", json={"ticker": "PYPL"})
    assert "PYPL" in check()

    trade(client, "PYPL", "buy", 4)
    trade(client, "TSLA", "buy", 1)
    client.delete("/api/watchlist/PYPL")  # held: stays
    client.delete("/api/watchlist/TSLA")  # held: stays
    client.delete("/api/watchlist/NFLX")  # not held: goes
    now = check()
    assert "PYPL" in now and "TSLA" in now and "NFLX" not in now

    trade(client, "PYPL", "sell", 1.5)  # partial: stays
    assert "PYPL" in check()
    trade(client, "PYPL", "sell", 2.5)  # flat and off the watchlist: goes
    assert "PYPL" not in check()
    assert market.get_price("PYPL") is None

    client.post("/api/portfolio/reset")  # TSLA position is gone, and it left the watchlist
    remaining = [t for t in DEFAULT_WATCHLIST if t not in ("TSLA", "NFLX")]
    assert check() == sorted(remaining)
    assert client.get("/api/watchlist").json() == {"tickers": remaining}


def test_full_sell_of_a_watchlist_ticker_keeps_it_tracked(client, market):
    trade(client, "AAPL", "buy", 2)
    trade(client, "AAPL", "sell", 2)
    assert "AAPL" in tracked(market)
    assert market.get_price("AAPL") == 190.0


def test_reset_drops_every_held_off_watchlist_ticker_and_nothing_else(client, market):
    for ticker in ("TSLA", "NVDA", "AAPL"):
        trade(client, ticker, "buy", 1)
    client.delete("/api/watchlist/TSLA")
    client.delete("/api/watchlist/NVDA")
    assert {"TSLA", "NVDA"} <= set(tracked(market))

    client.post("/api/portfolio/reset")
    assert tracked(market) == sorted(set(DEFAULT_WATCHLIST) - {"TSLA", "NVDA"})


def test_refused_trade_does_not_change_tracking(client, market):
    before = tracked(market)
    assert trade(client, "PYPL", "buy", 1).status_code == 409  # not tracked, so no price
    assert trade(client, "AAPL", "buy", 1000).status_code == 400
    assert trade(client, "AAPL", "sell", 1).status_code == 400
    assert tracked(market) == before


def test_noop_watchlist_add_heals_a_lost_ticker(client, market):
    """If an earlier reconcile failed part-way, the next write puts tracking right."""
    market.source.tickers.discard("AAPL")
    market.cache.remove("AAPL")

    assert client.post("/api/watchlist", json={"ticker": "AAPL"}).status_code == 200
    assert "AAPL" in tracked(market)
    assert market.get_price("AAPL") == 190.0


def test_restart_tracks_watchlist_and_positions_only(db, market):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(market=market, db=db)) as first:
        trade(first, "NVDA", "buy", 1)
        first.delete("/api/watchlist/NVDA")
        first.delete("/api/watchlist/META")
    market.source.tickers.clear()

    with TestClient(create_app(market=market, db=db)):
        assert tracked(market) == sorted(set(DEFAULT_WATCHLIST) - {"META"})
