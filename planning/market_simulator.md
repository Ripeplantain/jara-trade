# Market simulator

How Jara Trade generates stock prices when no `MASSIVE_API_KEY` is set. This is
the default data source: it needs no network, no key, and no market hours, and
it makes the workstation look alive the moment it starts.

Related documents:

- [market_interface.md](market_interface.md) — the `MarketDataSource` interface
  and `PriceCache` this plugs into.
- [MASSIVE_API.md](MASSIVE_API.md) — the real-data alternative.

## Approach

Each ticker follows geometric Brownian motion (GBM), the standard model behind
Black-Scholes. Every 500 ms each price is multiplied by a small random factor.
Three properties make the result look like a market:

- **Moves are proportional.** A $800 stock moves in bigger dollar steps than a
  $190 one, and a price can never reach zero or go negative.
- **Tickers move together.** Tech stocks tend to rise and fall as a group, as
  do the two financials, so the heatmap shows sector patterns rather than noise.
- **Occasional shocks.** Now and then a ticker jumps 2 to 5 percent in one tick,
  which gives the flash animation, the chart, and the AI something to react to.

## The math

For one tick of length `dt`:

```
S(t + dt) = S(t) * exp( (mu - sigma^2 / 2) * dt  +  sigma * sqrt(dt) * Z )
```

| Symbol | Meaning |
| --- | --- |
| `S` | Price |
| `mu` | Annualized drift (expected return), e.g. `0.05` |
| `sigma` | Annualized volatility, e.g. `0.22` for AAPL, `0.50` for TSLA |
| `dt` | Tick length as a fraction of a trading year |
| `Z` | Standard normal random draw, correlated across tickers |

**Time step.** A trading year is 252 days of 6.5 hours, which is 5,896,800
seconds. A 500 ms tick is therefore `dt = 0.5 / 5,896,800 ≈ 8.48e-8`. Scaling
`dt` this way means a ticker with `sigma = 0.22` really does show about 22%
annualized volatility, and a believable 1 to 2 percent range over a simulated day.

**Correlation.** Draw independent normals `Z`, then multiply by the Cholesky
factor `L` of the correlation matrix `C` (where `L · Lᵀ = C`). The result `L · Z`
has exactly the correlations in `C`.

| Pair | Correlation |
| --- | --- |
| Tech with tech (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX) | 0.6 |
| Finance with finance (JPM, V) | 0.5 |
| TSLA with anything | 0.3 |
| Across sectors, or any ticker not in the table | 0.3 |

The matrix is rebuilt whenever a ticker is added or removed. That is an O(n²)
build plus an O(n³) factorization, which is negligible below a few hundred
tickers. These particular values always give a valid (positive-definite) matrix.

**Events.** After the GBM step, each ticker has a 0.1% chance per tick of a
shock: the price is multiplied by `1 ± U(0.02, 0.05)`. With ten tickers at two
ticks a second, that is one event roughly every 50 seconds.

## Parameters

```python
# backend/app/market/seed_prices.py
"""Starting prices and per-ticker GBM parameters for the simulator."""

SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM": 195.00,
    "V": 280.00,
    "NFLX": 600.00,
}

# sigma: annualized volatility. mu: annualized drift.
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},
    "NVDA": {"sigma": 0.40, "mu": 0.08},
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},
    "V": {"sigma": 0.17, "mu": 0.04},
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}

DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

SECTORS: dict[str, str] = {
    "AAPL": "tech",
    "GOOGL": "tech",
    "MSFT": "tech",
    "AMZN": "tech",
    "META": "tech",
    "NVDA": "tech",
    "NFLX": "tech",
    "TSLA": "tsla",  # trades on its own story
    "JPM": "finance",
    "V": "finance",
}

INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
TSLA_CORR = 0.3
CROSS_SECTOR_CORR = 0.3  # also used for any ticker not listed in SECTORS
```

The seed prices are plausible round numbers, not live quotes. A ticker added at
runtime that is not in the table starts at a random price between $50 and $300
with the default parameters.

## Code

Two classes, split so the math can be tested without an event loop:

- `GBMSimulator` — pure math. Holds prices and parameters; `step()` advances
  everything by one tick. Takes a `seed` for reproducible tests.
- `SimulatorDataSource` — the `MarketDataSource` implementation. Owns the
  background task, calls `step()` every 500 ms, and writes into the `PriceCache`.

