import pytest

from app.market.config import MarketSettings
from app.market.models import InvalidTickerError, normalize_ticker


@pytest.mark.parametrize("raw,expected", [(" aapl ", "AAPL"), ("brk.b", "BRK.B"), ("BF-B", "BF-B")])
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "1ABC", "AAPL;DROP", "TOOLONGTICKER", "A B"])
def test_normalize_ticker_rejects(raw):
    with pytest.raises(InvalidTickerError):
        normalize_ticker(raw)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_blank_key_selects_simulator(value):
    env = {} if value is None else {"MASSIVE_API_KEY": value}
    assert not MarketSettings.from_env(env).use_massive


def test_real_key_selects_massive():
    settings = MarketSettings.from_env({"MASSIVE_API_KEY": " abc123 "})
    assert settings.use_massive and settings.massive_api_key == "abc123"


def test_settings_overrides():
    settings = MarketSettings.from_env(
        {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": "3", "SIMULATOR_SEED": "42"}
    )
    assert settings.massive_poll_interval == 3.0
    assert settings.simulator_seed == 42


def test_price_update_derived_fields():
    from app.market.models import PriceUpdate

    u = PriceUpdate("AAPL", price=99.0, previous_price=100.0, timestamp=0, prev_close=None)
    assert u.direction == "down" and u.change == -1.0
    assert u.day_change is None and u.day_change_percent is None
    d = u.to_dict()
    assert set(d) == {
        "ticker", "price", "previous_price", "timestamp", "direction",
        "change", "prev_close", "day_change", "day_change_percent",
    }
