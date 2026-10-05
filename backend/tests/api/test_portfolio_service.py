"""Money correctness of PortfolioService, driven directly (no HTTP)."""
from __future__ import annotations

import threading

import pytest

from app.market import InvalidTickerError, PriceUnavailableError
from app.portfolio import (
    InsufficientCashError,
    InsufficientSharesError,
    InvalidOrderError,
    PortfolioService,
)


@pytest.fixture
def service(db, market) -> PortfolioService:
    db.init(["AAPL"])
    market.cache.update("AAPL", 190.0)
    return PortfolioService(db, market)


def snapshot(db) -> tuple:
    """Everything a trade may write, read straight from the tables."""
    with db.read() as conn:
        return (
            tuple(conn.execute("SELECT cash, starting_cash FROM account").fetchone()),
            [tuple(r) for r in conn.execute("SELECT * FROM positions ORDER BY ticker")],
            [tuple(r) for r in conn.execute("SELECT * FROM trades ORDER BY id")],
        )


def position(service: PortfolioService, ticker: str = "AAPL") -> dict:
    return next(p for p in service.get_portfolio()["positions"] if p["ticker"] == ticker)


# ---------------------------------------------------------------- average cost


def test_average_cost_across_several_buys_at_different_prices(service, market):
    service.execute_trade("AAPL", "buy", 3)  # 570.00
    market.cache.update("AAPL", 201.37)
    service.execute_trade("AAPL", "buy", 2)  # 402.74
    market.cache.update("AAPL", 185.12)
    fill = service.execute_trade("AAPL", "buy", 1.5)  # 277.68
    assert fill["price"] == 185.12 and fill["total"] == 277.68 and fill["realized_pnl"] is None

    portfolio = service.get_portfolio()
    held = portfolio["positions"][0]
    assert held["quantity"] == 6.5
    assert held["cost_basis"] == 1250.42
    assert held["avg_cost"] == 192.37  # 1250.42 / 6.5, not the mean of the three prices
    assert held["market_value"] == 1203.28  # 6.5 * 185.12
    assert held["unrealized_pnl"] == -47.14
    assert held["unrealized_pnl_percent"] == -3.77
    assert portfolio["cash"] == 8749.58
    assert portfolio["total_value"] == 9952.86
    assert portfolio["total_pnl"] == -47.14 and portfolio["total_pnl_percent"] == -0.47
    assert portfolio["realized_pnl"] == 0.0


def test_partial_then_full_sell_realize_against_average_cost(service, market):
    service.execute_trade("AAPL", "buy", 3)
    market.cache.update("AAPL", 201.37)
    service.execute_trade("AAPL", "buy", 2)
    market.cache.update("AAPL", 185.12)
    service.execute_trade("AAPL", "buy", 1.5)  # 6.5 shares, cost 1250.42

    market.cache.update("AAPL", 210.0)
    partial = service.execute_trade("AAPL", "sell", 2.5)
    assert partial["total"] == 525.0
    assert partial["realized_pnl"] == 44.07  # 525.00 - 1250.42 * 2.5 / 6.5 (480.93)
    held = position(service)
    assert held["quantity"] == 4.0 and held["cost_basis"] == 769.49
    assert held["avg_cost"] == pytest.approx(192.37, abs=0.006)  # unchanged by the sell
    assert service.get_portfolio()["cash"] == 9274.58

    market.cache.update("AAPL", 180.0)
    closing = service.execute_trade("AAPL", "sell", 4)
    assert closing["total"] == 720.0
    assert closing["realized_pnl"] == -49.49  # 720.00 - the 769.49 that was left

    portfolio = service.get_portfolio()
    assert portfolio["positions"] == []
    assert portfolio["realized_pnl"] == -5.42
    assert portfolio["cash"] == 9994.58
    assert portfolio["total_pnl"] == -5.42 and portfolio["unrealized_pnl"] == 0.0