```python
# backend/app/market/simulator.py
from __future__ import annotations

import asyncio
import logging
import math
import random

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CROSS_SECTOR_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SECTORS,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)

logger = logging.getLogger(__name__)

# 252 trading days * 6.5 hours * 3600 seconds
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600


class GBMSimulator:
    """Pure math: correlated geometric Brownian motion. No asyncio, no cache."""

    def __init__(
        self,
        tickers: list[str],
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

        for ticker in tickers:
            self._add(ticker)
        self._rebuild_cholesky()

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick and return the new prices."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z  # independent draws -> correlated draws

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z[i]
            price = self._prices[ticker] * math.exp(drift + diffusion)

            # Occasional sudden 2-5% move, for visual drama.
            if self._py_rng.random() < self._event_probability:
                shock = self._py_rng.uniform(0.02, 0.05) * self._py_rng.choice([-1, 1])
                price *= 1 + shock
                logger.debug("Simulated event on %s: %+.1f%%", ticker, shock * 100)

            self._prices[ticker] = price
            result[ticker] = round(price, 2)
        return result

    def add_ticker(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._add(ticker)
        self._rebuild_cholesky()

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

    def _add(self, ticker: str) -> None:
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker, self._py_rng.uniform(50.0, 300.0))
        self._params[ticker] = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)

    def _rebuild_cholesky(self) -> None:
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(a: str, b: str) -> float:
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
        self._event_probability = event_probability
        self._seed = seed
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(
            tickers,
            tick_seconds=self._interval,
            event_probability=self._event_probability,
            seed=self._seed,
        )
        # Seed the cache so the first SSE event and the first trade have prices.
        for ticker in tickers:
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
        if self._sim is None or ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    def _seed_cache(self, ticker: str) -> None:
        price = self._sim.get_price(ticker)
        if price is not None:
            # The starting price doubles as "yesterday's close" for daily change.
            self._cache.update(ticker, price, prev_close=round(price, 2))

    async def _run_loop(self) -> None:
        while True:
            try:
                for ticker, price in self._sim.step().items():
                    self._cache.update(ticker, price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

## Behaviour

- **Startup.** `start()` writes the seed prices into the cache before the loop
  begins, so the first SSE event and an instant trade both have prices. The seed
  price is also stored as `prev_close`, so "daily change" means change since the
  app started.
- **Restart.** Prices are not persisted. Every start begins from the seed
  prices again, so a position bought at $250 may show a jump in P&L after a
  restart. This is acceptable for a simulated-money demo; persisting last prices
  in SQLite is the fix if it becomes annoying.
- **No market hours.** The simulator runs around the clock.
- **Adding a ticker** takes effect on the next tick and the cache is seeded
  immediately, so a newly added ticker is tradeable at once. This differs from
  Massive, where a new ticker waits for the next poll.
- **Any symbol is accepted.** The simulator cannot know whether `ZZZZ` is real,
  and will happily price it. If the watchlist should reject unknown symbols,
  that validation belongs in the API layer.
- **Failure.** An exception in `step()` is logged and the loop carries on.

## What it looks like

Measured over one simulated trading day (46,800 ticks, events off, `seed=42`):

| Check | Target | Measured |
| --- | --- | --- |
| AAPL annualized volatility | 0.22 | 0.220 |
| TSLA annualized volatility | 0.50 | 0.499 |
| JPM annualized volatility | 0.18 | 0.180 |
| AAPL / MSFT correlation | 0.6 | 0.60 |
| JPM / V correlation | 0.5 | 0.50 |
| AAPL / JPM correlation | 0.3 | 0.30 |
| TSLA / AAPL correlation | 0.3 | 0.30 |

Day moves in that run: AAPL −2.4%, TSLA −5.3%, JPM −0.7%.

One consequence worth knowing: the typical AAPL move per tick is about 0.005%,
roughly one cent at $190. Because prices are rounded to cents, a tick often
lands on the same price and reports `flat`. Cheaper or calmer tickers flash less
often than expensive or volatile ones. That is realistic, but if the UI feels
too quiet, the lever is `sigma` in `seed_prices.py`, not the tick rate.

## Tuning

| Want | Change |
| --- | --- |
| Livelier or calmer prices | `sigma` per ticker in `TICKER_PARAMS` |
| More or fewer sudden jumps | `event_probability` (default `0.001`; `0` turns them off) |
| Bigger jumps | the `uniform(0.02, 0.05)` range in `step()` |
| Sectors moving more in lockstep | the correlation constants (keep them below 1) |
| Different update rate | `update_interval`; `dt` scales with it, so volatility stays correct |

## Testing

`GBMSimulator` is deterministic given a `seed`, so tests need no mocks:

- Prices stay positive over many thousands of steps.
- With `event_probability=0`, realized volatility and pairwise correlations
  match the configured values within tolerance (the table above).
- With `event_probability=1`, every tick moves 2 to 5 percent.
- `add_ticker` and `remove_ticker` keep `step()` working, including going down
  to one ticker and to zero.
- An unknown ticker starts between $50 and $300.
- Two simulators with the same seed produce identical paths.

For `SimulatorDataSource`, use a short `update_interval` (for example 0.05 s):
`start()` seeds the cache, the cache `version` advances while running, `stop()`
is safe to call twice, and a removed ticker disappears from the cache.

## Verification status

The code in this document was run as written on 3 October 2026 (Python 3.14,
numpy 2.5). The figures in "What it looks like" come from that run. The test
list above describes tests to write; no test suite exists in the repository yet.
