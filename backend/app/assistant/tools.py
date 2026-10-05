"""Tools the model can call. Reads go straight to the services; nothing here trades.

`propose_trade` only validates and returns a proposal for the UI to show. The
user confirms there, and the UI calls POST /api/trades. This module does not
import or call trade execution.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app.market import (
    InvalidTickerError,
    MarketDataService,
    PriceUnavailableError,
    PriceUpdate,
    normalize_ticker,
)

QUANTITY_DP = 4
MAX_TRADES = 50
HISTORY_SAMPLES = 12


class PortfolioReader(Protocol):
    def get_portfolio(self) -> dict: ...
    def list_trades(self, limit: int = 50) -> list[dict]: ...


class WatchlistReader(Protocol):
    def list(self) -> list[str]: ...


@dataclass(slots=True)
class ToolResult:
    content: dict
    is_error: bool = False
    proposal: dict | None = None  # set only by a valid propose_trade


class ToolError(Exception):
    """Bad tool input; the message goes back to the model."""


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": schema},
    }


TOOL_DEFINITIONS: list[dict] = [
    _tool(
        "get_prices",
        "Current prices with day change. Omit `tickers` for every tracked ticker "
        "(watchlist plus holdings).",
        {"tickers": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['AAPL']"}},
    ),
    _tool("get_watchlist", "The user's watchlist with current prices and day change.", {}),
    _tool(
        "get_portfolio",
        "Cash, positions (quantity, average cost, price, value) and P&L (realized, "
        "unrealized, total) for the paper account.",
        {},
    ),
    _tool(
        "get_recent_trades",
        "The most recent executed trades, newest first.",
        {"limit": {"type": "integer", "minimum": 1, "maximum": MAX_TRADES}},
    ),
    _tool(
        "get_price_history",
        "Summary of a ticker's recent in-session price history (start, end, high, low, "
        "change, a few sampled points). Short window, not long-term history.",
        {"ticker": {"type": "string"}},
        ["ticker"],
    ),
    _tool(
        "propose_trade",
        "Propose a market order. This does NOT place it: the proposal is shown to the user, "
        "who must confirm in the UI. Only call when the user wants a trade or asks for one.",
        {
            "ticker": {"type": "string"},
            "side": {"type": "string", "enum": ["buy", "sell"]},
            "quantity": {"type": "number", "description": "Shares, greater than 0"},
            "rationale": {"type": "string", "description": "One short sentence"},
        },
        ["ticker", "side", "quantity", "rationale"],
    ),
]

TOOL_NAMES = frozenset(t["function"]["name"] for t in TOOL_DEFINITIONS)
READ_TOOLS = TOOL_NAMES - {"propose_trade"}


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _price_row(update: PriceUpdate) -> dict:
    pct = update.day_change_percent
    return {
        "ticker": update.ticker,
        "price": _money(update.price),
        "day_change": update.day_change,
        "day_change_percent": None if pct is None else round(pct, 2),
        "as_of": _iso(update.timestamp),
    }


def _ticker(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ToolError("ticker must be a string")
    try:
        return normalize_ticker(raw)
    except InvalidTickerError as exc:
        raise ToolError(str(exc)) from exc


class AssistantTools:
    def __init__(
        self, market: MarketDataService, portfolio: PortfolioReader, watchlist: WatchlistReader
    ) -> None:
        self._market = market
        self._portfolio = portfolio
        self._watchlist = watchlist
        self.definitions = TOOL_DEFINITIONS

    def run(self, name: str, args: dict) -> ToolResult:
        """Never raises: any failure is returned to the model as an error result."""
        if name not in TOOL_NAMES:
            return ToolResult({"error": f"Unknown tool {name!r}"}, is_error=True)
        try:
            return getattr(self, f"_tool_{name}")(args if isinstance(args, dict) else {})
        except ToolError as exc:
            return ToolResult({"error": str(exc)}, is_error=True)
        except Exception:  # noqa: BLE001 - a broken tool must not kill the chat
            return ToolResult({"error": f"{name} failed unexpectedly"}, is_error=True)

    # ------------------------------------------------------------------ reads

    def _tool_get_prices(self, args: dict) -> ToolResult:
        raw = args.get("tickers")
        if raw is None:
            wanted = None
        elif isinstance(raw, list) and all(isinstance(t, str) for t in raw):
            wanted = [_ticker(t) for t in raw]
        else:
            raise ToolError("tickers must be a list of strings")
        prices = self._market.get_prices(wanted)
        out: dict[str, Any] = {"prices": [_price_row(u) for _, u in sorted(prices.items())]}
        if wanted is not None:
            out["unavailable"] = sorted(set(wanted) - set(prices))
        return ToolResult(out)

    def _tool_get_watchlist(self, args: dict) -> ToolResult:
        tickers = self._watchlist.list()
        prices = self._market.get_prices(tickers)
        return ToolResult(
            {
                "watchlist": [
                    _price_row(prices[t]) if t in prices else {"ticker": t, "price": None}
                    for t in tickers
                ]
            }
        )

    def _tool_get_portfolio(self, args: dict) -> ToolResult:
        return ToolResult(self._portfolio.get_portfolio())

    def _tool_get_recent_trades(self, args: dict) -> ToolResult:
        limit = args.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_TRADES:
            raise ToolError(f"limit must be an integer from 1 to {MAX_TRADES}")
        trades = [{**t, "time": _iso(t["timestamp"])} for t in self._portfolio.list_trades(limit)]
        return ToolResult({"trades": trades})

    def _tool_get_price_history(self, args: dict) -> ToolResult:
        ticker = _ticker(args.get("ticker"))
        points = self._market.history(ticker)
        if not points:
            raise ToolError(f"No price history for {ticker} yet")
        prices = [p.price for p in points]
        step = max(1, math.ceil(len(points) / HISTORY_SAMPLES))
        sampled = points[::step]
        if sampled[-1] is not points[-1]:
            sampled.append(points[-1])
        start, end = prices[0], prices[-1]
        return ToolResult(
            {
                "ticker": ticker,
                "window_seconds": round(points[-1].timestamp - points[0].timestamp),
                "points": len(points),
                "start": _money(start),
                "end": _money(end),
                "high": _money(max(prices)),
                "low": _money(min(prices)),
                "change": _money(end - start),
                "change_percent": round((end - start) / start * 100, 2) if start else None,
                "samples": [{"time": _iso(p.timestamp), "price": _money(p.price)} for p in sampled],
            }
        )

    # ------------------------------------------------------------------ propose

    def _tool_propose_trade(self, args: dict) -> ToolResult:
        ticker = _ticker(args.get("ticker"))
        side = args.get("side")
        side = side.strip().lower() if isinstance(side, str) else side
        if side not in ("buy", "sell"):
            raise ToolError("side must be 'buy' or 'sell'")
        quantity = args.get("quantity")
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, (int, float))
            or not math.isfinite(quantity)
        ):
            raise ToolError("quantity must be a number")
        quantity = round(float(quantity), QUANTITY_DP)
        if quantity <= 0:
            raise ToolError("quantity must be greater than 0")
        try:
            price = _money(self._market.require_price(ticker).price)
        except PriceUnavailableError as exc:
            raise ToolError(f"{exc}. Do not propose a trade for it.") from exc
        rationale = args.get("rationale")
        rationale = rationale.strip()[:500] if isinstance(rationale, str) else ""

        proposal = {
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "rationale": rationale,
            "estimated_price": price,
            "estimated_total": _money(quantity * price),
        }
        return ToolResult(
            {
                "status": "proposal_shown_to_user",
                "message": (
                    "The proposal is now shown to the user, who must confirm it in the app. "
                    "It has NOT been executed. Do not say it was placed."
                ),
                "proposal": proposal,
            },
            proposal=proposal,
        )