def test_sell_leaves_average_cost_of_the_remainder_unchanged(service, market):
    market.cache.update("AAPL", 100.0)
    service.execute_trade("AAPL", "buy", 10)
    market.cache.update("AAPL", 110.0)
    service.execute_trade("AAPL", "buy", 10)
    assert position(service)["avg_cost"] == 105.0

    market.cache.update("AAPL", 120.0)
    assert service.execute_trade("AAPL", "sell", 7)["realized_pnl"] == 105.0  # 7 * (120 - 105)
    held = position(service)
    assert held["quantity"] == 13 and held["avg_cost"] == 105.0 and held["cost_basis"] == 1365.0


def test_buying_more_after_a_partial_sell_averages_from_the_remaining_cost(service, market):
    market.cache.update("AAPL", 100.0)
    service.execute_trade("AAPL", "buy", 10)
    market.cache.update("AAPL", 150.0)
    service.execute_trade("AAPL", "sell", 5)  # 5 left at 100
    market.cache.update("AAPL", 200.0)
    service.execute_trade("AAPL", "buy", 5)  # (500 + 1000) / 10
    held = position(service)
    assert held["quantity"] == 10 and held["cost_basis"] == 1500.0 and held["avg_cost"] == 150.0


def test_realized_loss_is_negative_and_accumulates(service, market):
    service.execute_trade("AAPL", "buy", 4)  # 4 @ 190
    market.cache.update("AAPL", 150.0)
    assert service.execute_trade("AAPL", "sell", 1)["realized_pnl"] == -40.0
    assert service.get_portfolio()["realized_pnl"] == -40.0
    assert position(service)["unrealized_pnl"] == -120.0
    assert service.execute_trade("AAPL", "sell", 3)["realized_pnl"] == -120.0

    portfolio = service.get_portfolio()
    assert portfolio["realized_pnl"] == -160.0
    assert portfolio["cash"] == 9840.0
    assert portfolio["total_pnl"] == -160.0 and portfolio["total_pnl_percent"] == -1.6


def test_closing_a_position_in_awkward_pieces_conserves_cash(service, market):
    """Once flat, cash gained or lost must equal the realized P&L, to the cent."""
    for price, quantity in ((190.0, 1.3333), (201.37, 2.6667), (33.33, 0.0007)):
        market.cache.update("AAPL", price)
        service.execute_trade("AAPL", "buy", quantity)
    for price, quantity in ((199.99, 0.7777), (177.01, 1.1111), (213.13, 1.0001), (150.55, 1.1118)):
        market.cache.update("AAPL", price)
        service.execute_trade("AAPL", "sell", quantity)

    portfolio = service.get_portfolio()
    assert portfolio["positions"] == []
    assert portfolio["positions_value"] == 0.0 and portfolio["unrealized_pnl"] == 0.0
    assert portfolio["realized_pnl"] == round(portfolio["cash"] - 10_000.0, 2)
    assert portfolio["total_pnl"] == portfolio["realized_pnl"]
    trades = service.list_trades()
    bought = sum(t["total"] for t in trades if t["side"] == "buy")
    sold = sum(t["total"] for t in trades if t["side"] == "sell")
    assert portfolio["cash"] == round(10_000.0 - bought + sold, 2)


def test_total_pnl_equals_realized_plus_unrealized_while_positions_are_open(service, market):
    market.cache.update("MSFT", 420.0)
    service.execute_trade("AAPL", "buy", 7.25)
    service.execute_trade("MSFT", "buy", 3.5)
    market.cache.update("AAPL", 203.47)
    service.execute_trade("AAPL", "sell", 2.1234)
    market.cache.update("MSFT", 398.76)
    service.execute_trade("MSFT", "sell", 1.0001)
    market.cache.update("AAPL", 187.02)

    portfolio = service.get_portfolio()
    assert len(portfolio["positions"]) == 2
    assert portfolio["total_pnl"] == round(portfolio["realized_pnl"] + portfolio["unrealized_pnl"], 2)
    assert portfolio["total_value"] == round(portfolio["cash"] + portfolio["positions_value"], 2)
    assert portfolio["positions_value"] == round(
        sum(p["market_value"] for p in portfolio["positions"]), 2
    )


# ---------------------------------------------------------------- rounding


def test_quantity_is_rounded_to_four_decimal_places(service):
    fill = service.execute_trade("AAPL", "buy", 1.23456)
    assert fill["quantity"] == 1.2346
    assert fill["total"] == 234.57  # 1.2346 * 190 = 234.574
    assert position(service)["quantity"] == 1.2346


