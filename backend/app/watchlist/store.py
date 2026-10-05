"""Watchlist persistence. Tickers passed in are already normalized."""
from __future__ import annotations

import time

from app.db import Database


class WatchlistStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[str]:
        """Tickers in the order they were added."""
        with self._db.read() as conn:
            rows = conn.execute("SELECT ticker FROM watchlist ORDER BY id").fetchall()
        return [row["ticker"] for row in rows]

    def add(self, ticker: str) -> bool:
        """Returns False if the ticker was already on the watchlist."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
                (ticker, time.time()),
            )
            return cursor.rowcount > 0

    def remove(self, ticker: str) -> bool:
        """Returns False if the ticker was not on the watchlist."""
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
            return cursor.rowcount > 0
