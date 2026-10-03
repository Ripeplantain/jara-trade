# Market data backend: detailed design

The implementation design for everything in Jara Trade that produces, stores,
and serves stock prices: the unified market data API, the GBM simulator, and
the Massive (formerly Polygon.io) REST client. It is written to be built from
directly. Every module is given in full, and every snippet was run as written
(see [Verification](#verification)).

This document builds on, and where noted supersedes, the three research notes
in this directory:

| Document | What it contributes | Status after this design |
| --- | --- | --- |
| [MASSIVE_API.md](MASSIVE_API.md) | Endpoints, plans, rate limits, response shapes | Still the API reference |
| [market_interface.md](market_interface.md) | `PriceUpdate`, `PriceCache`, `MarketDataSource`, factory, first Massive source, SSE | Superseded by this document where they differ |
| [market_simulator.md](market_simulator.md) | GBM math, parameters, correlations, measured behaviour | Still the reference for the math and tuning; the code here supersedes its code |

## Contents

1. [Goals and constraints](#1-goals-and-constraints)
2. [What changed from the earlier notes](#2-what-changed-from-the-earlier-notes)
3. [Architecture](#3-architecture)
4. [Files and dependencies](#4-files-and-dependencies)
5. [Unified API: models](#5-unified-api-models)
6. [Unified API: price cache](#6-unified-api-price-cache)
7. [Unified API: source interface](#7-unified-api-source-interface)
8. [Configuration and source selection](#8-configuration-and-source-selection)
9. [Simulator](#9-simulator)
10. [Massive client and data source](#10-massive-client-and-data-source)
11. [MarketDataService facade](#11-marketdataservice-facade)
12. [HTTP surface: SSE and REST](#12-http-surface-sse-and-rest)
13. [Wiring into the app](#13-wiring-into-the-app)
14. [How the rest of the backend uses it](#14-how-the-rest-of-the-backend-uses-it)
15. [Frontend consumption](#15-frontend-consumption)
16. [Runtime walkthroughs](#16-runtime-walkthroughs)
17. [Failure handling](#17-failure-handling)
18. [Tests](#18-tests)
19. [Build order](#19-build-order)
20. [Open questions](#20-open-questions)
21. [Verification](#verification)

## 1. Goals and constraints

**Goals**

- One interface for prices. Routes, trade execution, portfolio valuation and
  the AI chat read prices the same way whether they are simulated or real.
- Zero-configuration demo. With no `MASSIVE_API_KEY`, the simulator starts and
  prices move twice a second.
- Real prices with a key, on any Massive plan, including the free one, without
  a "plan" setting.
- Prices appear live in the browser over Server-Sent Events.

**Constraints that shape the design**

- The free Massive plan has no snapshot endpoint and allows 5 calls a minute
  (see MASSIVE_API.md). The client must detect this and fall back to end-of-day
  bars, spending as few calls as possible.
- Paid plans are "unlimited", but the client keeps one request in flight.
- The API key never leaves the server: not in the frontend, an SSE payload, a
  URL, or a log line.
- Single process, single event loop (FastAPI + uvicorn). No Redis, no worker
  processes. Prices live in memory.
- A failed fetch must never take down the app or blank out prices.

**Non-goals**

- The Massive WebSocket feed. REST polling is enough for a 15-minute-delayed or
  end-of-day workstation, and on Advanced a 2-5 s poll is fine.
- Persisting prices across restarts (see [Open questions](#20-open-questions)).
- Historical charts beyond the last hour in memory. Back-filling from the
  Massive custom-bars endpoint is a later feature.

## 2. What changed from the earlier notes

The earlier notes were verified research sketches. Designing the whole
subsystem exposed gaps; these are the changes, each with its reason.

| # | Change | Why |
| --- | --- | --- |
| 1 | New `MarketDataService` facade with `reconcile(wanted)` | Routes need one object to talk to. `reconcile` enforces "never stop tracking a held ticker" in one place instead of in every route |
| 2 | `status()` on every source, `SourceStatus` model, `GET /api/market/status` | The UI needs to say "simulated", "15-min delayed" or "end of day", and ops need to see a rejected key without reading logs |
| 3 | Massive split into `MassiveClient` (HTTP) and `MassiveDataSource` (polling) with typed errors | Parsing and error mapping become pure, testable functions; the poll loop handles exceptions by type instead of by status code |
| 4 | End-of-day bars fetched **once per trading day** and kept in memory | The earlier design re-downloaded the multi-megabyte whole-market file every 15 minutes for data that changes once a day |
| 5 | End-of-day `prev_close` is the **prior session's close** (one extra call per day) | The earlier design used the day's open, so "daily change" was really open-to-close |
| 6 | `add_ticker` is instant in end-of-day mode and wakes the poller in snapshot mode | The earlier design left a new ticker unpriced, and so untradeable, for up to 15 s (snapshot) or 15 min (end of day) |
| 7 | Exponential back-off on consecutive failures | A revoked key otherwise logs an error every 15 s forever; a 429 on the free plan needs a full minute |
| 8 | Cache ignores non-positive or non-finite prices and exact repeats | A stale snapshot after the close no longer wakes every SSE client every 15 s |
| 9 | Cache keeps a sampled history (1 point per 5 s, 1 hour) and `GET /api/market/history/{ticker}` | The chart and sparklines need something to draw on page load; SSE only carries the latest price |
| 10 | Ticker validation (`normalize_ticker`) in the unified API | Unknown symbols are a fact of life (Massive omits them; the simulator prices anything). Malformed ones should be rejected before they reach either |
| 11 | Simulator accepts `add_ticker` before `start`, schedules ticks against the loop clock, stamps each tick with one timestamp | Removes a silent drop, timing drift, and per-ticker timestamp skew |
| 12 | SSE sends an `id:` per event and a keep-alive comment every 15 s | Proxies close idle streams; end-of-day mode can be silent for hours |
| 13 | `PriceUpdate.day_change` (dollars) alongside `day_change_percent` | The watchlist shows both |
| 14 | Environment is read in one module (`config.py`) | Keeps "no other code reads `MASSIVE_API_KEY`" true while allowing a poll-interval override and a demo seed |

Unchanged from the notes: the `PriceUpdate` field set (plus `day_change`), the
one-writer/many-readers cache, the factory rule (blank key means simulator),
snapshot price fallback chain, 403-driven mode detection, the GBM maths and all
simulator parameters.

## 3. Architecture

```
                              ┌──────────────── MarketDataService ────────────────┐
 startup: config.py           │                                                   │
 MASSIVE_API_KEY? ──factory──▶│  source: MarketDataSource      cache: PriceCache  │
                              │  ┌───────────────────────┐     ┌───────────────┐  │
                              │  │ SimulatorDataSource   │     │ latest price  │  │
                              │  │  GBMSimulator, 500 ms │────▶│ per ticker    │  │
                              │  ├─── or ────────────────┤write│ + 1 h history │  │
                              │  │ MassiveDataSource     │────▶│ + version     │  │
                              │  │  MassiveClient (httpx)│     └───────┬───────┘  │
                              │  └───────────▲───────────┘             │ read     │
                              └──────────────┼─────────────────────────┼──────────┘
                     add/remove via reconcile│                         │
            ┌────────────────────────────────┴─┐   ┌───────────────────┼──────────────────────┐
            │ watchlist routes, trade routes   │   │ SSE /api/stream/prices (500 ms, version) │
            │ (after their DB write)           │   │ REST /api/market/*                       │
            └──────────────────────────────────┘   │ trade execution, portfolio, LLM context  │
                                                   └──────────────────────────────────────────┘
```

Rules the design enforces:

- **One writer.** Exactly one source runs, with one background task. It is the
  only code that calls `PriceCache.update`.
- **Readers use the cache only.** Nothing asks a source for a price. More SSE
  clients cost no API calls.
- **Sources push, readers pull.** A source writes when it has data. The SSE loop
  wakes every 500 ms and sends only if the cache `version` moved.
- **Selection happens once**, at startup, in the factory.
- **Tracking changes go through `reconcile`.** The tracked set is always
  "watchlist ∪ open positions".

## 4. Files and dependencies

```
backend/
  pyproject.toml
  app/
    main.py                  # FastAPI app + lifespan (section 13)
    market/
      __init__.py            # public exports
      models.py              # PriceUpdate, PricePoint, SourceStatus, normalize_ticker, errors
      cache.py               # PriceCache
      interface.py           # MarketDataSource (abstract)
      config.py              # MarketSettings: the only reader of the environment
      factory.py             # create_market_data_source()
      seed_prices.py         # simulator seed prices, GBM params, sectors
      simulator.py           # GBMSimulator, SimulatorDataSource
      massive_client.py      # MassiveClient, MassiveDataSource, parsing, errors
      service.py             # MarketDataService facade
      stream.py              # SSE router
      routes.py              # REST router
  tests/
    market/
      test_models.py  test_cache.py  test_factory.py
      test_simulator.py  test_massive.py  test_service_and_stream.py
```

```toml
# backend/pyproject.toml (market data parts)
[project]
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "httpx>=0.27",   # Massive client
    "numpy>=2.0",    # simulator
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Python 3.11 is the floor: the code uses `TimeoutError` as raised by
`asyncio.wait_for` and `zoneinfo`.

## 5. Unified API: models

`PriceUpdate` is the one shape shared by the cache, the SSE stream and every
reader. `previous_price` is the price one update ago and drives the green/red
flash; `prev_close` is the prior session's close and drives the daily change.

```python
# backend/app/market/models.py
"""Data shapes shared by the cache, the data sources, the SSE stream and every reader."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# Upper-case letters, digits and a dot or dash for share classes (BRK.B, BF-B).
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.-][A-Z0-9]{1,2})?$")


class InvalidTickerError(ValueError):
    """The string cannot be a US stock symbol."""


class PriceUnavailableError(LookupError):
    """No price is cached for the ticker yet (new ticker, unknown symbol, or source down)."""

    def __init__(self, ticker: str) -> None:
        super().__init__(f"No price available for {ticker} yet")
        self.ticker = ticker


def normalize_ticker(raw: str) -> str:
    """Trim and upper-case a symbol, rejecting anything that cannot be a ticker."""
    ticker = raw.strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise InvalidTickerError(f"Invalid ticker symbol: {raw!r}")
    return ticker


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """One ticker's latest price, as stored in the cache and sent over SSE."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float  # Unix seconds of the underlying market data
    prev_close: float | None = None  # prior session close, for daily change

    @property
    def direction(self) -> str:
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def day_change(self) -> float | None:
        if not self.prev_close:
            return None
        return round(self.price - self.prev_close, 4)

    @property
    def day_change_percent(self) -> float | None:
        if not self.prev_close:
            return None
        return round((self.price - self.prev_close) / self.prev_close * 100, 4)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "direction": self.direction,
            "change": self.change,
            "prev_close": self.prev_close,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One sample in a ticker's short in-memory history (for sparklines and charts)."""

    timestamp: float
    price: float


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """Health of the running data source, for /api/market/status and the UI badge."""

    source: str  # "simulator" | "massive"
    mode: str  # "simulated" | "snapshot" | "eod"
    running: bool
    tickers: int
    last_success: float | None = None  # Unix seconds of the last good fetch or tick
    last_error: str | None = None
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
```

Example payload (`PriceUpdate.to_dict()`), as it appears in SSE and REST:

```json
{
  "ticker": "NVDA",
  "price": 800.04,
  "previous_price": 800.05,
  "timestamp": 1791039119.97,
  "direction": "down",
  "change": -0.01,
  "prev_close": 800.0,
  "day_change": 0.04,
  "day_change_percent": 0.005
}
```

`normalize_ticker` examples:

| Input | Result |
| --- | --- |
| `" aapl "` | `"AAPL"` |
| `"brk.b"` | `"BRK.B"` |
| `"BF-B"` | `"BF-B"` |
| `""`, `"1ABC"`, `"AAPL;DROP"`, `"TOOLONGTICKER"` | `InvalidTickerError` |

The regex checks *shape* only. Whether a well-formed symbol actually trades is
answered by the source: Massive never returns a price for it, so it stays
unpriced; the simulator prices anything (see [Open questions](#20-open-questions)).

## 6. Unified API: price cache

```python
# backend/app/market/cache.py
"""In-memory latest price per ticker, plus a short sampled history."""
from __future__ import annotations

import math
import time
from collections import deque
from threading import Lock

from .models import PricePoint, PriceUpdate


class PriceCache:
    """One writer (the data source), many readers (SSE, trades, portfolio, chat)."""

    def __init__(self, history_spacing: float = 5.0, history_length: int = 720) -> None:
        # Defaults keep one point per 5 s for an hour per ticker.
        self._prices: dict[str, PriceUpdate] = {}
        self._history: dict[str, deque[PricePoint]] = {}
        self._history_spacing = history_spacing
        self._history_length = history_length
        self._lock = Lock()
        self._version = 0

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        prev_close: float | None = None,
    ) -> PriceUpdate | None:
        """Store a new price. Returns the stored update, or None if it was ignored.

        Ignored: non-finite or non-positive prices, and exact repeats (same price,
        timestamp and prev_close), so a source re-reporting stale data after the
        close does not wake every SSE client.
        """
        if not math.isfinite(price) or price <= 0:
            return None
        price = round(price, 2)
        prev_close = round(prev_close, 2) if prev_close else None
        ts = timestamp if timestamp is not None else time.time()
        with self._lock:
            existing = self._prices.get(ticker)
            if prev_close is None and existing is not None:
                prev_close = existing.prev_close
            if (
                existing is not None
                and existing.price == price
                and existing.timestamp == ts
                and existing.prev_close == prev_close
            ):
                return existing
            update = PriceUpdate(
                ticker=ticker,
                price=price,
                # First sighting: previous == current, so direction is "flat".
                previous_price=existing.price if existing else price,
                timestamp=ts,
                prev_close=prev_close,
            )
            self._prices[ticker] = update
            self._record_history(ticker, ts, price)
            self._version += 1
            return update

    def _record_history(self, ticker: str, ts: float, price: float) -> None:
        points = self._history.get(ticker)
        if points is None:
            points = self._history[ticker] = deque(maxlen=self._history_length)
        if points and ts - points[-1].timestamp < self._history_spacing:
            # Within the spacing window: keep the latest price in the last slot.
            points[-1] = PricePoint(points[-1].timestamp, price)
        else:
            points.append(PricePoint(ts, price))

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)

    def history(self, ticker: str) -> list[PricePoint]:
        with self._lock:
            return list(self._history.get(ticker, ()))

    def remove(self, ticker: str) -> None:
        with self._lock:
            self._history.pop(ticker, None)
            if self._prices.pop(ticker, None) is not None:
                self._version += 1

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    @property
    def version(self) -> int:
        """Bumps on every stored change; SSE uses it to skip sending unchanged data."""
        return self._version
```

Behaviour:

| Call | Effect |
| --- | --- |
| `update("AAPL", 190.0)` (first time) | Stored, `previous_price == price`, direction `flat`, `version += 1` |
| `update("AAPL", 190.5)` | `previous_price = 190.0`, direction `up` |
| `update("AAPL", 191.0)` with no `prev_close` | Keeps the earlier `prev_close` |
| Same price, timestamp and `prev_close` again | Returns the existing update; `version` unchanged |
| `update("AAPL", 0)` / `nan` / `-1` | Ignored, returns `None` |
| `remove("AAPL")` | Drops price and history, `version += 1` |

History sampling: an update less than `history_spacing` seconds after the last
stored point overwrites that point's price; otherwise it appends. So the last
point is always the current price, the series has at most one point per 5 s,
and `maxlen=720` caps it at one hour (about 55 KB per ticker, measured). Over
50 tickers that is under 3 MB.

Threading: everything runs on one event loop, so the lock is uncontended. It is
there so a source that ever works from a thread (for example the official
synchronous Massive client under `asyncio.to_thread`) stays safe. `version` is
read without the lock; a stale read only delays one SSE send by 500 ms.

## 7. Unified API: source interface

```python
# backend/app/market/interface.py
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
```

The contract both implementations honour:

- `start()` fills the cache before it returns where it can (simulator: always;
  Massive: if the first poll succeeds), so the first SSE event and an immediate
  trade have prices.
- `start()` twice raises; `stop()` twice is a no-op.
- `add_ticker()` and `remove_ticker()` are safe at any time, including before
  `start()`.
- The background task never dies on a failed fetch. The cache keeps the last
  good prices; `status()` reports the error.
- Tickers arrive normalized. Normalization is the service's job, not the source's.

## 8. Configuration and source selection

```python
# backend/app/market/config.py
"""Market data settings. The only module that reads the environment."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketSettings:
    massive_api_key: str | None = None
    massive_poll_interval: float = 15.0  # seconds, snapshot mode (paid plans)
    massive_eod_poll_interval: float = 900.0  # seconds, end-of-day mode (free plan)
    simulator_interval: float = 0.5  # seconds per tick
    simulator_seed: int | None = None  # fixed seed for reproducible demos and tests

    @property
    def use_massive(self) -> bool:
        return bool(self.massive_api_key)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MarketSettings:
        env = os.environ if env is None else env
        key = env.get("MASSIVE_API_KEY", "").strip() or None
        seed = env.get("SIMULATOR_SEED", "").strip()
        return cls(
            massive_api_key=key,
            massive_poll_interval=float(env.get("MASSIVE_POLL_INTERVAL", "15") or 15),
            simulator_seed=int(seed) if seed else None,
        )
```

| Variable | Default | Effect |
| --- | --- | --- |
| `MASSIVE_API_KEY` | unset | Set and non-blank selects Massive; otherwise the simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between snapshot polls. Use `2`-`5` on the Advanced (real-time) plan |
| `SIMULATOR_SEED` | unset | Fixed seed: the same price path every run (demos, screenshots, E2E tests) |

```bash
# .env examples
MASSIVE_API_KEY=                 # blank: simulator
MASSIVE_API_KEY=abc123           # Massive; plan detected automatically
MASSIVE_API_KEY=abc123
MASSIVE_POLL_INTERVAL=3          # Advanced plan
SIMULATOR_SEED=42                # reproducible simulator
```

```python
# backend/app/market/factory.py
"""Chooses the data source once, at startup."""
from __future__ import annotations

import logging

from .cache import PriceCache
from .config import MarketSettings
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(
    price_cache: PriceCache, settings: MarketSettings | None = None
) -> MarketDataSource:
    """Massive if MASSIVE_API_KEY is set and non-blank, otherwise the simulator."""
    settings = settings or MarketSettings.from_env()

    if settings.use_massive:
        # Deferred import: a simulator-only run never loads httpx, and vice versa numpy.
        from .massive_client import MassiveClient, MassiveDataSource

        logger.info("Market data source: Massive REST API")
        return MassiveDataSource(
            price_cache,
            MassiveClient(settings.massive_api_key),
            poll_interval=settings.massive_poll_interval,
            eod_poll_interval=settings.massive_eod_poll_interval,
        )

    from .simulator import SimulatorDataSource

    logger.info("Market data source: simulator")
    return SimulatorDataSource(
        price_cache,
        update_interval=settings.simulator_interval,
        seed=settings.simulator_seed,
    )
```

There is deliberately no `MASSIVE_PLAN` setting: the source finds out from the
first snapshot call (section 10).

## 9. Simulator

The maths, parameters and measured behaviour are specified in
[market_simulator.md](market_simulator.md) and are not repeated here. In one
line: every 500 ms each price is multiplied by
`exp((mu - sigma²/2)·dt + sigma·sqrt(dt)·Z)`, with `Z` correlated across
tickers through the Cholesky factor of a sector correlation matrix (tech 0.6,
finance 0.5, everything else 0.3), plus a 0.1% per-tick chance of a 2-5% jump.

### 9.1 Parameters

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

DEFAULT_WATCHLIST: list[str] = list(SEED_PRICES)

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

# Random starting range for a ticker added at runtime that is not in SEED_PRICES.
UNKNOWN_PRICE_RANGE = (50.0, 300.0)
```

`DEFAULT_WATCHLIST` is new: it is what a fresh database seeds the watchlist
with, and what `main.py` tracks until the database exists.

### 9.2 Code

```python
# backend/app/market/simulator.py
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
```

### 9.3 Design notes

- **Two classes.** `GBMSimulator` is pure maths with a `seed`, so its tests need
  no event loop and no mocks. `SimulatorDataSource` owns the timer and the cache.
- **The simulator is created in `__init__`**, not in `start()`. That is what
  makes `add_ticker()` before `start()` work instead of silently dropping the
  ticker.
- **`step()` returns unrounded prices.** The simulator keeps full precision
  internally and the cache rounds to cents, so rounding never biases the path.
- **One timestamp per tick.** All tickers in a tick share `time.time()`, so the
  frontend can treat a tick as one moment.
- **Drift-free timing.** Ticks are scheduled on `loop.time()` targets rather
  than `sleep(interval)` after the work, so ten minutes of running is 1,200
  ticks, not slightly fewer. After a long stall (a laptop sleeping) it skips
  ahead instead of firing a burst of catch-up ticks.
- **`prev_close` is the seed price**, so "daily change" in simulator mode means
  "since the app started". The UI badge (section 15) makes that clear.

### 9.4 Example

```python
from app.market.simulator import GBMSimulator

sim = GBMSimulator(["AAPL", "MSFT", "TSLA"], seed=42)
for _ in range(3):
    print({t: round(p, 2) for t, p in sim.step().items()})

sim.add_ticker("PYPL")          # not in SEED_PRICES: starts between $50 and $300
sim.remove_ticker("TSLA")
print(sim.get_tickers())        # ['AAPL', 'MSFT', 'PYPL']
```

## 10. Massive client and data source

The endpoints, plans and response shapes are in [MASSIVE_API.md](MASSIVE_API.md).
This module uses two of them:

| Mode | Endpoint | Plans | When |
| --- | --- | --- | --- |
| `snapshot` | `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=…` | Starter and up | Default |
| `eod` | `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` | All, including free | After the snapshot endpoint answers 403 |

### 10.1 Code

```python
# backend/app/market/massive_client.py
"""Real prices from the Massive (formerly Polygon.io) REST API.

Two layers:

- MassiveClient: a thin async HTTP wrapper. Knows endpoints, auth, response
  shapes and error codes. No state, no timers.
- MassiveDataSource: the MarketDataSource. Owns the polling loop, picks
  snapshot or end-of-day mode, and writes into the PriceCache.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .cache import PriceCache
from .interface import MarketDataSource
from .models import SourceStatus

logger = logging.getLogger(__name__)

BASE_URL = "https://api.massive.com"
SNAPSHOT_PATH = "/v2/snapshot/locale/us/markets/stocks/tickers"
GROUPED_DAILY_PATH = "/v2/aggs/grouped/locale/us/market/stocks/{day}"
EASTERN = ZoneInfo("America/New_York")

SNAPSHOT_BATCH = 250  # tickers per snapshot call; keeps the URL a sane length
EOD_CALL_BUDGET = 4  # grouped-daily calls per refresh; the free plan allows 5 a minute


# --------------------------------------------------------------------------- errors


class MassiveAPIError(Exception):
    """Any failed Massive request. `status` is None for network failures."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"Massive HTTP {status}: {message}" if status else message)
        self.status = status


class MassiveAuthError(MassiveAPIError):
    """401: missing or unknown API key."""


class MassivePlanError(MassiveAPIError):
    """403: the plan does not include this endpoint (snapshot on the free plan)."""


class MassiveRateLimitError(MassiveAPIError):
    """429: too many calls (5 per minute on the free plan)."""


_ERRORS_BY_STATUS: dict[int, type[MassiveAPIError]] = {
    401: MassiveAuthError,
    403: MassivePlanError,
    429: MassiveRateLimitError,
}


# --------------------------------------------------------------------------- parsed shapes


@dataclass(frozen=True, slots=True)
class Quote:
    """Latest price for one ticker from the snapshot endpoint."""

    ticker: str
    price: float
    timestamp: float | None  # Unix seconds
    prev_close: float | None


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One ticker's bar from the grouped-daily endpoint."""

    ticker: str
    open: float
    close: float
    timestamp: float  # Unix seconds


def parse_snapshot_entry(snap: dict) -> Quote | None:
    """Best available price from one snapshot entry.

    `lastTrade` only exists on plans that include trades (Developer and up), and
    `min`/`day` hold zeros between the 3:30 AM ET reset and the first trade, so
    walk the chain lastTrade.p -> min.c -> day.c -> prevDay.c, treating 0 as missing.
    Timestamps: lastTrade.t and updated are nanoseconds; min.t is milliseconds.
    """
    ticker = snap.get("ticker")
    if not ticker:
        return None
    prev_close = (snap.get("prevDay") or {}).get("c") or None
    updated = snap["updated"] / 1e9 if snap.get("updated") else None

    last_trade = snap.get("lastTrade") or {}
    if last_trade.get("p"):
        ts = last_trade["t"] / 1e9 if last_trade.get("t") else updated
        return Quote(ticker, float(last_trade["p"]), ts, prev_close)
    minute = snap.get("min") or {}
    if minute.get("c"):
        ts = minute["t"] / 1e3 if minute.get("t") else updated
        return Quote(ticker, float(minute["c"]), ts, prev_close)
    for key in ("day", "prevDay"):
        close = (snap.get(key) or {}).get("c")
        if close:
            return Quote(ticker, float(close), updated, prev_close)
    return None


def parse_snapshot(payload: dict) -> list[Quote]:
    quotes = (parse_snapshot_entry(s) for s in payload.get("tickers") or [])
    return [q for q in quotes if q is not None]


def parse_grouped_daily(payload: dict) -> dict[str, DailyBar]:
    """Whole-market bars keyed by ticker. Empty on weekends and holidays (no `results`)."""
    bars: dict[str, DailyBar] = {}
    for bar in payload.get("results") or []:
        if bar.get("T") and bar.get("c"):
            bars[bar["T"]] = DailyBar(
                ticker=bar["T"],
                open=float(bar.get("o") or bar["c"]),
                close=float(bar["c"]),
                timestamp=bar.get("t", 0) / 1e3,
            )
    return bars


# --------------------------------------------------------------------------- HTTP client


class MassiveClient:
    """Async wrapper over the two endpoints Jara Trade needs.

    The key goes in the Authorization header, never the URL, so it cannot leak
    into logs or exception messages. Does not retry: the caller's poll loop is
    the retry policy.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,  # tests pass httpx.MockTransport
        )

    async def snapshot(self, tickers: Iterable[str]) -> list[Quote]:
        """Latest price per ticker in one call. Starter plan and up; 403 on the free plan.

        Unknown tickers are silently left out of the response.
        """
        payload = await self._get(
            SNAPSHOT_PATH, params={"tickers": ",".join(t.upper() for t in tickers)}
        )
        return parse_snapshot(payload)

    async def grouped_daily(self, day: date) -> dict[str, DailyBar]:
        """Every US stock's bar for one trading day. All plans, including free."""
        payload = await self._get(
            GROUPED_DAILY_PATH.format(day=day.isoformat()), params={"adjusted": "true"}
        )
        return parse_grouped_daily(payload)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            resp = await self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise MassiveAPIError(None, f"network error: {type(exc).__name__}: {exc}") from exc
        if resp.status_code == 200:
            return resp.json()
        try:
            body = resp.json()
            message = body.get("error") or body.get("message") or body.get("status") or ""
        except ValueError:
            message = resp.text[:200]
        error_cls = _ERRORS_BY_STATUS.get(resp.status_code, MassiveAPIError)
        raise error_cls(resp.status_code, message)


# --------------------------------------------------------------------------- data source


def previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _eastern_today() -> date:
    return datetime.now(EASTERN).date()


class MassiveDataSource(MarketDataSource):
    """Polls Massive for the union of tracked tickers.

    Two modes, chosen automatically:

    - "snapshot": one call per poll prices every tracked ticker. Starter plan
      and up. Default cadence 15 s.
    - "eod": the snapshot endpoint answered 403 (free Basic plan). Prices come
      from the grouped-daily bars of the last completed trading day. Those bars
      are fetched once per trading day and kept in memory, so later polls and
      newly added tickers cost no API calls.
    """

    def __init__(
        self,
        price_cache: PriceCache,
        client: MassiveClient,
        poll_interval: float = 15.0,
        eod_poll_interval: float = 900.0,
        max_backoff: float = 300.0,
        today: Callable[[], date] = _eastern_today,
    ) -> None:
        self._cache = price_cache
        self._client = client
        self._poll_interval = poll_interval
        self._eod_poll_interval = eod_poll_interval
        self._max_backoff = max_backoff
        self._today = today

        self._tickers: set[str] = set()
        self._mode = "snapshot"
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

        # Health
        self._last_success: float | None = None
        self._last_error: str | None = None
        self._failures = 0

        # End-of-day mode memory
        self._eod_target: date | None = None  # the date the current bars were resolved for
        self._eod_day: date | None = None  # the trading day the bars belong to
        self._eod_bars: dict[str, DailyBar] = {}
        self._eod_prev_close: dict[str, float] = {}

    @property
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------ interface

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None:
            raise RuntimeError("MassiveDataSource already started")
        self._tickers.update(tickers)
        await self._poll_once()  # fill the cache before the first SSE client connects
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await self._client.aclose()

    async def add_ticker(self, ticker: str) -> None:
        if ticker in self._tickers:
            return
        self._tickers.add(ticker)
        if self._mode == "eod":
            self._apply_eod([ticker])  # served from bars already in memory: no API call
        elif self._task is not None:
            self._wake.set()  # poll now rather than in up to poll_interval seconds

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return sorted(self._tickers)

    def status(self) -> SourceStatus:
        return SourceStatus(
            source="massive",
            mode=self._mode,
            running=self._task is not None and not self._task.done(),
            tickers=len(self._tickers),
            last_success=self._last_success,
            last_error=self._last_error,
            consecutive_failures=self._failures,
        )

    # ------------------------------------------------------------------ polling

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._next_delay())
            except TimeoutError:
                pass
            self._wake.clear()
            await self._poll_once()

    def _next_delay(self) -> float:
        if self._mode == "snapshot":
            if self._failures == 0:
                return self._poll_interval
            return min(self._poll_interval * 2**self._failures, self._max_backoff)
        if self._failures == 0:
            return self._eod_poll_interval
        # A failed daily fetch is retried well before the next 15-minute slot.
        return min(60.0 * 2 ** (self._failures - 1), self._eod_poll_interval)

    async def _poll_once(self) -> None:
        """Never raises: a failed poll leaves the last good prices in the cache."""
        if not self._tickers:
            return
        try:
            if self._mode == "snapshot":
                await self._poll_snapshot()
            else:
                await self._poll_eod()
        except MassivePlanError as exc:
            if self._mode == "snapshot":
                logger.warning("Massive plan has no snapshot access; switching to end-of-day prices")
                self._mode = "eod"
                await self._poll_once()
                return
            self._record_failure(exc)
        except MassiveAuthError as exc:
            logger.error("Massive rejected the API key (401); check MASSIVE_API_KEY")
            self._record_failure(exc)
        except MassiveRateLimitError as exc:
            logger.warning("Massive rate limit hit (429); backing off")
            self._record_failure(exc)
        except MassiveAPIError as exc:
            logger.error("Massive poll failed: %s", exc)
            self._record_failure(exc)
        except Exception as exc:  # malformed JSON, a bug: keep the loop alive
            logger.exception("Unexpected error polling Massive")
            self._record_failure(exc)
        else:
            self._last_success = time.time()
            self._last_error = None
            self._failures = 0

    def _record_failure(self, exc: Exception) -> None:
        self._failures += 1
        self._last_error = str(exc)

    async def _poll_snapshot(self) -> None:
        tickers = sorted(self._tickers)
        for i in range(0, len(tickers), SNAPSHOT_BATCH):
            for quote in await self._client.snapshot(tickers[i : i + SNAPSHOT_BATCH]):
                # Re-check membership: a ticker may have been removed during the await.
                if quote.ticker in self._tickers:
                    self._cache.update(
                        quote.ticker,
                        quote.price,
                        timestamp=quote.timestamp,
                        prev_close=quote.prev_close,
                    )

    async def _poll_eod(self) -> None:
        target = previous_weekday(self._today())
        if target != self._eod_target:
            await self._refresh_eod(target)
        self._apply_eod(self._tickers)

    async def _refresh_eod(self, target: date) -> None:
        """Fetch the last trading day on or before `target`, and the one before it.

        Normally two calls per day. Each weekday that turns out to be a holiday
        costs one more, capped at EOD_CALL_BUDGET per refresh.
        """
        calls = 0
        day = target
        while True:
            bars = await self._client.grouped_daily(day)
            calls += 1
            if bars:
                break
            if calls >= EOD_CALL_BUDGET:
                raise MassiveAPIError(None, f"no trading day found back from {target}")
            day = previous_weekday(day)
        self._eod_day, self._eod_bars = day, bars

        # The prior session's closes give a true daily change. If that fetch
        # fails, fall back to the day's open and leave the target unresolved so
        # the next poll tries both again.
        prev_closes: dict[str, float] = {}
        prev_day = previous_weekday(day)
        try:
            while calls < EOD_CALL_BUDGET:
                prev_bars = await self._client.grouped_daily(prev_day)
                calls += 1
                if prev_bars:
                    prev_closes = {t: b.close for t, b in prev_bars.items()}
                    break
                prev_day = previous_weekday(prev_day)
        except MassiveAPIError as exc:
            logger.warning("Could not fetch prior closes (%s); using opens for daily change", exc)
        self._eod_prev_close = prev_closes
        self._eod_target = target if prev_closes else None
        logger.info("Massive end-of-day prices for %s (%d calls)", day, calls)

    def _apply_eod(self, tickers: Iterable[str]) -> None:
        for ticker in list(tickers):
            bar = self._eod_bars.get(ticker)
            if bar is None:
                continue  # unknown symbol, or not traded that day
            prev_close = self._eod_prev_close.get(ticker) or bar.open
            self._cache.update(ticker, bar.close, timestamp=bar.timestamp, prev_close=prev_close)
```

### 10.2 Mode detection

```
start()
  └─ poll: GET snapshot ──200──▶ mode stays "snapshot", poll every 15 s
                        ──403──▶ mode = "eod" (permanent for this process)
                                  └─ poll again immediately, end-of-day path
```

A 403 on the snapshot endpoint only ever means "your plan does not include
this", so switching is safe and the switch is one-way. Restart the app after
upgrading the plan.

### 10.3 End-of-day mode in detail

The free plan gives end-of-day data and 5 calls a minute. One grouped-daily
call returns every US stock for one date, so the whole watchlist costs one call
however long it is.

1. **Target date.** The previous weekday in New York time. The free plan never
   asks for today.
2. **Walk back over holidays.** A holiday returns `resultsCount: 0`. Step back a
   weekday and try again.
3. **Fetch the session before that** for true prior closes.
4. **Keep both in memory** (`_eod_bars`, `_eod_prev_close`) and record the target
   date. Later polls the same day make **no API calls**; they only re-apply the
   bars, which the cache de-duplicates.
5. **When the New York date rolls to a new weekday**, the target changes and the
   next poll fetches again.

Call budget per refresh, capped at `EOD_CALL_BUDGET = 4` (under the 5/minute
limit even with the snapshot call that triggered the switch):

| Day being priced | Calls |
| --- | --- |
| Ordinary day, ordinary day before it | 2 |
| Monday after a Friday holiday (e.g. Good Friday) | 3 |
| Tuesday after Labor Day: Monday empty, then Friday, Thursday | 3 |
| Budget exhausted before prior closes are found | prev_close falls back to the day's open; retried next poll |

Example for Tuesday 8 September 2026 (Monday 7 September is Labor Day), taken
from the test suite:

```
GET /v2/aggs/grouped/.../2026-09-07   → resultsCount 0      (holiday)
GET /v2/aggs/grouped/.../2026-09-04   → AAPL o=100 c=101    (prices)
GET /v2/aggs/grouped/.../2026-09-03   → AAPL c=100          (prior closes)
cache: AAPL price 101.00, prev_close 100.00, day_change_percent 1.0
```

Adding a ticker in this mode reads it from `_eod_bars`: priced instantly, zero
calls. A symbol absent from the bars (unknown, or not traded that day) simply
stays unpriced.

### 10.4 Snapshot mode in detail

- One call per poll for up to 250 tickers (`SNAPSHOT_BATCH`); a larger set is
  split into sequential calls, so one request is in flight at a time.
- Price per ticker follows the fallback chain `lastTrade.p → min.c → day.c →
  prevDay.c`, treating `0` as missing (Starter has no `lastTrade`; between the
  3:30 AM ET reset and the first trade `min` and `day` are zero).
- `prev_close` comes from `prevDay.c`.
- Timestamps: `lastTrade.t` and `updated` are nanoseconds, `min.t` milliseconds.
  All are converted to Unix seconds.
- A ticker removed while a request is in flight is re-checked on return and not
  written back into the cache.
- `add_ticker` sets an `asyncio.Event` that the loop waits on, so the new
  ticker is fetched straight away. Several adds in a burst coalesce into one
  extra poll, and polls still never overlap because only the loop polls.
- After the close the snapshot keeps returning the same values with the same
  timestamps; the cache's repeat check means SSE goes quiet rather than
  re-sending identical data.

### 10.5 Back-off

`_next_delay()` after `n` consecutive failures:

| Mode | n = 0 | n = 1 | n = 2 | n = 3 | n = 4 | n ≥ 5 |
| --- | --- | --- | --- | --- | --- | --- |
| `snapshot` (15 s) | 15 | 30 | 60 | 120 | 240 | 300 (cap) |
| `eod` (900 s) | 900 | 60 | 120 | 240 | 480 | 900 (cap) |

In end-of-day mode a failure retries *sooner* than the normal cadence, because
the next good poll might be fifteen minutes away and a 429 on the free plan
clears within a minute. Any success resets `n` to 0.

### 10.6 Example: using the client on its own

```python
import asyncio
import os
from datetime import date

from app.market.massive_client import MassiveClient, MassivePlanError


async def main() -> None:
    client = MassiveClient(os.environ["MASSIVE_API_KEY"])
    try:
        try:
            for q in await client.snapshot(["AAPL", "MSFT", "NOTREAL"]):
                print(q.ticker, q.price, q.prev_close)   # NOTREAL is simply absent
        except MassivePlanError:
            bars = await client.grouped_daily(date(2026, 10, 2))
            print(bars["AAPL"].close, len(bars), "tickers in one call")
    finally:
        await client.aclose()


asyncio.run(main())
```

## 11. MarketDataService facade

```python
# backend/app/market/service.py
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
```

Reads go to the cache; tracking changes go to the source. A route never holds
a `MarketDataSource`.

```python
market = MarketDataService.from_settings()          # cache + factory-chosen source
await market.start(["aapl", "MSFT"])                # normalized to AAPL, MSFT

market.get_price("aapl")                             # 190.0 or None
market.require_price("AAPL")                         # PriceUpdate, or PriceUnavailableError
market.get_prices()                                  # {"AAPL": PriceUpdate, ...}
market.get_prices(["AAPL", "TSLA"])                  # only those with a price
market.history("AAPL")                               # [PricePoint(ts, price), ...]
market.status()                                      # SourceStatus(source="simulator", ...)

added, removed = await market.reconcile(["AAPL", "NVDA"])
# (['NVDA'], ['MSFT'])
```

## 12. HTTP surface: SSE and REST

### 12.1 SSE: `GET /api/stream/prices`

```python
# backend/app/market/stream.py
"""Server-Sent Events: GET /api/stream/prices."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache


def create_stream_router(price_cache: PriceCache, interval: float = 0.5) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            price_events(price_cache, request.is_disconnected, interval=interval),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


async def price_events(
    price_cache: PriceCache,
    is_disconnected: Callable[[], Awaitable[bool]],
    interval: float = 0.5,
    heartbeat: float = 15.0,
) -> AsyncIterator[str]:
    """Yield one `data:` event per cache version change, checked every `interval` s.

    Every event carries the full set of tracked tickers, so a client that
    reconnects is up to date after one message. A comment line goes out every
    `heartbeat` seconds of silence so proxies do not close an idle stream
    (Massive end-of-day mode can be quiet for hours).
    """
    yield "retry: 1000\n\n"  # browser reconnects after 1 s if the stream drops
    loop = asyncio.get_running_loop()
    last_version = -1
    last_sent = loop.time()
    while not await is_disconnected():
        version = price_cache.version
        if version != last_version:
            last_version = version
            payload = {t: u.to_dict() for t, u in price_cache.get_all().items()}
            yield f"id: {version}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
            last_sent = loop.time()
        elif loop.time() - last_sent >= heartbeat:
            yield ": keep-alive\n\n"
            last_sent = loop.time()
        await asyncio.sleep(interval)
```

Wire format (from a real run under uvicorn):

```
retry: 1000

id: 10
data: {"AAPL":{"ticker":"AAPL","price":190.0,"previous_price":190.0,"timestamp":1791039125.21,"direction":"flat","change":0.0,"prev_close":190.0,"day_change":0.0,"day_change_percent":0.0},"GOOGL":{...},...}

id: 20
data: {"AAPL":{"ticker":"AAPL","price":190.01,"previous_price":190.0,...,"direction":"up","change":0.01,...},...}

: keep-alive
```

- Each event is a full snapshot of all tracked tickers keyed by symbol. With
  10-50 tickers that is 2-10 KB per event, 4-20 KB/s per client in simulator
  mode, which is fine for a single-user app. If it ever matters, send only the
  tickers whose update changed since `last_version` (the cache would need to
  record per-ticker versions).
- `id:` is the cache version. It is informational; the server does not replay
  missed events because the next event is a full snapshot anyway.
- A removed ticker disappears from the next event; the frontend should treat
  each event as the whole set, not merge into the previous one.
- `price_events` takes an `is_disconnected` callable rather than the request so
  tests can drive it directly.

### 12.2 REST: `/api/market/*`

```python
# backend/app/market/routes.py
"""REST endpoints for market data: snapshot, one ticker, history, source status."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .models import InvalidTickerError, PriceUnavailableError
from .service import MarketDataService


def create_market_router(market: MarketDataService) -> APIRouter:
    router = APIRouter(prefix="/api/market", tags=["market"])

    @router.get("/prices")
    async def prices(tickers: str | None = Query(None, description="Comma-separated")) -> dict:
        try:
            wanted = tickers.split(",") if tickers else None
            return {t: u.to_dict() for t, u in market.get_prices(wanted).items()}
        except InvalidTickerError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/prices/{ticker}")
    async def price(ticker: str) -> dict:
        try:
            return market.require_price(ticker).to_dict()
        except InvalidTickerError as exc:
            raise HTTPException(422, str(exc)) from exc
        except PriceUnavailableError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/history/{ticker}")
    async def history(ticker: str) -> dict:
        try:
            points = market.history(ticker)
        except InvalidTickerError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "ticker": ticker.upper(),
            "points": [{"timestamp": p.timestamp, "price": p.price} for p in points],
        }

    @router.get("/status")
    async def status() -> dict:
        return market.status().to_dict()

    return router
```

| Request | Response |
| --- | --- |
| `GET /api/market/prices` | `{"AAPL": {PriceUpdate}, ...}` for every tracked ticker |
| `GET /api/market/prices?tickers=AAPL,MSFT` | Only those with a price |
| `GET /api/market/prices/aapl` | `{PriceUpdate}`; `404` if no price yet; `422` if malformed |
| `GET /api/market/history/AAPL` | `{"ticker": "AAPL", "points": [{"timestamp": ..., "price": ...}, ...]}` |
| `GET /api/market/status` | `{SourceStatus}` (below) |

```json
{
  "source": "massive",
  "mode": "eod",
  "running": true,
  "tickers": 10,
  "last_success": 1791039119.97,
  "last_error": null,
  "consecutive_failures": 0
}
```

`last_error` holds messages such as `Massive HTTP 401: Unknown API Key`. They
never contain the key, because the key travels in a header and httpx does not
put headers in exception text.

## 13. Wiring into the app

```python
# backend/app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.market import MarketDataService, create_market_router, create_stream_router
from app.market.seed_prices import DEFAULT_WATCHLIST

market = MarketDataService.from_settings()


def load_tracked_tickers() -> list[str]:
    """Placeholder: SELECT ticker FROM watchlist UNION SELECT ticker FROM positions."""
    return DEFAULT_WATCHLIST


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.market = market
    await market.start(load_tracked_tickers())
    try:
        yield
    finally:
        await market.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(market.cache))
app.include_router(create_market_router(market))
```

`load_tracked_tickers()` becomes a database query once the schema exists:

```python
def load_tracked_tickers(db) -> list[str]:
    rows = db.execute(
        "SELECT ticker FROM watchlist UNION SELECT ticker FROM positions WHERE quantity > 0"
    )
    return [r[0] for r in rows]
```

Run it with `uvicorn app.main:app --reload` from `backend/`. With no key set,
`GET /api/market/status` reports the simulator and `curl -N
localhost:8000/api/stream/prices` shows events twice a second.

## 14. How the rest of the backend uses it

Each route gets the service from `request.app.state.market`. A small dependency
keeps that tidy:

```python
# backend/app/deps.py
from fastapi import Request

from app.market import MarketDataService


def get_market(request: Request) -> MarketDataService:
    return request.app.state.market
```

### 14.1 Trade execution

Fill at the cached price; refuse rather than guess when there is none.

```python
from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_market
from app.market import (
    InvalidTickerError,
    MarketDataService,
    PriceUnavailableError,
    normalize_ticker,
)

router = APIRouter(prefix="/api/trades")


@router.post("")
async def place_trade(order: TradeOrder, market: MarketDataService = Depends(get_market)):
    try:
        quote = market.require_price(order.ticker)
    except InvalidTickerError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PriceUnavailableError as exc:
        raise HTTPException(409, f"{exc}. Add it to the watchlist and try again shortly.") from exc

    fill_price = quote.price
    # ... check cash / shares, write the trade and position in one DB transaction ...
    # A buy of a ticker not on the watchlist makes it a held position, so track it:
    await market.reconcile(watchlist_tickers(db) | held_tickers(db))
    return {"ticker": quote.ticker, "price": fill_price, "as_of": quote.timestamp}
```

A buy needs a price, and a price needs the ticker to be tracked, so the
frontend should only offer trading for tickers already in the watchlist or
held. That is the natural UI anyway.

### 14.2 Watchlist add and remove

Write the database first, then reconcile. Removing a held ticker from the
watchlist keeps its price because it is still in the positions half of the union.

```python
@router.post("/api/watchlist")
async def add_to_watchlist(body: WatchlistAdd, market: MarketDataService = Depends(get_market)):
    try:
        ticker = normalize_ticker(body.ticker)
    except InvalidTickerError as exc:
        raise HTTPException(422, str(exc)) from exc
    insert_watchlist(db, ticker)
    await market.reconcile(watchlist_tickers(db) | held_tickers(db))
    return {"ticker": ticker, "price": market.get_price(ticker)}  # may be None for a moment on Massive


@router.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, market: MarketDataService = Depends(get_market)):
    delete_watchlist(db, normalize_ticker(ticker))
    await market.reconcile(watchlist_tickers(db) | held_tickers(db))
```

Selling a whole position runs the same `reconcile` after its transaction.

### 14.3 Portfolio valuation

```python
def value_portfolio(positions: list[Position], market: MarketDataService) -> dict:
    prices = market.get_prices(p.ticker for p in positions)
    rows, total = [], 0.0
    for p in positions:
        update = prices.get(p.ticker)
        price = update.price if update else p.avg_cost  # unpriced: show at cost, flag it
        value = price * p.quantity
        total += value
        rows.append({
            "ticker": p.ticker,
            "quantity": p.quantity,
            "price": price,
            "stale": update is None,
            "value": round(value, 2),
            "unrealized_pnl": round((price - p.avg_cost) * p.quantity, 2),
        })
    return {"positions": rows, "total_value": round(total, 2)}
```

### 14.4 LLM portfolio context

The chat prompt gets prices from the same cache, plus the data source so the
model does not present simulated or day-old prices as live.

```python
def market_context(market: MarketDataService, tickers: list[str]) -> str:
    status = market.status()
    source = {
        "simulated": "simulated prices (not real market data)",
        "snapshot": "Massive market data, 15 minutes delayed or real-time by plan",
        "eod": "previous trading day's closing prices",
    }[status.mode]
    lines = [f"Price source: {source}."]
    for ticker, u in sorted(market.get_prices(tickers).items()):
        pct = f"{u.day_change_percent:+.2f}%" if u.day_change_percent is not None else "n/a"
        lines.append(f"{ticker}: ${u.price:,.2f} ({pct} today)")
    return "\n".join(lines)
```

## 15. Frontend consumption

The browser only needs `EventSource`; reconnection is built in and the `retry:`
line sets it to one second.

```ts
// frontend/src/lib/prices.ts
export type PriceUpdate = {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;
  direction: "up" | "down" | "flat";
  change: number;
  prev_close: number | null;
  day_change: number | null;
  day_change_percent: number | null;
};

export function subscribePrices(onPrices: (p: Record<string, PriceUpdate>) => void): () => void {
  const source = new EventSource("/api/stream/prices");
  source.onmessage = (event) => onPrices(JSON.parse(event.data)); // full set each time
  return () => source.close();
}

export async function fetchHistory(ticker: string) {
  const res = await fetch(`/api/market/history/${ticker}`);
  return (await res.json()).points as { timestamp: number; price: number }[];
}
```

- **Flash**: on each event, flash a row green or red when `direction` is `up` or
  `down`. A `flat` tick (common: one cent is about one tick of AAPL's
  volatility) does not flash.
- **Charts**: load `/api/market/history/{ticker}` once, then append each SSE
  price for that ticker.
- **Data-source badge**: call `/api/market/status` on load (and every minute).
  Show "Simulated", "Delayed 15 min" (`snapshot`) or "End of day" (`eod`), and a
  warning when `consecutive_failures > 0`.

## 16. Runtime walkthroughs

**Startup, no key**

1. `MarketSettings.from_env()` sees no key; the factory builds `SimulatorDataSource`.
2. `market.start(watchlist ∪ held)` seeds the cache with seed prices (`prev_close` = seed).
3. The tick task starts. Every 500 ms, all prices step and the cache version rises.
4. An SSE client connecting at t = 0 gets the seeded prices in its first event.

**Startup, free Massive key**

1. Factory builds `MassiveDataSource`; `start()` polls immediately.
2. Snapshot answers 403; mode becomes `eod`; same poll fetches 2 grouped-daily files.
3. Cache holds yesterday's closes with true prior closes. `start()` returns.
4. Every 15 minutes the loop wakes, sees the same target date, and makes no
   calls. The first poll after the New York date moves to a new weekday fetches again.

**Startup, Starter key**

1. First snapshot answers 200; mode stays `snapshot`; cache filled; `start()` returns.
2. Every 15 s one snapshot call refreshes every tracked ticker.

**User adds PYPL to the watchlist**

| Source | What happens | API calls | PYPL priced after |
| --- | --- | --- | --- |
| Simulator | Seeded from a random $50-$300 start | 0 | Immediately |
| Massive `snapshot` | Poller woken | 1 | One round trip (~100-300 ms) |
| Massive `eod` | Read from in-memory bars | 0 | Immediately |

**User sells all of TSLA, which is not on the watchlist**: the trade
transaction removes the position; `reconcile` drops TSLA from tracking; the
next SSE event no longer contains it.

## 17. Failure handling

| Failure | Detected as | Effect on prices | Recovery |
| --- | --- | --- | --- |
| No key | factory | Simulator runs | n/a |
| Bad or revoked key | 401 → `MassiveAuthError` | Last prices kept (none if at startup) | Back-off to 300 s; `status.last_error` says why; fix `.env` and restart |
| Free plan | 403 on snapshot | Switches to end-of-day | Automatic |
| Rate limited | 429 → `MassiveRateLimitError` | Last prices kept | Back-off; free plan retries after 60 s |
| Massive down / network | 5xx or `httpx` error → `MassiveAPIError` | Last prices kept | Back-off, then normal cadence |
| Malformed JSON / bug | any other exception | Last prices kept | Logged with traceback; loop continues |
| Holiday | empty grouped-daily | n/a | Walk back a weekday |
| Unknown ticker | absent from responses | Never priced | Trades refused with 409 |
| Simulator step raises | exception in `step()` | Last prices kept | Logged; next tick tries again |
| SSE client disconnects | `is_disconnected()` | none | Generator exits |

A key that is wrong at startup leaves the cache empty under Massive. The app
still starts; the status endpoint and the UI badge show the error. Falling back
to the simulator automatically was considered and rejected: silently showing
fake prices to someone who configured real ones is worse than showing none.

## 18. Tests

All tests run without network or a key. Massive is exercised through
`httpx.MockTransport` injected into `MassiveClient`; the simulator through its
`seed`. The full suite (42 tests) runs in about a second.

### 18.1 Cache and models

```python
# backend/tests/market/test_cache.py
from app.market.cache import PriceCache


def test_first_update_is_flat():
    cache = PriceCache()
    update = cache.update("AAPL", 190.0)
    assert update.direction == "flat"
    assert update.previous_price == 190.0


def test_second_update_sets_direction_and_previous_price():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    update = cache.update("AAPL", 190.5)
    assert update.direction == "up"
    assert update.previous_price == 190.0
    assert update.change == 0.5


def test_prev_close_survives_later_updates():
    cache = PriceCache()
    cache.update("AAPL", 190.0, prev_close=188.0)
    update = cache.update("AAPL", 191.76)
    assert update.prev_close == 188.0
    assert update.day_change_percent == 2.0


def test_exact_repeat_does_not_bump_version():
    cache = PriceCache()
    cache.update("AAPL", 190.0, timestamp=100.0, prev_close=188.0)
    v = cache.version
    cache.update("AAPL", 190.0, timestamp=100.0, prev_close=188.0)
    assert cache.version == v


def test_bad_prices_are_ignored():
    cache = PriceCache()
    assert cache.update("AAPL", 0.0) is None
    assert cache.update("AAPL", float("nan")) is None
    assert cache.get("AAPL") is None


def test_remove_bumps_version_and_drops_history():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    v = cache.version
    cache.remove("AAPL")
    assert cache.version == v + 1
    assert cache.history("AAPL") == []


def test_history_is_sampled_by_spacing():
    cache = PriceCache(history_spacing=5.0, history_length=3)
    for ts, price in [(0, 1.0), (1, 2.0), (5, 3.0), (10, 4.0), (15, 5.0)]:
        cache.update("X", price, timestamp=ts)
    points = cache.history("X")
    # (0,1)->(0,2) overwritten in-window; maxlen 3 keeps the last three slots.
    assert [(p.timestamp, p.price) for p in points] == [(5, 3.0), (10, 4.0), (15, 5.0)]
```

```python
# backend/tests/market/test_models.py
import pytest

from app.market.config import MarketSettings
from app.market.models import InvalidTickerError, normalize_ticker


@pytest.mark.parametrize("raw,expected", [(" aapl ", "AAPL"), ("brk.b", "BRK.B"), ("BF-B", "BF-B")])
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "1ABC", "AAPL;DROP", "TOOLONGTICKER", "A B"])
def test_normalize_ticker_rejects(raw):
    with pytest.raises(InvalidTickerError):
        normalize_ticker(raw)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_blank_key_selects_simulator(value):
    env = {} if value is None else {"MASSIVE_API_KEY": value}
    assert not MarketSettings.from_env(env).use_massive


def test_real_key_selects_massive():
    settings = MarketSettings.from_env({"MASSIVE_API_KEY": " abc123 "})
    assert settings.use_massive and settings.massive_api_key == "abc123"
```

```python
# backend/tests/market/test_factory.py
from app.market.cache import PriceCache
from app.market.config import MarketSettings
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


async def test_factory_picks_simulator_without_key():
    source = create_market_data_source(PriceCache(), MarketSettings())
    assert isinstance(source, SimulatorDataSource)


async def test_factory_picks_massive_with_key():
    source = create_market_data_source(PriceCache(), MarketSettings(massive_api_key="k"))
    assert isinstance(source, MassiveDataSource)
    await source.stop()  # closes the httpx client
```

### 18.2 Simulator

```python
# backend/tests/market/test_simulator.py
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
```

### 18.3 Massive

```python
# backend/tests/market/test_massive.py
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
```

### 18.4 Service, SSE and REST

```python
# backend/tests/market/test_service_and_stream.py
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_rest_routes():
    cache = PriceCache()
    cache.update("AAPL", 190.0, prev_close=188.0)
    market = MarketDataService(cache, SimulatorDataSource(cache))
    app = FastAPI()
    app.include_router(create_market_router(market))
    client = TestClient(app)
    assert client.get("/api/market/prices/aapl").json()["day_change"] == 2.0
    assert client.get("/api/market/prices/ZZZZ").status_code == 404
    assert client.get("/api/market/prices/bad;ticker").status_code == 422
    assert set(client.get("/api/market/prices?tickers=AAPL,MSFT").json()) == {"AAPL"}
    assert client.get("/api/market/status").json()["source"] == "simulator"
    assert len(client.get("/api/market/history/AAPL").json()["points"]) == 1
```

## 19. Build order

Each step is independently testable and leaves the app runnable.

1. `models.py`, `cache.py`, `interface.py` + `test_cache.py`, `test_models.py`.
2. `seed_prices.py`, `simulator.py` + `test_simulator.py`.
3. `config.py`, `factory.py`, `service.py` (simulator only at first).
4. `stream.py`, `routes.py`, `main.py`. Checkpoint: `curl -N /api/stream/prices`
   shows moving prices. **The frontend can start here.**
5. `massive_client.py` + `test_massive.py`. Then a manual check with a real free
   key (see Verification) and, if available, a Starter key.
6. Replace `load_tracked_tickers()` with the database query and call
   `reconcile` from the watchlist and trade routes.

## 20. Open questions

1. **Unknown symbols in simulator mode.** The simulator will price `ZZZZ`. If
   the watchlist should reject symbols that do not exist, the cheapest source is
   a static list of US tickers bundled with the app (Massive's
   `/v3/reference/tickers` can generate it), checked in the watchlist route.
   Recommendation: accept any well-formed symbol for now; it is a simulated-money app.
2. **Persisting simulator prices.** Every restart begins from the seed prices, so
   P&L jumps. Storing the last price per ticker in SQLite on shutdown (and
   seeding from it) fixes this in about twenty lines. Recommendation: do it once
   trades exist.
3. **SSE payload size.** Full snapshot per event is simple and fine below about
   100 tickers. Revisit only if the watchlist grows past that.
4. **Market hours on Massive.** Polling continues when the market is closed;
   the cache de-duplicates so no events go out, but calls are still spent.
   `GET /v1/marketstatus/now` could slow polling to every few minutes outside
   hours. Not worth it on paid plans; irrelevant in end-of-day mode.
5. **Plan upgrade without restart.** Mode detection is one-way. Re-probing the
   snapshot endpoint once a day in `eod` mode would pick up an upgrade; it costs
   one call a day. Deferred.
6. **Extra SSE fields.** The project brief lists ticker, price, previous price,
   timestamp and direction. `change`, `prev_close`, `day_change` and
   `day_change_percent` are additions the watchlist needs. They are additive and
   can be dropped from `to_dict()` if the smaller payload is preferred.

## Verification

All code in this document was copied from a working package and run on
3 October 2026 with Python 3.11, numpy 2.4, httpx 0.28, FastAPI 0.142, pytest 9.1:

- `pytest`: 42 passed. That covers the cache, ticker normalization, settings
  and factory selection, simulator statistics (volatility and correlations
  within 3% of target over one simulated day), Massive parsing, snapshot mode,
  403 to end-of-day switching with a Labor Day walk-back and true prior closes,
  zero-call ticker adds in end-of-day mode, the open-price fallback, 401/429/500
  and network errors keeping the last prices, back-off timing, `reconcile`, the
  SSE generator and the REST routes.
- `app/main.py` was run under uvicorn; `/api/market/status` and
  `/api/stream/prices` returned what sections 12 and 13 show.

Not verified: any call to the live Massive API with a real key. The status
codes and response shapes come from MASSIVE_API.md, where the 403 and 429 bodies
are marked unconfirmed. The code depends only on the status codes. Before
relying on Massive, run section 10.6 with a free key and confirm the snapshot
endpoint answers 403.