def test_quantity_that_rounds_to_zero_is_refused(service, db):
    before = snapshot(db)
    with pytest.raises(InvalidOrderError, match="at least 0.0001"):
        service.execute_trade("AAPL", "buy", 0.00004)
    assert snapshot(db) == before


def test_quantity_just_over_half_a_step_rounds_up_to_the_smallest_lot(service):
    fill = service.execute_trade("AAPL", "buy", 0.00006)
    assert fill["quantity"] == 0.0001
    assert fill["total"] == 0.02  # 0.0001 * 190 = 0.019


def test_totals_and_cash_are_rounded_to_cents(service, market):
    market.cache.update("AAPL", 33.33)
    fill = service.execute_trade("AAPL", "buy", 0.3333)  # 11.108889
    assert fill["total"] == 11.11
    portfolio = service.get_portfolio()
    assert portfolio["cash"] == 9988.89
    assert portfolio["positions"][0]["cost_basis"] == 11.11


def test_price_is_filled_at_the_cached_cent_price(service, market):
    market.cache.update("AAPL", 190.126)  # the cache keeps cents
    fill = service.execute_trade("AAPL", "buy", 2)
    assert fill["price"] == 190.13 and fill["total"] == 380.26


def test_repeated_fractional_buys_do_not_accumulate_float_noise(service, db):
    for _ in range(3):
        service.execute_trade("AAPL", "buy", 0.1)
    with db.read() as conn:
        stored = conn.execute("SELECT quantity, cost_basis FROM positions").fetchone()
    assert tuple(stored) == (0.3, 57.0)  # not 0.30000000000000004
    service.execute_trade("AAPL", "sell", 0.3)
    assert service.get_portfolio()["positions"] == []
    assert service.get_portfolio()["cash"] == 10_000.0


def test_an_order_worth_less_than_half_a_cent_does_not_hand_out_free_shares(service, market):
    market.cache.update("AAPL", 40.0)  # 0.0001 * 40 = $0.004 -> total $0.00
    try:
        for _ in range(3):
            service.execute_trade("AAPL", "buy", 0.0001)
    except InvalidOrderError:
        pass  # refusing the order is a fine fix
    portfolio = service.get_portfolio()
    paid = round(10_000.0 - portfolio["cash"], 2)
    shares = sum(p["quantity"] for p in portfolio["positions"])
    assert shares == 0 or paid > 0


# ---------------------------------------------------------------- cash and share limits


def test_buy_with_exactly_all_cash_leaves_zero(service, market):
    market.cache.update("AAPL", 10_000.0)
    service.execute_trade("AAPL", "buy", 1)
    portfolio = service.get_portfolio()
    assert portfolio["cash"] == 0.0
    assert portfolio["total_value"] == 10_000.0

    # With nothing left even the smallest lot is refused...
    with pytest.raises(InsufficientCashError, match=r"need \$1\.00, have \$0\.00"):
        service.execute_trade("AAPL", "buy", 0.0001)
    # ...and selling brings the cash back.
    service.execute_trade("AAPL", "sell", 1)
    assert service.get_portfolio()["cash"] == 10_000.0


def test_buy_one_cent_short_is_refused_and_writes_nothing(service, market, db):
    market.cache.update("AAPL", 10_000.01)
    before = snapshot(db)
    with pytest.raises(InsufficientCashError) as exc:
        service.execute_trade("AAPL", "buy", 1)
    assert str(exc.value) == "Insufficient cash: need $10000.01, have $10000.00"
    assert snapshot(db) == before


def test_buy_one_cent_short_after_earlier_spending(service, market, db):
    service.execute_trade("AAPL", "buy", 10)  # cash 8100.00
    market.cache.update("AAPL", 810.0)
    service.execute_trade("AAPL", "buy", 9.9999)  # 8099.919 -> 8099.92, cash 0.08
    assert service.get_portfolio()["cash"] == 0.08
    before = snapshot(db)
    with pytest.raises(InsufficientCashError, match=r"need \$0\.16, have \$0\.08"):
        service.execute_trade("AAPL", "buy", 0.0002)  # 0.162
    assert snapshot(db) == before
    service.execute_trade("AAPL", "buy", 0.0001)  # 0.081 -> 0.08: exactly what is left
    assert service.get_portfolio()["cash"] == 0.0


