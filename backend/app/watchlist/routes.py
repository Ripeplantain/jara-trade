"""REST endpoints for the watchlist."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.market import InvalidTickerError, normalize_ticker
from app.tracking import TrackingSync

from .store import WatchlistStore


class WatchlistAdd(BaseModel):
    ticker: str


def _normalize(raw: str) -> str:
    try:
        return normalize_ticker(raw)
    except InvalidTickerError as exc:
        raise HTTPException(422, str(exc)) from exc


def create_watchlist_router(store: WatchlistStore, tracking: TrackingSync) -> APIRouter:
    router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

    @router.get("")
    async def get_watchlist() -> dict:
        return {"tickers": store.list()}

    @router.post("")
    async def add_ticker(body: WatchlistAdd) -> dict:
        store.add(_normalize(body.ticker))
        # Database first, then tracking. Also runs for a no-op add, which heals
        # tracking if an earlier reconcile failed part-way.
        await tracking.sync()
        return {"tickers": store.list()}

    @router.delete("/{ticker}")
    async def remove_ticker(ticker: str) -> dict:
        symbol = _normalize(ticker)
        if not store.remove(symbol):
            raise HTTPException(404, f"{symbol} is not on the watchlist")
        # A ticker that is still held stays tracked: it is in the positions half
        # of the wanted set.
        await tracking.sync()
        return {"tickers": store.list()}

    return router
