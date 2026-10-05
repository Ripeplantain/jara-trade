"""REST endpoints for the portfolio and trading."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from app.market import InvalidTickerError, PriceUnavailableError
from app.tracking import TrackingSync

from .service import InvalidOrderError, PortfolioService, TradeError


class TradeOrder(BaseModel):
    ticker: str
    side: str
    quantity: float

    @field_validator("quantity", mode="before")
    @classmethod
    def _reject_bool(cls, value: object) -> object:
        # JSON true would otherwise be coerced to 1.0 and buy a share.
        if isinstance(value, bool):
            raise ValueError("quantity must be a number")
        return value


def create_portfolio_router(portfolio: PortfolioService, tracking: TrackingSync) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["portfolio"])

    @router.get("/portfolio")
    async def get_portfolio() -> dict:
        return portfolio.get_portfolio()

    @router.post("/portfolio/reset")
    async def reset_portfolio() -> dict:
        portfolio.reset()
        await tracking.sync()  # positions are gone: drop non-watchlist tickers
        return portfolio.get_portfolio()

    @router.post("/trades")
    async def place_trade(order: TradeOrder) -> dict:
        try:
            trade = portfolio.execute_trade(order.ticker, order.side.strip().lower(), order.quantity)
        except (InvalidTickerError, InvalidOrderError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except PriceUnavailableError as exc:
            raise HTTPException(
                409, f"{exc}. Add it to the watchlist and try again shortly."
            ) from exc
        except TradeError as exc:
            raise HTTPException(400, str(exc)) from exc
        # Keeps a bought ticker tracked even off the watchlist, and stops tracking
        # a non-watchlist ticker once it is fully sold.
        await tracking.sync()
        return {"trade": trade, "portfolio": portfolio.get_portfolio()}

    @router.get("/trades")
    async def list_trades(limit: int = Query(50, ge=1, le=500)) -> dict:
        return {"trades": portfolio.list_trades(limit)}

    return router