def test_sell_exactly_all_closes_the_position(service, db):
    service.execute_trade("AAPL", "buy", 2.5)
    fill = service.execute_trade("AAPL", "sell", 2.5)
    assert fill["total"] == 475.0 and fill["realized_pnl"] == 0.0
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert service.get_portfolio()["cash"] == 10_000.0


def test_sell_one_step_more_than_held_is_refused_and_writes_nothing(service, db):
    service.execute_trade("AAPL", "buy", 2.5)
    before = snapshot(db)
    with pytest.raises(InsufficientSharesError) as exc:
        service.execute_trade("AAPL", "sell", 2.5001)
    assert str(exc.value) == "Insufficient shares: have 2.5 AAPL"
    assert snapshot(db) == before


def test_sell_quantity_is_rounded_before_it_is_compared_with_the_holding(service):
    service.execute_trade("AAPL", "buy", 2.5)
    fill = service.execute_trade("AAPL", "sell", 2.50004)  # rounds to 2.5: a full sell
    assert fill["quantity"] == 2.5
    assert service.get_portfolio()["positions"] == []


def test_sell_of_a_ticker_never_held_reports_zero_shares_even_without_a_price(service, db):
    before = snapshot(db)
    with pytest.raises(InsufficientSharesError, match="have 0 PYPL"):
        service.execute_trade("PYPL", "sell", 1)  # PYPL has no cached price either
    assert snapshot(db) == before


# ---------------------------------------------------------------- atomicity


@pytest.mark.parametrize(
    ("ticker", "side", "quantity", "error"),
    [
        ("AAPL", "buy", 1000, InsufficientCashError),
        ("AAPL", "sell", 3.0001, InsufficientSharesError),
        ("MSFT", "sell", 1, InsufficientSharesError),
        ("MSFT", "buy", 1, PriceUnavailableError),
        ("AAPL", "hold", 1, InvalidOrderError),
        ("AAPL", "BUY", 1, InvalidOrderError),  # the route lower-cases, the service is strict
        ("AAPL", "buy", 0, InvalidOrderError),
        ("AAPL", "buy", -1, InvalidOrderError),
        ("AAPL", "buy", float("nan"), InvalidOrderError),
        ("AAPL", "buy", float("inf"), InvalidOrderError),
        ("AAPL", "buy", True, InvalidOrderError),
        ("bad;ticker", "buy", 1, InvalidTickerError),
    ],
)
def test_refused_trade_leaves_cash_positions_and_trades_untouched(
    service, db, ticker, side, quantity, error
):
    service.execute_trade("AAPL", "buy", 3)
    before = snapshot(db)
    with pytest.raises(error):
        service.execute_trade(ticker, side, quantity)
    assert snapshot(db) == before


def test_sell_of_a_held_ticker_whose_price_disappeared_is_refused_untouched(service, market, db):
    service.execute_trade("AAPL", "buy", 3)
    market.cache.remove("AAPL")
    before = snapshot(db)
    with pytest.raises(PriceUnavailableError):
        service.execute_trade("AAPL", "sell", 1)
    assert snapshot(db) == before


def test_failure_while_writing_the_position_rolls_back_the_cash_update(service, db):
    """The cash UPDATE has already run when the position write fails."""
    service.execute_trade("AAPL", "buy", 1)
    before = snapshot(db)
    with db.transaction() as conn:
        conn.execute(
            "CREATE TRIGGER block_positions BEFORE UPDATE ON positions "
            "BEGIN SELECT RAISE(ABORT, 'disk on fire'); END"
        )
    with pytest.raises(Exception, match="disk on fire"):
        service.execute_trade("AAPL", "buy", 1)
    with db.transaction() as conn:
        conn.execute("DROP TRIGGER block_positions")
    assert snapshot(db) == before


