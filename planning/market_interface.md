# Market data interface

The unified Python API for stock prices in the Jara Trade backend. One interface,
two implementations: the Massive REST API when `MASSIVE_API_KEY` is set, and a
built-in simulator otherwise. Everything downstream reads from a shared price
cache and cannot tell which one is running.

Related documents:

- [MASSIVE_API.md](MASSIVE_API.md) — the endpoints, plans, and response shapes.
- [market_simulator.md](market_simulator.md) — how the simulator generates prices.

## Design

```
MASSIVE_API_KEY set?
   yes -> MassiveDataSource  (polls REST)  --+
   no  -> SimulatorDataSource (GBM, 500 ms) -+--> PriceCache --> SSE /api/stream/prices
                                                             --> trade execution
                                                             --> portfolio valuation
                                                             --> LLM portfolio context
```

Rules the design enforces:

- **One writer.** Exactly one data source runs, with exactly one background
  task. It is the only thing that writes to the cache.
- **Readers use the cache only.** SSE, trades, portfolio valuation, and the chat
  prompt never call a data source for a price. Adding SSE clients adds no API
  calls.
- **Sources push, readers pull.** A source writes whenever it has data. The SSE
  loop reads on its own 500 ms timer and skips the send if nothing changed.
- **Selection happens once**, in a factory, at startup. No other code reads
  `MASSIVE_API_KEY`.

## Files

```
backend/app/market/
  __init__.py        # public exports
  models.py          # PriceUpdate
  cache.py           # PriceCache
  interface.py       # MarketDataSource (abstract)
  factory.py         # create_market_data_source()
  massive_client.py  # MassiveDataSource
  simulator.py       # GBMSimulator, SimulatorDataSource
  seed_prices.py     # simulator starting prices and parameters
  stream.py          # SSE router
```

The `backend/` tree does not exist yet; these are the paths to create.
Dependencies: `httpx` (Massive), `numpy` (simulator), `fastapi`.

## PriceUpdate

The one data shape shared by the cache, the SSE stream, and every reader.

```python
# backend/app/market/models.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """One ticker's latest price, as stored in the cache and sent over SSE."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float  # Unix seconds
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
            "day_change_percent": self.day_change_percent,
        }
```

- `previous_price` is the price before the last update. It drives the green or
  red flash. On a ticker's first update it equals `price`, so direction is `flat`.
- `prev_close` is the previous session's close. It drives the watchlist's daily
  change, which `previous_price` cannot (that only looks back one tick).

**Open point.** The project brief lists the SSE fields as ticker, price,
previous price, timestamp, and direction. `prev_close`, `change`, and
`day_change_percent` are additions here, because the watchlist must show a daily
change and has no other source for it. They are additive; drop them from
`to_dict()` if the plan wants the smaller payload.

## PriceCache

```python
# backend/app/market/cache.py
from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """In-memory latest price per ticker. One writer (the data source), many readers."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version = 0

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        prev_close: float | None = None,
    ) -> PriceUpdate:
        with self._lock:
            existing = self._prices.get(ticker)
            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                # First sighting: previous == current, so direction is "flat".
                previous_price=existing.price if existing else round(price, 2),
                timestamp=timestamp if timestamp is not None else time.time(),
                prev_close=prev_close
                if prev_close is not None
                else (existing.prev_close if existing else None),
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1

    @property
    def version(self) -> int:
        """Bumps on every change; SSE uses it to skip sending unchanged data."""
        return self._version
```

- Prices are rounded to cents on the way in.
- The lock is uncontended in normal use, since everything runs on one event
  loop. It is there so a source that works from a thread (for example the
  official synchronous Massive client under `asyncio.to_thread`) stays safe.
- `version` lets the SSE loop skip sends when nothing changed. That matters with
  Massive, where the cache changes every 15 seconds but SSE wakes every 500 ms.

## MarketDataSource

```python
# backend/app/market/interface.py
from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Produces prices and writes them into a PriceCache.

    Implementations own exactly one background task. Nothing downstream reads
    from a source directly; everything reads the cache.
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Seed the cache for `tickers` and start the background task."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task and release resources. Safe to call twice."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a ticker. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and drop it from the cache."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""
```

Contract details that both implementations honour:

- `start()` fills the cache before it returns where it can, so the first SSE
  event and an immediate trade both have prices.
- `add_ticker()` and `remove_ticker()` are safe to call while running. The
  watchlist API calls them after it changes the database.
- A failed fetch never raises out of the background task. The cache keeps its
  last good prices and the task tries again on the next interval.
- Tickers are upper-case. The API layer normalizes before calling in.

## Factory

