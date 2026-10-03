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