def test_two_concurrent_all_in_buys_cannot_both_spend_the_cash(service, market):
    market.cache.update("AAPL", 10_000.0)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def buy() -> None:
        barrier.wait()
        try:
            service.execute_trade("AAPL", "buy", 1)
            outcomes.append("filled")
        except InsufficientCashError:
            outcomes.append("refused")

    threads = [threading.Thread(target=buy) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["filled", "refused"]
    portfolio = service.get_portfolio()
    assert portfolio["cash"] == 0.0
    assert portfolio["positions"][0]["quantity"] == 1
    assert len(service.list_trades()) == 1


# ---------------------------------------------------------------- valuation


def test_position_without_a_cached_price_is_valued_at_exactly_its_cost(service, market):
    market.cache.update("AAPL", 33.337)  # 33.34
    service.execute_trade("AAPL", "buy", 3)  # 100.02
    market.cache.update("AAPL", 33.33)
    service.execute_trade("AAPL", "buy", 4)  # 133.32 -> cost 233.34, avg 33.334285...
    market.cache.remove("AAPL")

    portfolio = service.get_portfolio()
    held = portfolio["positions"][0]
    assert held["avg_cost"] == 33.33 and held["price"] == 33.33
    # Valued at the unrounded average: 7 * 33.33 would be 233.31 and show a phantom loss.
    assert held["market_value"] == 233.34 == held["cost_basis"]
    assert held["unrealized_pnl"] == 0.0 and held["unrealized_pnl_percent"] == 0.0
    assert portfolio["total_value"] == 10_000.0 and portfolio["total_pnl"] == 0.0


def test_only_the_unpriced_position_falls_back_to_cost(service, market):
    market.cache.update("MSFT", 420.0)
    service.execute_trade("AAPL", "buy", 2)  # 380
    service.execute_trade("MSFT", "buy", 1)  # 420
    market.cache.update("AAPL", 200.0)
    market.cache.update("MSFT", 500.0)
    market.cache.remove("MSFT")

    portfolio = service.get_portfolio()
    aapl, msft = portfolio["positions"]
    assert (aapl["price"], aapl["market_value"], aapl["unrealized_pnl"]) == (200.0, 400.0, 20.0)
    assert (msft["price"], msft["market_value"], msft["unrealized_pnl"]) == (420.0, 420.0, 0.0)
    assert portfolio["positions_value"] == 820.0
    assert portfolio["unrealized_pnl"] == 20.0
    assert portfolio["total_value"] == 10_020.0


def test_unrealized_percent_is_relative_to_cost_basis(service, market):
    service.execute_trade("AAPL", "buy", 3)  # cost 570
    market.cache.update("AAPL", 171.0)
    held = position(service)
    assert held["unrealized_pnl"] == -57.0 and held["unrealized_pnl_percent"] == -10.0


# ---------------------------------------------------------------- history and reset


def test_list_trades_returns_the_newest_first_up_to_the_limit(service):
    for quantity in (1, 2, 3):
        service.execute_trade("AAPL", "buy", quantity)
    assert [t["quantity"] for t in service.list_trades()] == [3, 2, 1]
    assert [t["quantity"] for t in service.list_trades(2)] == [3, 2]
    assert [t["quantity"] for t in service.list_trades(1)] == [3]


def test_reset_restores_starting_cash_and_clears_realized_pnl(service, market, db):
    service.execute_trade("AAPL", "buy", 10)
    market.cache.update("AAPL", 100.0)
    service.execute_trade("AAPL", "sell", 4)  # realized -360
    assert service.get_portfolio()["realized_pnl"] == -360.0

    service.reset()
    portfolio = service.get_portfolio()
    assert portfolio["cash"] == 10_000.0 and portfolio["starting_cash"] == 10_000.0
    assert portfolio["positions"] == [] and portfolio["realized_pnl"] == 0.0
    assert portfolio["total_pnl"] == 0.0
    assert service.list_trades() == []
    with db.read() as conn:
        assert [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist")] == ["AAPL"]

    # Trading works again from a clean slate.
    fill = service.execute_trade("AAPL", "buy", 1)
    assert fill["total"] == 100.0 and service.get_portfolio()["cash"] == 9900.0
    assert len(service.list_trades()) == 1
