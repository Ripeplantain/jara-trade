import pytest

from app.portfolio import PortfolioService

from .conftest import trade

EMPTY = {
    "cash": 10000.0,
    "positions_value": 0.0,
    "total_value": 10000.0,
    "starting_cash": 10000.0,
    "total_pnl": 0.0,
    "total_pnl_percent": 0.0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "positions": [],
}


def test_new_portfolio(client):
    response = client.get("/api/portfolio")
    assert response.status_code == 200
    assert response.json() == EMPTY


def test_buy_fills_at_cached_price(client):
    response = trade(client, "aapl", "buy", 5)
    assert response.status_code == 200
    body = response.json()
    fill = body["trade"]
    assert fill.pop("timestamp") > 1_600_000_000
    assert fill == {
        "id": 1,
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 5,
        "price": 190.0,
        "total": 950.0,
        "realized_pnl": None,
    }
    assert body["portfolio"] == client.get("/api/portfolio").json()
    assert body["portfolio"] == {
        **EMPTY,
        "cash": 9050.0,
        "positions_value": 950.0,
        "positions": [
            {
                "ticker": "AAPL",
                "quantity": 5,
                "avg_cost": 190.0,
                "price": 190.0,
                "market_value": 950.0,
                "cost_basis": 950.0,
                "unrealized_pnl": 0.0,
                "unrealized_pnl_percent": 0.0,
            }
        ],
    }


def test_average_cost_and_realized_pnl(client, market):
    trade(client, "AAPL", "buy", 10)  # 10 @ 190
    market.cache.update("AAPL", 210.0)
    trade(client, "AAPL", "buy", 10)  # 10 @ 210 -> avg 200
    market.cache.update("AAPL", 220.0)

    portfolio = client.get("/api/portfolio").json()
    position = portfolio["positions"][0]
    assert position["avg_cost"] == 200.0 and position["cost_basis"] == 4000.0
    assert position["market_value"] == 4400.0
    assert position["unrealized_pnl"] == 400.0 and position["unrealized_pnl_percent"] == 10.0
    assert portfolio["unrealized_pnl"] == 400.0 and portfolio["realized_pnl"] == 0.0
    assert portfolio["total_value"] == 10400.0
    assert portfolio["total_pnl"] == 400.0 and portfolio["total_pnl_percent"] == 4.0

    body = trade(client, "AAPL", "sell", 5).json()  # 5 @ 220 against avg 200
    assert body["trade"]["realized_pnl"] == 100.0 and body["trade"]["total"] == 1100.0
    portfolio = body["portfolio"]
    position = portfolio["positions"][0]
    assert position["quantity"] == 15 and position["avg_cost"] == 200.0
    assert portfolio["cash"] == 7100.0
    assert portfolio["realized_pnl"] == 100.0 and portfolio["unrealized_pnl"] == 300.0
    assert portfolio["total_pnl"] == 400.0


def test_sell_to_zero_deletes_position(client, db):
    trade(client, "MSFT", "buy", 2)
    body = trade(client, "MSFT", "sell", 2).json()
    assert body["trade"]["realized_pnl"] == 0.0
    assert body["portfolio"] == EMPTY
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


def test_fractional_shares(client):
    body = trade(client, "AAPL", "buy", 0.1234).json()
    assert body["trade"]["quantity"] == 0.1234
    assert body["trade"]["total"] == 23.45  # 0.1234 * 190 = 23.446
    assert trade(client, "AAPL", "sell", 0.1234).json()["portfolio"]["positions"] == []


def test_positions_sorted_by_ticker(client):
    for ticker in ("TSLA", "AAPL", "MSFT"):
        trade(client, ticker, "buy", 1)
    tickers = [p["ticker"] for p in client.get("/api/portfolio").json()["positions"]]
    assert tickers == ["AAPL", "MSFT", "TSLA"]