```python
# backend/app/market/factory.py
from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Massive if MASSIVE_API_KEY is set and non-empty, otherwise the simulator."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        from .massive_client import MassiveDataSource

        logger.info("Market data source: Massive REST API")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)

    from .simulator import SimulatorDataSource

    logger.info("Market data source: simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

An empty or whitespace-only key selects the simulator, so a blank line in `.env`
does the right thing. Imports are deferred so a simulator-only run does not need
`httpx` loaded, and the reverse for `numpy`.

## MassiveDataSource

```python
# backend/app/market/massive_client.py
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

BASE_URL = "https://api.massive.com"
SNAPSHOT_PATH = "/v2/snapshot/locale/us/markets/stocks/tickers"
GROUPED_DAILY_PATH = "/v2/aggs/grouped/locale/us/market/stocks/{date}"
EASTERN = ZoneInfo("America/New_York")


class MassiveDataSource(MarketDataSource):
    """Polls Massive REST for the union of tracked tickers.

    Two modes, chosen automatically on the first poll:

    - "snapshot": one call returns the latest price for every tracked ticker.
      Needs a Starter plan or above.
    - "eod": the snapshot endpoint answered 403 (free Basic plan), so fall back
      to the grouped daily bars, which give yesterday's close for every ticker
      in one call. Prices only change once per trading day in this mode.
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
        eod_poll_interval: float = 900.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache = price_cache
        self._poll_interval = poll_interval
        self._eod_poll_interval = eod_poll_interval
        self._tickers: set[str] = set()
        self._mode = "snapshot"
        self._task: asyncio.Task | None = None
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )

    @property
    def mode(self) -> str:
        return self._mode

    async def start(self, tickers: list[str]) -> None:
        self._tickers = {t.upper() for t in tickers}
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
        # Picked up on the next poll; no extra API call here.
        self._tickers.add(ticker.upper())

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker.upper())
        self._cache.remove(ticker.upper())

    def get_tickers(self) -> list[str]:
        return sorted(self._tickers)

    async def _poll_loop(self) -> None:
        while True:
            interval = self._poll_interval if self._mode == "snapshot" else self._eod_poll_interval
            await asyncio.sleep(interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Never raises: a failed poll leaves the last good prices in the cache."""
        if not self._tickers:
            return
        try:
            if self._mode == "snapshot":
                await self._poll_snapshot()
            else:
                await self._poll_eod()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 403 and self._mode == "snapshot":
                logger.warning("Massive plan has no snapshot access; using end-of-day prices")
                self._mode = "eod"
                await self._poll_once()
            elif status == 401:
                logger.error("Massive rejected the API key (401)")
            elif status == 429:
                logger.warning("Massive rate limit hit (429); will retry next interval")
            else:
                logger.error("Massive poll failed with HTTP %s", status)
        except httpx.HTTPError as exc:
            logger.error("Massive poll failed: %s", exc)

    async def _poll_snapshot(self) -> None:
        resp = await self._client.get(
            SNAPSHOT_PATH, params={"tickers": ",".join(sorted(self._tickers))}
        )
        resp.raise_for_status()
        for snap in resp.json().get("tickers", []):
            ticker = snap.get("ticker")
            price, timestamp = _snapshot_price(snap)
            if ticker in self._tickers and price:
                prev_close = (snap.get("prevDay") or {}).get("c") or None
                self._cache.update(ticker, price, timestamp=timestamp, prev_close=prev_close)

    async def _poll_eod(self) -> None:
        # Walk back from today to the most recent day that has bars.
        # Capped at 4 requests to stay inside the free tier's 5 calls/minute.
        day = datetime.now(EASTERN).date()
        for _ in range(4):
            day = _previous_weekday(day)
            resp = await self._client.get(GROUPED_DAILY_PATH.format(date=day.isoformat()))
            resp.raise_for_status()
            bars = resp.json().get("results") or []
            if not bars:
                continue  # market holiday
            for bar in bars:
                if bar.get("T") in self._tickers and bar.get("c"):
                    self._cache.update(
                        bar["T"], bar["c"], timestamp=bar["t"] / 1000, prev_close=bar.get("o")
                    )
            return


def _snapshot_price(snap: dict) -> tuple[float | None, float | None]:
    """Best available price from one snapshot entry, with its Unix-seconds timestamp.

    `lastTrade` is only present on plans that include trades, so fall back to
    the latest minute bar, then today's bar, then the previous close.
    """
    last_trade = snap.get("lastTrade") or {}
    if last_trade.get("p"):
        return last_trade["p"], last_trade["t"] / 1e9  # nanoseconds
    minute = snap.get("min") or {}
    if minute.get("c"):
        return minute["c"], minute["t"] / 1e3  # milliseconds
    updated = snap["updated"] / 1e9 if snap.get("updated") else None
    for key in ("day", "prevDay"):
        close = (snap.get(key) or {}).get("c")
        if close:
            return close, updated
    return None, None


