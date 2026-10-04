"""Correlated geometric Brownian motion: the default source when no Massive key is set."""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .models import SourceStatus
from .seed_prices import (
    CROSS_SECTOR_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SECTORS,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
    UNKNOWN_PRICE_RANGE,
)

logger = logging.getLogger(__name__)

# 252 trading days * 6.5 hours * 3600 seconds
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600


class GBMSimulator:
    """Pure math: correlated geometric Brownian motion. No asyncio, no cache."""

    def __init__(
        self,
        tickers: list[str] | None = None,
        tick_seconds: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._dt = tick_seconds / TRADING_SECONDS_PER_YEAR
        self._event_probability = event_probability
        self._rng = np.random.default_rng(seed)
        self._py_rng = random.Random(seed)

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None
        self.add_tickers(tickers or [])

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick and return the new (unrounded) prices."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z  # independent draws -> correlated draws

        sqrt_dt = math.sqrt(self._dt)
        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * sqrt_dt * z[i]
            price = self._prices[ticker] * math.exp(drift + diffusion)

            # Occasional sudden 2-5% move, for visual drama.
            if self._py_rng.random() < self._event_probability:
                shock = self._py_rng.uniform(0.02, 0.05) * self._py_rng.choice([-1, 1])
                price *= 1 + shock
                logger.debug("Simulated event on %s: %+.1f%%", ticker, shock * 100)

            self._prices[ticker] = price
            result[ticker] = price
        return result

    def add_tickers(self, tickers: list[str]) -> None:
        """Add several tickers with a single correlation-matrix rebuild."""
        new = [t for t in dict.fromkeys(tickers) if t not in self._prices]
        if not new:
            return
        for ticker in new:
            self._tickers.append(ticker)
            self._prices[ticker] = SEED_PRICES.get(ticker) or self._py_rng.uniform(
                *UNKNOWN_PRICE_RANGE
            )
            self._params[ticker] = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)
        self._rebuild_cholesky()

    def add_ticker(self, ticker: str) -> None:
        self.add_tickers([ticker])

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def _rebuild_cholesky(self) -> None:
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        self._cholesky = np.linalg.cholesky(corr)


def pairwise_correlation(a: str, b: str) -> float:
    sector_a, sector_b = SECTORS.get(a), SECTORS.get(b)
    if "tsla" in (sector_a, sector_b):
        return TSLA_CORR
    if sector_a is None or sector_b is None or sector_a != sector_b:
        return CROSS_SECTOR_CORR
    return INTRA_TECH_CORR if sector_a == "tech" else INTRA_FINANCE_CORR


class SimulatorDataSource(MarketDataSource):
    """Runs a GBMSimulator on a timer and writes each tick into the cache."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._sim = GBMSimulator(
            tick_seconds=update_interval, event_probability=event_probability, seed=seed
        )
        self._task: asyncio.Task | None = None
        self._last_tick: float | None = None
        self._last_error: str | None = None

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None:
            raise RuntimeError("SimulatorDataSource already started")
        self._sim.add_tickers(tickers)
        # Seed the cache so the first SSE event and the first trade have prices.
        for ticker in self._sim.get_tickers():
            self._seed_cache(ticker)
        self._task = asyncio.create_task(self._run_loop(), name="market-simulator")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        if self._sim.get_price(ticker) is not None:
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)  # tradeable immediately, unlike Massive

    async def remove_ticker(self, ticker: str) -> None:
        self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return sorted(self._sim.get_tickers())

    def status(self) -> SourceStatus:
        return SourceStatus(
            source="simulator",
            mode="simulated",
            running=self._task is not None and not self._task.done(),
            tickers=len(self._sim.get_tickers()),
            last_success=self._last_tick,
            last_error=self._last_error,
        )

    def _seed_cache(self, ticker: str) -> None:
        price = self._sim.get_price(ticker)
        if price is not None:
            # The starting price doubles as "yesterday's close" for daily change.
            self._cache.update(ticker, price, prev_close=price)

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            # Schedule against the loop clock so slow ticks do not accumulate drift.
            next_tick += self._interval
            if next_tick < loop.time():  # fell far behind (laptop slept): skip, don't burst
                next_tick = loop.time() + self._interval
            await asyncio.sleep(max(0.0, next_tick - loop.time()))
            try:
                now = time.time()
                for ticker, price in self._sim.step().items():
                    self._cache.update(ticker, price, timestamp=now)
                self._last_tick = now
                self._last_error = None
            except Exception as exc:  # never let the loop die
                self._last_error = repr(exc)
                logger.exception("Simulator step failed")