@pytest.mark.parametrize(
    "body",
    [
        {"ticker": "bad;ticker", "side": "buy", "quantity": 1},
        {"ticker": "AAPL", "side": "hold", "quantity": 1},
        {"ticker": "AAPL", "side": "buy", "quantity": 0},
        {"ticker": "AAPL", "side": "buy", "quantity": -1},
        {"ticker": "AAPL", "side": "buy", "quantity": 0.00001},
        {"ticker": "AAPL", "side": "buy", "quantity": "lots"},
        {"ticker": "AAPL", "side": "buy"},
    ],
)
def test_invalid_orders_are_422(client, body):
    response = client.post("/api/trades", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert client.get("/api/portfolio").json() == EMPTY


def test_insufficient_cash(client):
    response = trade(client, "NVDA", "buy", 13)  # 13 * 800 = 10400
    assert response.status_code == 400
    assert response.json() == {"detail": "Insufficient cash: need $10400.00, have $10000.00"}
    assert client.get("/api/trades").json() == {"trades": []}


def test_can_spend_exactly_all_cash(client):
    response = trade(client, "NVDA", "buy", 12.5)
    assert response.status_code == 200
    assert response.json()["portfolio"]["cash"] == 0.0


def test_insufficient_shares(client):
    response = trade(client, "AAPL", "sell", 1)
    assert response.status_code == 400
    assert response.json() == {"detail": "Insufficient shares: have 0 AAPL"}

    trade(client, "AAPL", "buy", 2.5)
    response = trade(client, "AAPL", "sell", 3)
    assert response.status_code == 400
    assert response.json() == {"detail": "Insufficient shares: have 2.5 AAPL"}


def test_no_price_is_409(client):
    # Not tracked at all.
    response = trade(client, "PYPL", "buy", 1)
    assert response.status_code == 409
    assert "No price available for PYPL" in response.json()["detail"]
    # Tracked, but the source has not priced it yet.
    client.post("/api/watchlist", json={"ticker": "ZZZZ"})
    assert trade(client, "ZZZZ", "buy", 1).status_code == 409
    assert client.get("/api/portfolio").json() == EMPTY


def test_held_ticker_without_price_is_valued_at_cost(client, market):
    trade(client, "AAPL", "buy", 4)
    market.cache.remove("AAPL")
    position = client.get("/api/portfolio").json()["positions"][0]
    assert position["price"] == 190.0 and position["market_value"] == 760.0
    assert position["unrealized_pnl"] == 0.0


def test_buying_off_watchlist_ticker_keeps_it_tracked_until_fully_sold(client, market):
    client.post("/api/watchlist", json={"ticker": "PYPL"})
    trade(client, "PYPL", "buy", 4)
    client.delete("/api/watchlist/PYPL")
    assert "PYPL" in market.source.get_tickers()

    trade(client, "PYPL", "sell", 1)
    assert "PYPL" in market.source.get_tickers()
    trade(client, "PYPL", "sell", 3)
    assert "PYPL" not in market.source.get_tickers()


def test_trade_history_newest_first_with_limit(client):
    trade(client, "AAPL", "buy", 1)
    trade(client, "MSFT", "buy", 1)
    trade(client, "AAPL", "sell", 1)
    trades = client.get("/api/trades").json()["trades"]
    assert [t["id"] for t in trades] == [3, 2, 1]
    assert [t["side"] for t in trades] == ["sell", "buy", "buy"]
    assert trades[0]["realized_pnl"] == 0.0 and trades[1]["realized_pnl"] is None
    assert set(trades[0]) == {
        "id", "ticker", "side", "quantity", "price", "total", "realized_pnl", "timestamp",
    }
    assert [t["id"] for t in client.get("/api/trades?limit=2").json()["trades"]] == [3, 2]
    assert client.get("/api/trades?limit=0").status_code == 422


def test_reset(client, market):
    client.post("/api/watchlist", json={"ticker": "PYPL"})
    trade(client, "PYPL", "buy", 3)
    trade(client, "AAPL", "buy", 3)
    client.delete("/api/watchlist/PYPL")
    watchlist = client.get("/api/watchlist").json()

    response = client.post("/api/portfolio/reset")
    assert response.status_code == 200
    assert response.json() == EMPTY
    assert client.get("/api/trades").json() == {"trades": []}
    assert client.get("/api/watchlist").json() == watchlist
    assert "PYPL" not in market.source.get_tickers()


def test_failed_write_rolls_back_cash_and_position(db, market, monkeypatch):
    """Cash, position and trade row are one transaction."""
    db.init(["AAPL"])
    market.cache.update("AAPL", 190.0)
    service = PortfolioService(db, market)
    service.execute_trade("AAPL", "buy", 1)

    monkeypatch.setattr("app.portfolio.service.time.time", lambda: None)  # NOT NULL violation
    with pytest.raises(Exception):
        service.execute_trade("AAPL", "buy", 1)
    monkeypatch.undo()

    portfolio = service.get_portfolio()
    assert portfolio["cash"] == 9810.0
    assert portfolio["positions"][0]["quantity"] == 1
    assert len(service.list_trades()) == 1


def test_positions_survive_restart_and_are_tracked(db, market):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(market=market, db=db)) as first:
        first.post("/api/watchlist", json={"ticker": "PYPL"})
        trade(first, "PYPL", "buy", 2)
        first.delete("/api/watchlist/PYPL")
    market.source.tickers.clear()
    with TestClient(create_app(market=market, db=db)) as second:
        assert "PYPL" in market.source.get_tickers()
        assert second.get("/api/portfolio").json()["cash"] == 9860.0