def _previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day
```

How it behaves:

| Plan | Mode | Calls | Price movement |
| --- | --- | --- | --- |
| Starter, Developer | `snapshot` | 1 per 15 s, any number of tickers | 15 minutes behind the market |
| Advanced | `snapshot` | 1 per 15 s (lower `poll_interval` to 2-5 s) | Real-time |
| Basic (free) | `eod` | 1 per 15 min, up to 4 on a holiday | Static: last close, updated once a day |

- **Mode is detected, not configured.** The first snapshot call answers 403 on
  a free key; the source switches to `eod` and stays there. No plan setting is
  needed in `.env`.
- **Price choice** follows the fallback chain in MASSIVE_API.md: `lastTrade.p`,
  then `min.c`, then `day.c`, then `prevDay.c`.
- **A new ticker has no price until the next poll**, up to 15 seconds in
  snapshot mode and 15 minutes in end-of-day mode. Until then
  `cache.get_price()` returns `None`, and the trade endpoint must reject the
  order with a clear message rather than guess. If that delay is unacceptable,
  have `add_ticker()` trigger `_poll_once()`; the cost is one extra API call per
  add, which matters on the free tier.
- **An invalid ticker** never appears in a response, so it never gets a price.
- **Outside market hours** the snapshot keeps returning the last values, so
  prices stop moving. That is correct behaviour, not a fault.
- **In end-of-day mode `prev_close` is the day's open**, not the prior close, so
  the daily change shown is open-to-close. Getting the true prior close would
  cost a second full-market call. This is an approximation; say so in the UI or
  spend the extra call.
- End-of-day mode shows the last *completed* trading day, so on a Monday evening
  it still shows Friday until the date rolls over in New York.

On a free key the workstation looks frozen. For demos without a paid plan, leave
`MASSIVE_API_KEY` unset and use the simulator.

## SSE endpoint

```python
# backend/app/market/stream.py
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _events(price_cache, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


async def _events(
    price_cache: PriceCache, request: Request, interval: float = 0.5
) -> AsyncGenerator[str, None]:
    yield "retry: 1000\n\n"  # browser reconnects after 1 s if the stream drops
    last_version = -1
    while not await request.is_disconnected():
        if price_cache.version != last_version:
            last_version = price_cache.version
            payload = {t: u.to_dict() for t, u in price_cache.get_all().items()}
            if payload:
                yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(interval)
```

Each event carries every tracked ticker, keyed by symbol:

```
data: {"AAPL": {"ticker": "AAPL", "price": 190.12, "previous_price": 190.08, "timestamp": 1791037124.07, "direction": "up", "change": 0.04, "prev_close": 190.0, "day_change_percent": 0.0632}, "MSFT": {...}}
```

The browser side is `new EventSource("/api/stream/prices")`; reconnection is
built in.

## Wiring it into the app

```python
# backend/app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.market import PriceCache, create_market_data_source, create_stream_router

price_cache = PriceCache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    source = create_market_data_source(price_cache)
    app.state.price_cache = price_cache
    app.state.market_source = source
    # Watchlist tickers plus anything held, so open positions always have a price.
    await source.start(load_tracked_tickers())
    yield
    await source.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(price_cache))
```

How the rest of the backend uses it:

```python
# Trade execution: fill at the current cached price.
price = request.app.state.price_cache.get_price("AAPL")
if price is None:
    raise HTTPException(400, "No price available for AAPL yet")

# Watchlist add / remove: after the database write.
await request.app.state.market_source.add_ticker("PYPL")
await request.app.state.market_source.remove_ticker("PYPL")

# Portfolio valuation and the LLM context: read everything at once.
prices = request.app.state.price_cache.get_all()
```

One rule for the watchlist route: **do not stop tracking a ticker that is still
held.** If the user removes TSLA from the watchlist while owning shares, the
position still needs a live price. Only call `remove_ticker()` when the ticker
is in neither the watchlist nor the positions table.

## Testing

- **Cache**: first update is `flat`; second update sets `previous_price` and
  direction; `remove` bumps `version`; `prev_close` survives later updates.
- **Factory**: unset, empty, and whitespace keys give the simulator; a real
  value gives Massive.
- **Massive**: inject `httpx.AsyncClient(transport=httpx.MockTransport(handler))`
  through the `client` argument. No network, no key. Cover: snapshot parsing and
  the price fallback chain, zeros before the open, 403 switching to `eod`,
  holiday walk-back, 401/429/network errors leaving the cache intact, unknown
  tickers ignored.
- **SSE**: drive `_events()` with a fake request object and assert it emits
  once per cache version.

## Verification status

The model, cache, interface, factory, simulator, and Massive source in this
document were run as written on 3 October 2026 (Python 3.14, numpy 2.5,
httpx 0.28) against a mocked transport: snapshot mode, the 403 fall-back to
end-of-day mode with a holiday walk-back, a network failure, and all three
factory cases behaved as described.

Not run: anything against the live Massive API with a real key, and the FastAPI
snippets (`stream.py`, `main.py`), which are design sketches. The
`load_tracked_tickers()` helper is a placeholder for the database query.
