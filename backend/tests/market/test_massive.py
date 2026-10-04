import asyncio
import json
from datetime import date

import httpx
import pytest

from app.market.cache import PriceCache
from app.market.massive_client import (
    MassiveClient,
    MassiveDataSource,
    parse_snapshot_entry,
)

ns = 1_000_000_000


def snap(ticker, *, trade=None, minute=None, day=None, prev=None):
    return {
        "ticker": ticker,
        "lastTrade": {"p": trade, "t": 1_700_000_000 * ns} if trade else None,
        "min": {"c": minute or 0, "t": 1_700_000_000_000},
        "day": {"c": day or 0},
        "prevDay": {"c": prev or 0},
        "updated": 1_700_000_001 * ns,
    }


def make_client(handler) -> MassiveClient:
    return MassiveClient("test-key", transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- parsing


def test_fallback_chain():
    assert parse_snapshot_entry(snap("A", trade=10, minute=9, day=8, prev=7)).price == 10
    assert parse_snapshot_entry(snap("A", minute=9, day=8, prev=7)).price == 9
    assert parse_snapshot_entry(snap("A", day=8, prev=7)).price == 8
    q = parse_snapshot_entry(snap("A", prev=7))  # before the open: zeros everywhere else
    assert q.price == 7 and q.timestamp == 1_700_000_001
    assert parse_snapshot_entry(snap("A")) is None


def test_timestamp_units():
    assert parse_snapshot_entry(snap("A", trade=10)).timestamp == 1_700_000_000  # ns
    assert parse_snapshot_entry(snap("A", minute=9)).timestamp == 1_700_000_000  # ms


# ---------------------------------------------------------------- snapshot mode


async def test_snapshot_mode_updates_cache_and_ignores_unknown():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["tickers"] = request.url.params["tickers"]
        body = {"status": "OK", "tickers": [snap("AAPL", minute=190.12, prev=189.0)]}
        return httpx.Response(200, json=body)  # NOTREAL silently missing

    cache = PriceCache()
    source = MassiveDataSource(cache, make_client(handler))
    await source.start(["AAPL", "NOTREAL"])
    assert seen == {"auth": "Bearer test-key", "tickers": "AAPL,NOTREAL"}
    assert cache.get("AAPL").price == 190.12
    assert cache.get("AAPL").prev_close == 189.0
    assert cache.get("NOTREAL") is None
    assert source.mode == "snapshot" and source.status().last_error is None
    await source.stop()


async def test_add_ticker_wakes_poller():
    calls = []

    def handler(request):
        calls.append(request.url.params["tickers"])
        tickers = request.url.params["tickers"].split(",")
        return httpx.Response(200, json={"tickers": [snap(t, minute=100) for t in tickers]})

    cache = PriceCache()
    source = MassiveDataSource(cache, make_client(handler), poll_interval=60)
    await source.start(["AAPL"])
    await source.add_ticker("PYPL")
    await asyncio.sleep(0.05)
    assert calls == ["AAPL", "AAPL,PYPL"]
    assert cache.get_price("PYPL") == 100
    await source.stop()


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_errors_keep_last_prices(status):
    responses = [httpx.Response(200, json={"tickers": [snap("AAPL", minute=190)]})]

    def handler(request):
        return responses.pop(0) if responses else httpx.Response(status, json={"error": "nope"})

    cache = PriceCache()
    source = MassiveDataSource(cache, make_client(handler))
    await source.start(["AAPL"])
    await source._poll_once()
    assert cache.get_price("AAPL") == 190
    st = source.status()
    assert st.consecutive_failures == 1 and str(status) in st.last_error
    assert source._next_delay() == 30  # 15 s doubled
    await source.stop()


async def test_network_error_keeps_last_prices():
    def handler(request):
        raise httpx.ConnectError("boom")

    cache = PriceCache()
    cache.update("AAPL", 190)
    source = MassiveDataSource(cache, make_client(handler))
    await source.start(["AAPL"])
    assert cache.get_price("AAPL") == 190
    assert "network error" in source.status().last_error
    await source.stop()


# ---------------------------------------------------------------- end-of-day mode


def grouped(day_bars):
    if not day_bars:
        return httpx.Response(200, json={"status": "OK", "resultsCount": 0})
    results = [{"T": t, "o": o, "c": c, "t": 1_759_377_600_000} for t, o, c in day_bars]
    return httpx.Response(200, json={"status": "OK", "results": results})


async def test_403_switches_to_eod_with_holiday_walk_back_and_true_prev_close():
    requested = []
    market = {
        "2026-09-07": [],  # Labor Day (Monday): empty
        "2026-09-04": [("AAPL", 100.0, 101.0), ("MSFT", 400.0, 404.0)],  # Friday
        "2026-09-03": [("AAPL", 99.0, 100.0), ("MSFT", 398.0, 400.0)],  # Thursday
    }

    def handler(request):
        path = request.url.path
        if "snapshot" in path:
            return httpx.Response(403, json={"status": "NOT_AUTHORIZED"})
        day = path.rsplit("/", 1)[1]
        requested.append(day)
        return grouped(market.get(day, []))

    cache = PriceCache()
    source = MassiveDataSource(cache, make_client(handler), today=lambda: date(2026, 9, 8))
    await source.start(["AAPL", "MSFT"])
    assert source.mode == "eod"
    assert requested == ["2026-09-07", "2026-09-04", "2026-09-03"]
    aapl = cache.get("AAPL")
    assert (aapl.price, aapl.prev_close, aapl.day_change_percent) == (101.0, 100.0, 1.0)

    # Same day: no more calls. A new ticker is served from memory.
    await source._poll_once()
    await source.add_ticker("MSFT")
    assert len(requested) == 3
    await source.stop()


async def test_eod_add_ticker_costs_no_call():
    calls = []

    def handler(request):
        if "snapshot" in request.url.path:
            return httpx.Response(403, json={})
        calls.append(request.url.path)
        return grouped([("AAPL", 1.0, 2.0), ("PYPL", 60.0, 61.0)])

    cache = PriceCache()
    source = MassiveDataSource(cache, make_client(handler), today=lambda: date(2026, 10, 2))
    await source.start(["AAPL"])
    n = len(calls)
    await source.add_ticker("PYPL")
    assert cache.get_price("PYPL") == 61.0 and len(calls) == n
    await source.stop()


async def test_eod_falls_back_to_open_when_prev_day_fails():
    def handler(request):
        if "snapshot" in request.url.path:
            return httpx.Response(403, json={})
        if request.url.path.endswith("2026-10-01"):
            return httpx.Response(429, json={"error": "slow down"})
        return grouped([("AAPL", 100.0, 102.0)])

    cache = PriceCache()
    source = MassiveDataSource(cache, make_client(handler), today=lambda: date(2026, 10, 3))
    await source.start(["AAPL"])
    assert cache.get("AAPL").prev_close == 100.0  # the open
    assert source._eod_target is None  # retried on the next poll
    await source.stop()


async def test_snapshot_batches_large_ticker_sets(monkeypatch):
    import app.market.massive_client as mc

    monkeypatch.setattr(mc, "SNAPSHOT_BATCH", 2)
    batches = []

    def handler(request):
        tickers = request.url.params["tickers"].split(",")
        batches.append(tickers)
        return httpx.Response(200, json={"tickers": [snap(t, minute=10) for t in tickers]})

    cache = PriceCache()
    source = MassiveDataSource(cache, make_client(handler))
    await source.start(["A", "B", "C", "D", "E"])
    assert batches == [["A", "B"], ["C", "D"], ["E"]]
    assert len(cache) == 5
    await source.stop()


async def test_backoff_schedule():
    source = MassiveDataSource(PriceCache(), make_client(lambda r: httpx.Response(500)))
    delays = []
    for failures in range(6):
        source._failures = failures
        delays.append(source._next_delay())
    assert delays == [15, 30, 60, 120, 240, 300]
    source._mode = "eod"
    eod = []
    for failures in range(6):
        source._failures = failures
        eod.append(source._next_delay())
    assert eod == [900, 60, 120, 240, 480, 900]
    await source.stop()


async def test_success_resets_failures():
    responses = [httpx.Response(500), httpx.Response(200, json={"tickers": [snap("AAPL", minute=5)]})]
    source = MassiveDataSource(PriceCache(), make_client(lambda r: responses.pop(0)))
    await source.start(["AAPL"])
    assert source.status().consecutive_failures == 1
    await source._poll_once()
    st = source.status()
    assert st.consecutive_failures == 0 and st.last_error is None and st.last_success
    await source.stop()


async def test_removed_ticker_not_written_back():
    cache = PriceCache()
    source = None

    async def slow_handler(request):
        await source.remove_ticker("AAPL")  # removed while the request is in flight
        return httpx.Response(200, json={"tickers": [snap("AAPL", minute=5)]})

    source = MassiveDataSource(cache, make_client(slow_handler))
    await source.start(["AAPL"])
    assert cache.get("AAPL") is None
    await source.stop()


async def test_eod_refetches_when_date_rolls_over():
    calls = []
    today = {"d": date(2026, 10, 2)}  # Friday -> prices Thursday

    def handler(request):
        if "snapshot" in request.url.path:
            return httpx.Response(403, json={})
        calls.append(request.url.path.rsplit("/", 1)[1])
        return grouped([("AAPL", 1.0, 2.0)])

    source = MassiveDataSource(PriceCache(), make_client(handler), today=lambda: today["d"])
    await source.start(["AAPL"])
    assert calls == ["2026-10-01", "2026-09-30"]
    today["d"] = date(2026, 10, 5)  # Monday -> prices Friday
    await source._poll_once()
    assert calls[2:] == ["2026-10-02", "2026-10-01"]
    await source.stop()


async def test_api_key_sent_in_header_not_url():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(401, json={"status": "ERROR", "error": "Unknown API Key"})

    source = MassiveDataSource(PriceCache(), make_client(handler))
    await source.start(["AAPL"])
    assert "test-key" not in seen["url"] and seen["auth"] == "Bearer test-key"
    assert "test-key" not in source.status().last_error
    assert "Unknown API Key" in source.status().last_error
    await source.stop()


async def test_stop_closes_http_client():
    client = make_client(lambda r: httpx.Response(200, json={"tickers": []}))
    source = MassiveDataSource(PriceCache(), client)
    await source.start(["AAPL"])
    await source.stop()
    assert client._http.is_closed