def test_cors_allows_frontend_origin(client):
    response = client.get("/api/portfolio", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    response = client.get("/api/portfolio", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------- deeper coverage


def test_side_and_ticker_are_normalized_and_numeric_strings_accepted(client):
    response = client.post(
        "/api/trades", json={"ticker": " aapl ", "side": " BUY ", "quantity": "2.5"}
    )
    assert response.status_code == 200
    fill = response.json()["trade"]
    assert (fill["ticker"], fill["side"], fill["quantity"], fill["total"]) == (
        "AAPL", "buy", 2.5, 475.0,
    )


def test_trade_response_matches_history_and_portfolio_endpoints(client):
    trade(client, "AAPL", "buy", 3)
    body = trade(client, "AAPL", "sell", 1).json()
    assert client.get("/api/trades").json()["trades"][0] == body["trade"]
    assert client.get("/api/portfolio").json() == body["portfolio"]


def test_buy_one_cent_short_is_400_and_changes_nothing(client, market):
    trade(client, "AAPL", "buy", 1)  # cash 9810.00
    market.cache.update("NVDA", 9810.01)
    portfolio = client.get("/api/portfolio").json()
    trades = client.get("/api/trades").json()

    response = trade(client, "NVDA", "buy", 1)
    assert response.status_code == 400
    assert response.json() == {"detail": "Insufficient cash: need $9810.01, have $9810.00"}
    assert client.get("/api/portfolio").json() == portfolio
    assert client.get("/api/trades").json() == trades

    market.cache.update("NVDA", 9810.0)
    response = trade(client, "NVDA", "buy", 1)
    assert response.status_code == 200 and response.json()["portfolio"]["cash"] == 0.0


def test_oversell_is_400_and_changes_nothing(client):
    trade(client, "AAPL", "buy", 2)
    portfolio = client.get("/api/portfolio").json()
    response = trade(client, "AAPL", "sell", 2.0001)
    assert response.status_code == 400
    assert response.json() == {"detail": "Insufficient shares: have 2 AAPL"}
    assert client.get("/api/portfolio").json() == portfolio
    assert len(client.get("/api/trades").json()["trades"]) == 1


def test_selling_a_held_ticker_with_no_price_is_409_and_keeps_the_position(client, market):
    trade(client, "AAPL", "buy", 2)
    market.cache.remove("AAPL")
    response = trade(client, "AAPL", "sell", 1)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "No price available for AAPL yet. Add it to the watchlist and try again shortly."
    }
    portfolio = client.get("/api/portfolio").json()
    assert portfolio["positions"][0]["quantity"] == 2 and portfolio["cash"] == 9620.0


def test_quantity_rounding_over_http(client):
    body = trade(client, "AAPL", "buy", 1.23456).json()
    assert body["trade"]["quantity"] == 1.2346
    assert body["portfolio"]["positions"][0]["quantity"] == 1.2346
    response = trade(client, "AAPL", "buy", 0.00004)
    assert response.status_code == 422
    assert response.json() == {"detail": "quantity must be at least 0.0001"}


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_non_finite_quantity_is_422(client, raw):
    response = client.post(
        "/api/trades",
        content='{"ticker": "AAPL", "side": "buy", "quantity": %s}' % raw,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "quantity must be greater than 0"}
    assert client.get("/api/portfolio").json() == EMPTY


def test_boolean_quantity_is_422(client):
    response = client.post("/api/trades", json={"ticker": "AAPL", "side": "buy", "quantity": True})
    assert response.status_code == 422
    assert client.get("/api/portfolio").json() == EMPTY


@pytest.mark.parametrize("limit", ["0", "-1", "501", "abc", "1.5", ""])
def test_trades_limit_out_of_bounds_is_422(client, limit):
    response = client.get(f"/api/trades?limit={limit}")
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail"} and isinstance(body["detail"], str)
    assert body["detail"].startswith("limit: ")


def test_trades_limit_bounds_and_default(client):
    for _ in range(55):
        assert trade(client, "AAPL", "buy", 0.01).status_code == 200

    default = client.get("/api/trades").json()["trades"]
    assert [t["id"] for t in default] == list(range(55, 5, -1))  # 50, newest first
    assert [t["id"] for t in client.get("/api/trades?limit=1").json()["trades"]] == [55]
    everything = client.get("/api/trades?limit=500")
    assert everything.status_code == 200
    assert [t["id"] for t in everything.json()["trades"]] == list(range(55, 0, -1))


def test_reset_on_an_untouched_portfolio_is_a_noop(client):
    watchlist = client.get("/api/watchlist").json()
    response = client.post("/api/portfolio/reset")
    assert response.status_code == 200 and response.json() == EMPTY
    assert client.get("/api/watchlist").json() == watchlist


def test_reset_after_losses_restores_cash_and_allows_trading_again(client, market):
    trade(client, "AAPL", "buy", 10)
    market.cache.update("AAPL", 100.0)
    trade(client, "AAPL", "sell", 10)  # realized -900
    assert client.get("/api/portfolio").json()["cash"] == 9100.0

    assert client.post("/api/portfolio/reset").json() == EMPTY
    body = trade(client, "AAPL", "buy", 1).json()
    assert body["portfolio"]["cash"] == 9900.0 and body["portfolio"]["realized_pnl"] == 0.0
    assert len(client.get("/api/trades").json()["trades"]) == 1
