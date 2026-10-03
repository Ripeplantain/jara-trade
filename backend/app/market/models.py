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
