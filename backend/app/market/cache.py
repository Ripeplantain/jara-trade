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
