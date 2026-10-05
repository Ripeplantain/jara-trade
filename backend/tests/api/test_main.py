"""App wiring: startup seeding, persistence across restarts, and the error contract."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.market.seed_prices import DEFAULT_WATCHLIST

from .conftest import trade

JSON = {"Content-Type": "application/json"}


def test_startup_creates_and_seeds_a_new_database(db, market):
    assert not db.path.exists()
    with TestClient(create_app(market=market, db=db)) as client:
        assert db.path.is_file()
        assert client.get("/api/watchlist").json() == {"tickers": DEFAULT_WATCHLIST}
        assert client.get("/api/portfolio").json()["cash"] == 10_000.0


def test_restart_does_not_reseed_or_reset_anything(db, market):
    with TestClient(create_app(market=market, db=db)) as first:
        for ticker in DEFAULT_WATCHLIST:
            first.delete(f"/api/watchlist/{ticker}")
        first.post("/api/watchlist", json={"ticker": "PYPL"})
        trade(first, "PYPL", "buy", 3)
        trade(first, "PYPL", "sell", 1)
        portfolio = first.get("/api/portfolio").json()
        trades = first.get("/api/trades").json()

    with TestClient(create_app(market=market, db=db)) as second:
        assert second.get("/api/watchlist").json() == {"tickers": ["PYPL"]}
        assert second.get("/api/portfolio").json() == portfolio
        assert second.get("/api/trades").json() == trades
        assert portfolio["cash"] == 9860.0 and portfolio["positions"][0]["quantity"] == 2


def test_reset_survives_a_restart(db, market):
    with TestClient(create_app(market=market, db=db)) as first:
        trade(first, "AAPL", "buy", 3)
        first.post("/api/portfolio/reset")
    with TestClient(create_app(market=market, db=db)) as second:
        portfolio = second.get("/api/portfolio").json()
        assert portfolio["cash"] == 10_000.0 and portfolio["positions"] == []
        assert second.get("/api/trades").json() == {"trades": []}


@pytest.mark.parametrize(
    ("method", "url", "kwargs", "status"),
    [
        # 422: validation of body, query and path
        ("POST", "/api/trades", {"json": {}}, 422),
        ("POST", "/api/trades", {"content": "not json", "headers": JSON}, 422),
        ("POST", "/api/trades", {"content": "[]", "headers": JSON}, 422),
        ("POST", "/api/trades", {"content": "null", "headers": JSON}, 422),
        ("POST", "/api/trades", {}, 422),
        ("POST", "/api/trades", {"json": {"ticker": None, "side": 1, "quantity": []}}, 422),
        ("POST", "/api/trades", {"json": {"ticker": "AAPL", "side": "hold", "quantity": 1}}, 422),
        ("POST", "/api/trades", {"json": {"ticker": "A B", "side": "buy", "quantity": 1}}, 422),
        ("POST", "/api/watchlist", {"json": {}}, 422),
        ("POST", "/api/watchlist", {"json": {"ticker": ["AAPL"]}}, 422),
        ("POST", "/api/watchlist", {"json": {"ticker": "TOOLONGTICKER"}}, 422),
        ("DELETE", "/api/watchlist/bad;ticker", {}, 422),
        ("GET", "/api/trades?limit=0", {}, 422),
        ("GET", "/api/trades?limit=many", {}, 422),
        ("GET", "/api/market/prices/bad;ticker", {}, 422),
        # 400: refused trades
        ("POST", "/api/trades", {"json": {"ticker": "AAPL", "side": "buy", "quantity": 1e6}}, 400),
        ("POST", "/api/trades", {"json": {"ticker": "AAPL", "side": "sell", "quantity": 1}}, 400),
        # 404 / 405 / 409
        ("DELETE", "/api/watchlist/PYPL", {}, 404),
        ("GET", "/api/market/prices/PYPL", {}, 404),
        ("GET", "/api/does-not-exist", {}, 404),
        ("PUT", "/api/portfolio", {}, 405),
        ("POST", "/api/trades", {"json": {"ticker": "PYPL", "side": "buy", "quantity": 1}}, 409),
    ],
)
def test_every_error_body_is_a_single_string_detail(client, method, url, kwargs, status):
    response = client.request(method, url, **kwargs)
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"detail"}
    assert isinstance(body["detail"], str) and body["detail"].strip()


def test_validation_message_names_each_offending_field(client):
    response = client.post("/api/trades", json={})
    assert response.json() == {
        "detail": "ticker: Field required; side: Field required; quantity: Field required"
    }
    response = client.post("/api/trades", json={"ticker": "AAPL", "side": "buy", "quantity": "x"})
    detail = response.json()["detail"]
    assert detail.startswith("quantity: ") and "ticker" not in detail and "body" not in detail


def test_errors_do_not_change_state(client):
    """After the whole catalogue of bad requests the account is still pristine."""
    for method, url, kwargs in (
        ("POST", "/api/trades", {"json": {"ticker": "AAPL", "side": "buy", "quantity": 1e6}}),
        ("POST", "/api/trades", {"json": {"ticker": "AAPL", "side": "sell", "quantity": 1}}),
        ("POST", "/api/trades", {"json": {"ticker": "PYPL", "side": "buy", "quantity": 1}}),
        ("POST", "/api/watchlist", {"json": {"ticker": "bad ticker"}}),
        ("DELETE", "/api/watchlist/PYPL", {}),
    ):
        assert client.request(method, url, **kwargs).status_code >= 400
    assert client.get("/api/portfolio").json()["cash"] == 10_000.0
    assert client.get("/api/trades").json() == {"trades": []}
    assert client.get("/api/watchlist").json() == {"tickers": DEFAULT_WATCHLIST}
