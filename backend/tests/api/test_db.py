import sqlite3
from pathlib import Path

import pytest

from app.db import DEFAULT_DB_PATH, STARTING_CASH, Database, db_path_from_env
from app.market.seed_prices import DEFAULT_WATCHLIST
from app.watchlist import WatchlistStore


def test_path_from_env(tmp_path):
    assert db_path_from_env({}) == DEFAULT_DB_PATH
    assert db_path_from_env({"JARA_DB_PATH": " "}) == DEFAULT_DB_PATH
    assert db_path_from_env({"JARA_DB_PATH": str(tmp_path / "x.db")}) == tmp_path / "x.db"
    assert DEFAULT_DB_PATH.parts[-3:] == ("backend", "data", "jara.db")


def test_init_creates_dir_and_seeds_once(tmp_path):
    db = Database(tmp_path / "nested" / "jara.db")
    assert db.init(DEFAULT_WATCHLIST) is True
    store = WatchlistStore(db)
    assert store.list() == DEFAULT_WATCHLIST
    with db.read() as conn:
        assert tuple(conn.execute("SELECT cash, starting_cash FROM account").fetchone()) == (
            10000.0,
            10000.0,
        )

    # Not new any more: an emptied watchlist is not re-seeded.
    for ticker in DEFAULT_WATCHLIST:
        store.remove(ticker)
    assert Database(db.path).init(DEFAULT_WATCHLIST) is False
    assert store.list() == []


def test_transaction_rolls_back_on_error(db):
    db.init(["AAPL"])
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE account SET cash = 1")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.read() as conn:
        assert conn.execute("SELECT cash FROM account").fetchone()[0] == 10000.0


def test_tracked_tickers_is_watchlist_union_positions(db):
    db.init(["MSFT", "AAPL"])
    with db.transaction() as conn:
        conn.execute("INSERT INTO positions VALUES ('TSLA', 1, 250)")
        conn.execute("INSERT INTO positions VALUES ('AAPL', 1, 190)")
    assert db.tracked_tickers() == ["AAPL", "MSFT", "TSLA"]


def test_path_from_env_expands_home():
    assert db_path_from_env({"JARA_DB_PATH": "~/jara/x.db"}) == Path.home() / "jara" / "x.db"


def test_default_database_reads_jara_db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("JARA_DB_PATH", str(tmp_path / "env.db"))
    assert Database().path == tmp_path / "env.db"
    monkeypatch.delenv("JARA_DB_PATH")
    assert Database().path == DEFAULT_DB_PATH


def test_nothing_touches_disk_before_init(tmp_path):
    db = Database(tmp_path / "later" / "jara.db")
    assert not (tmp_path / "later").exists()
    db.init()
    assert db.path.is_file()


def test_init_without_seed_has_starting_cash_and_empty_tables(db):
    assert db.init() is True
    with db.read() as conn:
        assert conn.execute("SELECT cash FROM account").fetchone()[0] == STARTING_CASH == 10_000.0
        for table in ("watchlist", "positions", "trades"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert db.tracked_tickers() == []


def test_reopening_keeps_cash_positions_trades_and_watchlist(db):
    db.init(["AAPL", "MSFT"])
    with db.transaction() as conn:
        conn.execute("UPDATE account SET cash = 1234.56")
        conn.execute("DELETE FROM watchlist WHERE ticker = 'MSFT'")
        conn.execute("INSERT INTO positions VALUES ('TSLA', 2.5, 625)")
        conn.execute(
            "INSERT INTO trades (ticker, side, quantity, price, total, realized_pnl, timestamp) "
            "VALUES ('TSLA', 'buy', 2.5, 250, 625, NULL, 1)"
        )

    reopened = Database(db.path)
    # A different seed list on an existing database must be ignored entirely.
    assert reopened.init(["NVDA", "MSFT"]) is False
    with reopened.read() as conn:
        assert tuple(conn.execute("SELECT cash, starting_cash FROM account").fetchone()) == (
            1234.56,
            10_000.0,
        )
        assert [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist")] == ["AAPL"]
        assert tuple(conn.execute("SELECT * FROM positions").fetchone()) == ("TSLA", 2.5, 625.0)
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    assert reopened.tracked_tickers() == ["AAPL", "TSLA"]


def test_failed_seed_leaves_no_half_built_database(db):
    """Schema and seed are one transaction: a bad seed must not leave an unseeded schema."""
    with pytest.raises(sqlite3.IntegrityError):
        db.init(["AAPL", "AAPL"])  # UNIQUE violation on the second row
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    assert db.init(["AAPL"]) is True
    assert db.tracked_tickers() == ["AAPL"]


def test_transaction_commit_is_visible_to_other_connections(db):
    db.init()
    with db.transaction() as conn:
        conn.execute("UPDATE account SET cash = 42")
    with Database(db.path).read() as conn:
        assert conn.execute("SELECT cash FROM account").fetchone()[0] == 42


def test_transaction_rolls_back_on_base_exceptions_too(db):
    db.init()
    with pytest.raises(KeyboardInterrupt):
        with db.transaction() as conn:
            conn.execute("UPDATE account SET cash = 1")
            raise KeyboardInterrupt
    with db.read() as conn:
        assert conn.execute("SELECT cash FROM account").fetchone()[0] == 10_000.0
    # The connection was released: a new write does not hit a lock.
    with db.transaction() as conn:
        conn.execute("UPDATE account SET cash = 2")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO account (id, cash, starting_cash) VALUES (2, 1, 1)",  # single account row
        "INSERT INTO positions VALUES ('AAPL', 0, 0)",  # no empty positions
        "INSERT INTO positions VALUES ('AAPL', -1, 0)",
        "INSERT INTO watchlist (ticker, added_at) VALUES ('AAPL', 1)",  # no duplicates
        "INSERT INTO trades (ticker, side, quantity, price, total, timestamp) "
        "VALUES ('AAPL', 'short', 1, 1, 1, 1)",
    ],
)
def test_schema_rejects_invalid_rows(db, statement):
    db.init(["AAPL"])
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(statement)
