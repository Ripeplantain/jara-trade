"""Keeps the market service's tracked set equal to watchlist plus held positions."""
from __future__ import annotations

import asyncio

from app.db import Database
from app.market import MarketDataService


class TrackingSync:
    """Call `sync()` after any write that changes the watchlist or the positions.

    The lock makes "read the wanted set, then reconcile" one step, so two
    overlapping requests cannot apply their reconciles out of order.
    """

    def __init__(self, db: Database, market: MarketDataService) -> None:
        self._db = db
        self._market = market
        self._lock = asyncio.Lock()

    async def sync(self) -> None:
        async with self._lock:
            await self._market.reconcile(self._db.tracked_tickers())
