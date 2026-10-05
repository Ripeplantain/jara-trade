"""SQLite persistence: one file, stdlib sqlite3, no ORM.

Single user, so there is one `account` row. Every operation opens its own
short-lived connection; writes that must be atomic use `transaction()`.
"""
from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

STARTING_CASH = 10_000.00
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jara.db"

_SCHEMA = """
CREATE TABLE account (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    cash          REAL NOT NULL,
    starting_cash REAL NOT NULL
);
CREATE TABLE watchlist (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker   TEXT NOT NULL UNIQUE,
    added_at REAL NOT NULL
);
CREATE TABLE positions (
    ticker     TEXT PRIMARY KEY,
    quantity   REAL NOT NULL CHECK (quantity > 0),
    cost_basis REAL NOT NULL
);
CREATE TABLE trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    side         TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity     REAL NOT NULL,
    price        REAL NOT NULL,
    total        REAL NOT NULL,
    realized_pnl REAL,
    timestamp    REAL NOT NULL
);
"""


def db_path_from_env(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = env.get("JARA_DB_PATH", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_DB_PATH


class Database:
    """Handle on the SQLite file. Nothing touches disk until `init()`."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else db_path_from_env()

    def init(self, seed_watchlist: Iterable[str] = ()) -> bool:
        """Create the schema and seed it, only if the database is new.

        Returns True when the database was created by this call.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'account'"
            ).fetchone()
            if exists:
                return False
            # executescript() would commit implicitly; run the statements one by one
            # so schema and seed land in the same transaction.
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(
                "INSERT INTO account (id, cash, starting_cash) VALUES (1, ?, ?)",
                (STARTING_CASH, STARTING_CASH),
            )
            now = time.time()
            conn.executemany(
                "INSERT INTO watchlist (ticker, added_at) VALUES (?, ?)",
                [(ticker, now) for ticker in seed_watchlist],
            )
            return True

    def connect(self) -> sqlite3.Connection:
        # isolation_level=None: no implicit transactions, we BEGIN explicitly.
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One atomic write: commits on success, rolls back on any exception."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def tracked_tickers(self) -> list[str]:
        """Watchlist plus held positions: what the market service must track."""
        with self.read() as conn:
            rows = conn.execute(
                "SELECT ticker FROM watchlist UNION SELECT ticker FROM positions"
            ).fetchall()
        return sorted(row["ticker"] for row in rows)
