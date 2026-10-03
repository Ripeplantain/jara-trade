import asyncio
import math

import numpy as np
import pytest

from app.market.cache import PriceCache
from app.market.seed_prices import DEFAULT_WATCHLIST
from app.market.simulator import TRADING_SECONDS_PER_YEAR, GBMSimulator, SimulatorDataSource


def test_prices_stay_positive():
    sim = GBMSimulator(DEFAULT_WATCHLIST, seed=1, event_probability=0.01)
    for _ in range(20_000):
        assert all(p > 0 for p in sim.step().values())


def test_same_seed_same_path():
    a, b = GBMSimulator(["AAPL", "TSLA"], seed=7), GBMSimulator(["AAPL", "TSLA"], seed=7)
    assert [a.step() for _ in range(100)] == [b.step() for _ in range(100)]


def test_volatility_and_correlation_match_parameters():
    tickers = ["AAPL", "MSFT", "JPM", "V", "TSLA"]
    sim = GBMSimulator(tickers, seed=42, event_probability=0)
    steps = 46_800  # one trading day of 500 ms ticks
    paths = np.empty((steps + 1, len(tickers)))
    paths[0] = [sim.get_price(t) for t in tickers]
    for i in range(1, steps + 1):
        tick = sim.step()
        paths[i] = [tick[t] for t in tickers]
    returns = np.diff(np.log(paths), axis=0)
    ticks_per_year = TRADING_SECONDS_PER_YEAR / 0.5
    vol = returns.std(axis=0) * math.sqrt(ticks_per_year)
    corr = np.corrcoef(returns.T)
    assert vol[0] == pytest.approx(0.22, rel=0.03)   # AAPL
    assert vol[4] == pytest.approx(0.50, rel=0.03)   # TSLA
    assert corr[0, 1] == pytest.approx(0.6, abs=0.03)  # AAPL / MSFT
    assert corr[2, 3] == pytest.approx(0.5, abs=0.03)  # JPM / V
    assert corr[0, 2] == pytest.approx(0.3, abs=0.03)  # AAPL / JPM
    assert corr[0, 4] == pytest.approx(0.3, abs=0.03)  # AAPL / TSLA


def test_events_move_two_to_five_percent():
    sim = GBMSimulator(["AAPL"], seed=3, event_probability=1.0)
    before = sim.get_price("AAPL")
    after = sim.step()["AAPL"]
    assert 0.019 < abs(after / before - 1) < 0.051


def test_add_and_remove_down_to_zero():
    sim = GBMSimulator(["AAPL", "MSFT"], seed=1)
    sim.add_ticker("ZZZZ")
    assert 50 <= sim.get_price("ZZZZ") <= 300
    sim.remove_ticker("AAPL")
    sim.remove_ticker("MSFT")
    assert set(sim.step()) == {"ZZZZ"}
    sim.remove_ticker("ZZZZ")
    assert sim.step() == {}


async def test_data_source_seeds_runs_and_stops():
    cache = PriceCache()
    source = SimulatorDataSource(cache, update_interval=0.02, seed=1)
    await source.start(["AAPL", "TSLA"])
    assert cache.get_price("AAPL") == 190.0
    assert cache.get("AAPL").prev_close == 190.0
    v = cache.version
    await asyncio.sleep(0.1)
    assert cache.version > v
    assert source.status().running

    await source.add_ticker("PYPL")
    assert cache.get_price("PYPL") is not None  # tradeable immediately
    await source.remove_ticker("TSLA")
    assert cache.get("TSLA") is None

    await source.stop()
    await source.stop()  # safe twice
    assert not source.status().running


async def test_add_before_start_is_kept():
    cache = PriceCache()
    source = SimulatorDataSource(cache, update_interval=0.02, seed=1)
    await source.add_ticker("NVDA")
    await source.start(["AAPL"])
    assert source.get_tickers() == ["AAPL", "NVDA"]
    await source.stop()


async def test_start_twice_raises():
    source = SimulatorDataSource(PriceCache(), update_interval=0.05)
    await source.start(["AAPL"])
    with pytest.raises(RuntimeError):
        await source.start(["AAPL"])
    await source.stop()


async def test_step_failure_is_reported_and_loop_survives(monkeypatch):
    cache = PriceCache()
    source = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await source.start(["AAPL"])
    real_step = source._sim.step

    def broken_step():
        raise ValueError("boom")

    monkeypatch.setattr(source._sim, "step", broken_step)
    await asyncio.sleep(0.05)
    assert "boom" in source.status().last_error
    assert source.status().running  # the loop survived

    monkeypatch.setattr(source._sim, "step", real_step)
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version > v
    assert source.status().last_error is None
    await source.stop()
