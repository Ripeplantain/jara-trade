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
            "ticker": ticker.strip().upper(),
            "points": [{"timestamp": p.timestamp, "price": p.price} for p in points],
        }

    @router.get("/status")
    async def status() -> dict:
        return market.status().to_dict()

    return router
