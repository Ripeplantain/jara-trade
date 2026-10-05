"""Cash, positions and trade execution with average-cost accounting.

Positions store quantity and total cost basis (avg_cost = cost_basis / quantity),
so partial sells remove cost proportionally and nothing drifts through rounding
an average. Money is rounded to cents and quantity to 4 dp on every write.
"""
from __future__ import annotations

import math
import sqlite3
import time

from app.db import STARTING_CASH, Database
from app.market import MarketDataService, normalize_ticker

QUANTITY_DP = 4
SIDES = ("buy", "sell")


class TradeError(Exception):
    """Base for a trade that was refused. Nothing was written."""


class InvalidOrderError(TradeError):
    """Bad side or quantity (HTTP 422)."""


class InsufficientCashError(TradeError):
    def __init__(self, needed: float, cash: float) -> None:
        super().__init__(f"Insufficient cash: need ${needed:.2f}, have ${cash:.2f}")


class InsufficientSharesError(TradeError):
    def __init__(self, held: float, ticker: str) -> None:
        super().__init__(f"Insufficient shares: have {_fmt_quantity(held)} {ticker}")


def _fmt_quantity(quantity: float) -> str:
    return f"{quantity:.{QUANTITY_DP}f}".rstrip("0").rstrip(".")


def _money(value: float) -> float:
    return round(value + 0.0, 2)  # + 0.0 turns -0.0 into 0.0


def _trade_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "side": row["side"],
        "quantity": row["quantity"],
        "price": row["price"],
        "total": row["total"],
        "realized_pnl": row["realized_pnl"],
        "timestamp": row["timestamp"],
    }


class PortfolioService:
    def __init__(self, db: Database, market: MarketDataService) -> None:
        self._db = db
        self._market = market

    # ------------------------------------------------------------------ reads

    def get_portfolio(self) -> dict:
        with self._db.read() as conn:
            account = conn.execute("SELECT cash, starting_cash FROM account").fetchone()
            rows = conn.execute(
                "SELECT ticker, quantity, cost_basis FROM positions ORDER BY ticker"
            ).fetchall()
            realized = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM trades"
            ).fetchone()[0]

        prices = self._market.get_prices([row["ticker"] for row in rows]) if rows else {}
        positions = []
        for row in rows:
            quantity, cost_basis = row["quantity"], _money(row["cost_basis"])
            avg_cost = row["cost_basis"] / quantity
            update = prices.get(row["ticker"])
            # No price cached yet: value the position at cost rather than at zero.
            price = update.price if update else avg_cost
            market_value = _money(quantity * price)
            unrealized = _money(market_value - cost_basis)
            positions.append(
                {
                    "ticker": row["ticker"],
                    "quantity": quantity,
                    "avg_cost": _money(avg_cost),
                    "price": _money(price),
                    "market_value": market_value,
                    "cost_basis": cost_basis,
                    "unrealized_pnl": unrealized,
                    "unrealized_pnl_percent": (
                        round(unrealized / cost_basis * 100 + 0.0, 2) if cost_basis else 0.0
                    ),
                }
            )

        cash = _money(account["cash"])
        starting_cash = _money(account["starting_cash"])
        positions_value = _money(sum(p["market_value"] for p in positions))
        total_value = _money(cash + positions_value)
        total_pnl = _money(total_value - starting_cash)
        return {
            "cash": cash,
            "positions_value": positions_value,
            "total_value": total_value,
            "starting_cash": starting_cash,
            "total_pnl": total_pnl,
            "total_pnl_percent": (
                round(total_pnl / starting_cash * 100 + 0.0, 2) if starting_cash else 0.0
            ),
            "realized_pnl": _money(realized),
            "unrealized_pnl": _money(sum(p["unrealized_pnl"] for p in positions)),
            "positions": positions,
        }

    def list_trades(self, limit: int = 50) -> list[dict]:
        with self._db.read() as conn:
            rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_trade_dict(row) for row in rows]

    # ------------------------------------------------------------------ writes

    def execute_trade(self, ticker: str, side: str, quantity: float) -> dict:
        """Fill an order at the current cached price and return the trade.

        Raises InvalidTickerError, InvalidOrderError, PriceUnavailableError,
        InsufficientCashError or InsufficientSharesError; none of them write.
        Cash, position and trade row change in one SQLite transaction.
        """
        ticker = normalize_ticker(ticker)
        if side not in SIDES:
            raise InvalidOrderError("side must be 'buy' or 'sell'")
        if isinstance(quantity, bool) or not math.isfinite(quantity) or quantity <= 0:
            raise InvalidOrderError("quantity must be greater than 0")
        quantity = round(float(quantity), QUANTITY_DP)
        if quantity <= 0:
            raise InvalidOrderError(f"quantity must be at least {10 ** -QUANTITY_DP}")

        with self._db.transaction() as conn:
            cash = conn.execute("SELECT cash FROM account").fetchone()["cash"]
            position = conn.execute(
                "SELECT quantity, cost_basis FROM positions WHERE ticker = ?", (ticker,)
            ).fetchone()
            held = position["quantity"] if position else 0.0

            # Checked before the price so selling something you do not own says so.
            if side == "sell" and quantity > held:
                raise InsufficientSharesError(held, ticker)

            price = _money(self._market.require_price(ticker).price)
            total = _money(quantity * price)
            realized: float | None = None

            if side == "buy":
                # A total that rounds to $0.00 would hand out shares for free.
                if total <= 0:
                    raise InvalidOrderError("order value must be at least $0.01")
                if total > _money(cash):
                    raise InsufficientCashError(total, cash)
                new_cash = _money(cash - total)
                new_quantity = round(held + quantity, QUANTITY_DP)
                new_cost = _money((position["cost_basis"] if position else 0.0) + total)
            else:
                new_quantity = round(held - quantity, QUANTITY_DP)
                # Cost leaves the position in proportion to the shares sold; a full
                # sell removes exactly what is left.
                cost_sold = (
                    position["cost_basis"]
                    if new_quantity <= 0
                    else _money(position["cost_basis"] * quantity / held)
                )
                realized = _money(total - cost_sold)
                new_cash = _money(cash + total)
                new_cost = _money(position["cost_basis"] - cost_sold)

            conn.execute("UPDATE account SET cash = ?", (new_cash,))
            if new_quantity <= 0:
                conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
            else:
                conn.execute(
                    "INSERT INTO positions (ticker, quantity, cost_basis) VALUES (?, ?, ?) "
                    "ON CONFLICT (ticker) DO UPDATE SET quantity = excluded.quantity, "
                    "cost_basis = excluded.cost_basis",
                    (ticker, new_quantity, new_cost),
                )
            cursor = conn.execute(
                "INSERT INTO trades (ticker, side, quantity, price, total, realized_pnl, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, side, quantity, price, total, realized, time.time()),
            )
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _trade_dict(row)

    def reset(self) -> None:
        """Wipe trades and positions and restore the starting cash. Watchlist untouched."""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM positions")
            conn.execute(
                "UPDATE account SET cash = ?, starting_cash = ?", (STARTING_CASH, STARTING_CASH)
            )
