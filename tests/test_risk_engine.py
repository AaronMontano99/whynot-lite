"""Milestone 4: the central risk engine. Every order from every agent passes through it,
and nothing in it can be switched off by a caller."""

from dataclasses import FrozenInstanceError
from datetime import date

import inspect
import pytest

from whynot.riskengine import Order, RiskEngine, RiskSettings

D = date(2024, 3, 4)                    # a Monday


def order(bot="Kip", contract="QQQ240419C00440000", qty=1, price=5.0, direction="LONG", opens=True, day=D, **kw):
    spec = dict(bot=bot, underlying="QQQ", contract=contract, direction=direction, qty=qty, est_price=price, opens=opens,
                decision_day=day, data_day=day, volume=5000, trades=400)
    spec.update(kw)
    return Order(**spec)


@pytest.fixture
def risk():
    r = RiskEngine(RiskSettings(), equity=100_000.0)
    r.start_day(D, 100_000.0)
    return r


def fill(risk, o):
    assert risk.check(o).approved
    risk.record_fill(o, o.est_price)


def test_a_small_order_passes_and_says_why(risk):
    v = risk.check(order())
    assert v.approved and v.reasons == []


def test_position_bot_portfolio_and_correlated_limits(risk):
    assert "position" in risk.check(order(qty=5)).reasons[0]                       # $2,500 > 2% of $100,000
    for i in range(3):
        fill(risk, order(contract=f"QQQ240419C0044{i}000", qty=3, price=5.0))     # Kip now holds $4,500
    assert "Kip" in risk.check(order(contract="QQQ240419C00450000", qty=2)).reasons[0]     # $5,500 > 5% for one agent
    fill(risk, order(bot="Aura", contract="QQQ240419C00451000", qty=3, price=5.0))   # $6,000 of calls across agents
    v = risk.check(order(bot="Nemo", contract="SPY240419C00500000", underlying="SPY", qty=1))
    assert not v.approved and "same direction" in v.reasons[0]                      # the three funds move together
    for i in range(2):
        fill(risk, order(bot="Nemo", contract=f"SPY240419P0050{i}000", underlying="SPY", qty=4, price=5.0, direction="SHORT"))
    v = risk.check(order(bot="Chud", contract="IWM240419P00200000", underlying="IWM", qty=1, direction="SHORT"))
    assert not v.approved and "portfolio" in v.reasons[0]                           # $10,000 at risk is the ceiling


def test_daily_and_weekly_loss_limits_stop_new_entries_but_not_exits(risk):
    risk.mark(D, 97_900.0)                                                          # down 2.1% today
    v = risk.check(order())
    assert not v.approved and "daily loss" in v.reasons[0]
    assert risk.check(order(opens=False)).approved                                  # getting out is always allowed
    risk.start_day(date(2024, 3, 5), 97_900.0)
    assert risk.check(order(day=date(2024, 3, 5))).approved                         # a new day
    risk.mark(date(2024, 3, 5), 95_900.0)                                           # down 4.1% on the week
    risk.start_day(date(2024, 3, 6), 95_900.0)
    assert "weekly loss" in risk.check(order(day=date(2024, 3, 6))).reasons[0]
    risk.start_day(date(2024, 3, 11), 95_900.0)                                     # next Monday
    assert risk.check(order(day=date(2024, 3, 11))).approved


def test_the_kill_switch_fires_flattens_and_stays_off_until_the_owner_resets_it(risk):
    fill(risk, order())
    assert risk.mark(D, 95_000.0) == []                                             # 5% down: not yet
    to_close = risk.mark(D, 89_900.0)                                               # 10.1% below the peak
    assert risk.halted and "drawdown" in risk.halt_reason
    assert to_close == [("Kip", "QQQ240419C00440000")]                              # flatten everything
    risk.start_day(date(2024, 3, 5), 89_900.0)
    risk.mark(date(2024, 3, 5), 100_000.0)                                          # even a full recovery does not restart it
    v = risk.check(order(day=date(2024, 3, 5), contract="QQQ240419C00441000"))
    assert not v.approved and "halted" in v.reasons[0]
    assert risk.check(order(opens=False, day=date(2024, 3, 5))).approved
    with pytest.raises(PermissionError):
        risk.owner_reset("please")
    risk.owner_reset("I, the owner, reset the kill switch")
    assert not risk.halted and risk.events[-1]["kind"] == "reset"


def test_duplicates_rate_limits_stale_data_and_thin_contracts_are_refused(risk):
    o = order()
    fill(risk, o)
    assert "duplicate" in risk.check(o).reasons[0]
    assert "stale" in risk.check(order(contract="QQQ240419C00441000", data_day=date(2024, 2, 27))).reasons[0]
    assert "liquidity" in risk.check(order(contract="QQQ240419C00442000", volume=10)).reasons[0]
    assert "liquidity" in risk.check(order(contract="QQQ240419C00443000", qty=1, volume=60, trades=2)).reasons[0]
    for i in range(3):
        fill(risk, order(contract=f"QQQ240419P0043{i}000", direction="SHORT", price=0.5))
    assert "orders today" in risk.check(order(contract="QQQ240419P00439000", direction="SHORT", price=0.5)).reasons[0]


def test_repeated_critical_failures_halt_trading(risk):
    risk.failure("data feed error")
    risk.failure("data feed error")
    assert not risk.halted
    risk.failure("data feed error")
    assert risk.halted and "failures" in risk.halt_reason
    risk2 = RiskEngine(RiskSettings(), 100_000.0)
    risk2.start_day(D, 100_000.0)
    risk2.failure("x"); risk2.failure("x"); risk2.success(); risk2.failure("x")
    assert not risk2.halted                                                         # only consecutive failures count


def test_large_orders_need_the_owner_and_no_caller_can_override_a_limit():
    r = RiskEngine(RiskSettings(max_position_risk=0.05), 100_000.0)
    r.start_day(D, 100_000.0)
    v = r.check(order(qty=5))                                                       # $2,500: inside the 5% cap, above the 2% approval line
    assert not v.approved and v.needs_owner and "approval" in v.reasons[0]
    with pytest.raises(FrozenInstanceError):
        r.settings.max_drawdown = 0.9
    assert set(inspect.signature(RiskEngine.check).parameters) == {"self", "order"}   # no force, no bypass
    with pytest.raises(ValueError):
        RiskSettings(max_position_risk=0.5)                                          # settings outside sane bounds are refused


def test_everything_the_engine_does_is_logged(risk):
    risk.check(order(qty=50))
    risk.mark(D, 89_000.0)
    kinds = [e["kind"] for e in risk.events]
    assert "reject" in kinds and "kill" in kinds
