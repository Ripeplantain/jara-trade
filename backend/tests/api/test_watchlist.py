from app.market.seed_prices import DEFAULT_WATCHLIST
from app.watchlist import WatchlistStore

from .conftest import trade


def test_seeded_watchlist_is_tracked_on_startup(client, market):
    assert client.get("/api/watchlist").json() == {"tickers": DEFAULT_WATCHLIST}
    assert market.source.get_tickers() == sorted(DEFAULT_WATCHLIST)


def test_add_normalizes_appends_and_tracks(client, market):
    response = client.post("/api/watchlist", json={"ticker": " pypl "})
    assert response.status_code == 200
    assert response.json() == {"tickers": [*DEFAULT_WATCHLIST, "PYPL"]}
    assert "PYPL" in market.source.get_tickers()
    assert market.get_price("PYPL") == 70.0


def test_add_existing_is_noop(client):
    response = client.post("/api/watchlist", json={"ticker": "aapl"})
    assert response.status_code == 200
    assert response.json() == {"tickers": DEFAULT_WATCHLIST}


def test_add_invalid_is_422_with_string_detail(client):
    for body in ({"ticker": "bad;ticker"}, {"ticker": ""}, {}, {"ticker": 5}):
        response = client.post("/api/watchlist", json=body)
        assert response.status_code == 422, body
        assert isinstance(response.json()["detail"], str)
    assert client.get("/api/watchlist").json() == {"tickers": DEFAULT_WATCHLIST}


def test_remove_untracks(client, market):
    response = client.delete("/api/watchlist/tsla")
    assert response.status_code == 200
    assert "TSLA" not in response.json()["tickers"]
    assert "TSLA" not in market.source.get_tickers()


def test_remove_missing_is_404_and_invalid_is_422(client):
    response = client.delete("/api/watchlist/PYPL")
    assert response.status_code == 404
    assert response.json() == {"detail": "PYPL is not on the watchlist"}
    assert client.delete("/api/watchlist/bad;ticker").status_code == 422


def test_remove_held_ticker_keeps_tracking_until_sold(client, market):
    assert trade(client, "TSLA", "buy", 2).status_code == 200
    assert client.delete("/api/watchlist/TSLA").status_code == 200
    assert "TSLA" in market.source.get_tickers()
    assert client.get("/api/portfolio").json()["positions"][0]["price"] == 250.0

    assert trade(client, "TSLA", "sell", 2).status_code == 200
    assert "TSLA" not in market.source.get_tickers()


def test_watchlist_survives_restart(db, market):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(market=market, db=db)) as first:
        first.post("/api/watchlist", json={"ticker": "PYPL"})
        first.delete("/api/watchlist/AAPL")
    with TestClient(create_app(market=market, db=db)) as second:
        tickers = second.get("/api/watchlist").json()["tickers"]
    assert tickers[-1] == "PYPL" and "AAPL" not in tickers


def test_store_reports_whether_anything_changed(db):
    db.init(["AAPL"])
    store = WatchlistStore(db)
    assert store.add("MSFT") is True
    assert store.add("MSFT") is False
    assert store.list() == ["AAPL", "MSFT"]
    assert store.remove("AAPL") is True
    assert store.remove("AAPL") is False
    assert store.list() == ["MSFT"]


def test_readding_a_removed_ticker_puts_it_last(client):
    client.delete("/api/watchlist/AAPL")
    response = client.post("/api/watchlist", json={"ticker": "AAPL"})
    assert response.json() == {"tickers": [*DEFAULT_WATCHLIST[1:], "AAPL"]}


def test_adding_in_another_case_does_not_duplicate(client):
    for raw in ("pypl", "PYPL", " Pypl "):
        assert client.post("/api/watchlist", json={"ticker": raw}).status_code == 200
    tickers = client.get("/api/watchlist").json()["tickers"]
    assert tickers.count("PYPL") == 1 and len(tickers) == len(DEFAULT_WATCHLIST) + 1


def test_share_class_ticker_round_trips(client, market):
    assert client.post("/api/watchlist", json={"ticker": "brk.b"}).json()["tickers"][-1] == "BRK.B"
    assert "BRK.B" in market.source.get_tickers()
    response = client.delete("/api/watchlist/brk.b")
    assert response.status_code == 200 and "BRK.B" not in response.json()["tickers"]
    assert "BRK.B" not in market.source.get_tickers()


def test_removing_twice_is_404_the_second_time(client):
    assert client.delete("/api/watchlist/aapl").status_code == 200
    response = client.delete("/api/watchlist/aapl")
    assert response.status_code == 404
    assert response.json() == {"detail": "AAPL is not on the watchlist"}


def test_watchlist_can_be_emptied(client, market):
    for ticker in DEFAULT_WATCHLIST:
        assert client.delete(f"/api/watchlist/{ticker}").status_code == 200
    assert client.get("/api/watchlist").json() == {"tickers": []}
    assert market.source.get_tickers() == []
    assert market.get_prices() == {}


def test_unpriced_ticker_can_be_watched_and_removed(client, market):
    assert client.post("/api/watchlist", json={"ticker": "ZZZZ"}).status_code == 200
    assert "ZZZZ" in market.source.get_tickers() and market.get_price("ZZZZ") is None
    assert client.delete("/api/watchlist/ZZZZ").status_code == 200
    assert "ZZZZ" not in market.source.get_tickers()
