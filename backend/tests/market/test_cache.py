from app.market.cache import PriceCache


def test_first_update_is_flat():
    cache = PriceCache()
    update = cache.update("AAPL", 190.0)
    assert update.direction == "flat"
    assert update.previous_price == 190.0


def test_second_update_sets_direction_and_previous_price():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    update = cache.update("AAPL", 190.5)
    assert update.direction == "up"
    assert update.previous_price == 190.0
    assert update.change == 0.5


def test_prev_close_survives_later_updates():
    cache = PriceCache()
    cache.update("AAPL", 190.0, prev_close=188.0)
    update = cache.update("AAPL", 191.76)
    assert update.prev_close == 188.0
    assert update.day_change_percent == 2.0


def test_exact_repeat_does_not_bump_version():
    cache = PriceCache()
    cache.update("AAPL", 190.0, timestamp=100.0, prev_close=188.0)
    v = cache.version
    cache.update("AAPL", 190.0, timestamp=100.0, prev_close=188.0)
    assert cache.version == v


def test_bad_prices_are_ignored():
    cache = PriceCache()
    assert cache.update("AAPL", 0.0) is None
    assert cache.update("AAPL", float("nan")) is None
    assert cache.get("AAPL") is None


def test_remove_bumps_version_and_drops_history():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    v = cache.version
    cache.remove("AAPL")
    assert cache.version == v + 1
    assert cache.history("AAPL") == []


def test_history_is_sampled_by_spacing():
    cache = PriceCache(history_spacing=5.0, history_length=3)
    for ts, price in [(0, 1.0), (1, 2.0), (5, 3.0), (10, 4.0), (15, 5.0)]:
        cache.update("X", price, timestamp=ts)
    points = cache.history("X")
    # (0,1)->(0,2) overwritten in-window; maxlen 3 keeps the last three slots.
    assert [(p.timestamp, p.price) for p in points] == [(5, 3.0), (10, 4.0), (15, 5.0)]


def test_contains_len_and_get_all_is_a_copy():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    snapshot = cache.get_all()
    snapshot.clear()
    assert "AAPL" in cache and len(cache) == 1


def test_prices_round_to_cents():
    cache = PriceCache()
    update = cache.update("AAPL", 190.123456, prev_close=188.987)
    assert update.price == 190.12 and update.prev_close == 188.99
