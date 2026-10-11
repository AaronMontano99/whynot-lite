"""Milestone 4: the options engine and the options portfolio simulator. Prices come from
real traded bars; anything derived or assumed is labeled; nothing is invented."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from whynot.opt import pricing
from whynot.opt.engine import Quote, SelectionRule, entry_fill, exit_fill, select_contract, settle
from whynot.riskengine import RiskSettings
from whynot.sim.portfolio import run_options

D = date(2024, 3, 4)
EXP = date(2024, 4, 19)                 # 46 days out
NEAR = date(2024, 3, 15)                # 11 days out: too close


def occ(underlying, exp, kind, strike):
    return f"{underlying}{exp:%y%m%d}{'C' if kind == 'call' else 'P'}{int(strike * 1000):08d}"


class FakeData:
    """bars[(contract, day)] = Quote. A contract with no entry has no trade that day."""

    def __init__(self):
        self.bars, self.exps = {}, {}

    def add(self, underlying, exp, kind, strike, day, close, volume=2000, trades=200, open_=None):
        c = occ(underlying, exp, kind, strike)
        self.exps.setdefault(underlying, set()).add(exp)
        self.bars[(c, day)] = Quote(c, kind, strike, exp, open_ if open_ is not None else close, close * 1.05, close * 0.95, close, volume, trades)
        return c

    def expirations(self, underlying):
        return sorted(self.exps.get(underlying, ()))

    def chain(self, underlying, kind, exp, day):
        return [q for (c, d), q in self.bars.items() if d == day and q.kind == kind and q.expiration == exp and c.startswith(underlying)]

    def bar(self, contract, day):
        return self.bars.get((contract, day))


def priced(data, day, spot=440.0, vol=0.18, strikes=(420, 430, 440, 450, 460), exp=EXP, kinds=("call", "put"), **kw):
    if (exp - day).days <= 0:
        return                                                                       # nothing trades after it has expired
    for k in strikes:
        for kind in kinds:
            data.add("QQQ", exp, kind, k, day, round(pricing.price(kind, spot, k, (exp - day).days / 365, vol), 2), **kw)


# ---------------------------------------------------------------- derived quantities

def test_implied_volatility_inverts_the_price_and_delta_has_the_right_sign():
    p = pricing.price("call", 440, 440, 46 / 365, 0.18)
    assert pricing.implied_vol("call", p, 440, 440, 46 / 365) == pytest.approx(0.18, abs=1e-4)
    assert 0.5 < pricing.delta("call", 440, 440, 46 / 365, 0.18) < 0.6
    assert -0.5 < pricing.delta("put", 440, 440, 46 / 365, 0.18) < -0.4
    assert pricing.implied_vol("call", 19.0, 440, 420, 46 / 365) is None            # below what it is worth exercised: no answer, not a guess
    g = pricing.greeks("call", 440, 440, 46 / 365, 0.18)
    assert g["gamma"] > 0 and g["vega"] > 0 and g["theta"] < 0


# ---------------------------------------------------------------- choosing a contract

def test_picks_the_liquid_contract_nearest_the_target_delta_at_least_21_days_out():
    data = FakeData()
    priced(data, D)
    priced(data, D, exp=NEAR)
    choice, why = select_contract(data, "QQQ", "LONG", D, spot=440.0, budget=2000.0, rule=SelectionRule())
    assert choice.kind == "call" and choice.strike == 440 and choice.expiration == EXP and choice.dte == 46
    assert choice.delta == pytest.approx(0.53, abs=0.03) and choice.iv == pytest.approx(0.18, abs=0.01)
    assert choice.qty == 1 and "derived" in choice.labels["iv"] and "assumed" in choice.labels["slippage"]
    put, _ = select_contract(data, "QQQ", "SHORT", D, 440.0, 2000.0, SelectionRule())
    assert put.kind == "put" and put.delta < 0
    wide, _ = select_contract(data, "QQQ", "LONG", D, 440.0, 2000.0, SelectionRule(target_delta=0.30))
    assert wide.strike == 460                                                        # a cheaper, further-out strike


def test_thin_untraded_and_unaffordable_contracts_are_refused_with_a_reason():
    data = FakeData()
    priced(data, D, volume=10, trades=1)
    choice, why = select_contract(data, "QQQ", "LONG", D, 440.0, 2000.0, SelectionRule())
    assert choice is None and "liquid" in why
    data = FakeData()
    priced(data, D)
    choice, why = select_contract(data, "QQQ", "LONG", D, 440.0, 300.0, SelectionRule())
    assert choice is None and "costs more" in why
    choice, why = select_contract(FakeData(), "QQQ", "LONG", D, 440.0, 2000.0, SelectionRule())
    assert choice is None and "no" in why.lower()
    data = FakeData()
    priced(data, D, exp=NEAR)
    assert select_contract(data, "QQQ", "LONG", D, 440.0, 2000.0, SelectionRule())[0] is None      # nothing 21 days out


def test_the_choice_uses_only_what_traded_that_day():
    data = FakeData()
    priced(data, D)
    before = select_contract(data, "QQQ", "LONG", D, 440.0, 2000.0, SelectionRule())[0]
    priced(data, D + timedelta(days=1), spot=500.0, vol=0.6)                         # tomorrow is wild
    assert select_contract(data, "QQQ", "LONG", D, 440.0, 2000.0, SelectionRule())[0] == before


def test_fills_are_real_trades_plus_an_assumed_cost_and_no_trade_means_no_fill():
    data, rule = FakeData(), SelectionRule()
    c = data.add("QQQ", EXP, "call", 440, D + timedelta(days=1), close=12.0, open_=11.0)
    price, note = entry_fill(data, c, D + timedelta(days=1), rule)
    assert price == pytest.approx(11.0 * 1.015) and "assumed" in note                # 1.5% of the premium
    cheap = data.add("QQQ", EXP, "call", 470, D + timedelta(days=1), close=0.50, open_=0.50)
    assert entry_fill(data, cheap, D + timedelta(days=1), rule)[0] == pytest.approx(0.52)        # never less than 2 cents
    assert exit_fill(data, c, D + timedelta(days=1), rule)[0] == pytest.approx(11.0 * 0.985)
    assert entry_fill(data, c, D + timedelta(days=2), rule) == (None, "no trade printed that day")
    assert settle("call", 440, 452.5) == 12.5 and settle("put", 440, 452.5) == 0.0


# ---------------------------------------------------------------- the portfolio simulator

def sessions(n, start=D):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def signal(agent, entry_day, exit_day, direction="LONG", ret=0.02, reason="exit rule", state="CALM_UP", fraction=1.0):
    return SimpleNamespace(agent=agent, direction=direction, state=state, entry_day=entry_day, exit_day=exit_day, exit_reason=reason,
                           ret=ret, fraction=fraction, probs={"CALM_UP": 0.9, "CHOP": 0.1}, rule="test")


def world(days, path, vol=0.18):
    """Option bars every day along an underlying path, and the underlying's (open, close)."""
    data, under = FakeData(), {}
    for d, spot in zip(days, path):
        priced(data, d, spot=spot, vol=vol)
        under[d] = (spot, spot)
    return data, {"QQQ": under}


def test_a_signal_becomes_an_option_trade_and_both_results_are_kept():
    days = sessions(8)
    data, under = world(days, [440, 441, 444, 448, 452, 455, 455, 455])
    out = run_options({"Kip": [signal("Kip", days[1], days[5])]}, {"Kip": "QQQ"}, data, under, days, SelectionRule(), RiskSettings())
    t = out.trades[0]
    assert (t.agent, t.kind, t.strike, t.qty) == ("Kip", "call", 440, 1)
    assert t.entry_day == days[1] and t.exit_day == days[5] and t.pnl > 0
    assert t.underlying_ret == 0.02 and t.option_ret == pytest.approx(t.pnl / (t.entry_price * 100))
    assert t.simulated and out.equity[-1][1] == pytest.approx(100_000 + t.pnl)
    assert out.rejections == []


def test_refusals_are_recorded_and_nothing_is_bought():
    days = sessions(6)
    data, under = world(days, [440] * 6)
    del data.bars[(occ("QQQ", EXP, "call", 440), days[1])]                           # the chosen contract does not trade on the fill day
    out = run_options({"Kip": [signal("Kip", days[1], days[4])]}, {"Kip": "QQQ"}, data, under, days, SelectionRule(), RiskSettings())
    assert not out.trades and out.rejections[0]["reason"] == "no trade printed that day" and out.equity[-1][1] == 100_000
    tight = RiskSettings(max_position_risk=0.001)
    out = run_options({"Kip": [signal("Kip", days[1], days[4])]}, {"Kip": "QQQ"}, *world(days, [440] * 6), days, SelectionRule(), tight)
    assert not out.trades and out.rejections[0]["stage"] in ("selection", "risk")


def test_a_stopped_signal_exits_at_that_days_close_not_its_open():
    days = sessions(6)
    data, under = world(days, [440, 440, 441, 430, 430, 430])
    out = run_options({"Kip": [signal("Kip", days[1], days[3], reason="stop", ret=-0.05)]}, {"Kip": "QQQ"}, data, under, days, SelectionRule(), RiskSettings())
    t = out.trades[0]
    close = data.bar(t.contract, days[3]).close
    assert t.exit_price == pytest.approx(close - max(0.02, close * 0.015)) and t.pnl < 0


def test_an_unsold_option_settles_at_what_it_is_worth_at_expiry():
    days = sessions(40)
    path = [440 + i for i in range(40)]
    data, under = world(days, path)
    contract = occ("QQQ", EXP, "call", 440)
    for d in days[2:]:
        data.bars.pop((contract, d), None)                                            # it never trades again
    out = run_options({"Kip": [signal("Kip", days[1], days[39])]}, {"Kip": "QQQ"}, data, under, days, SelectionRule(), RiskSettings())
    t = out.trades[0]
    assert t.exit_reason == "settled at expiry" and t.exit_day == EXP
    assert t.exit_price == pytest.approx(under["QQQ"][EXP][1] - 440) and "stale" in " ".join(t.flags)


def test_the_kill_switch_flattens_the_book_and_stops_new_trades():
    days = sessions(10)
    data, under = world(days, [440] * 10)
    settings = RiskSettings(max_drawdown=0.001)                                       # absurdly tight, to make it fire
    sigs = {"Kip": [signal("Kip", days[1], days[8]), signal("Kip", days[6], days[8])]}
    crash = [440, 440, 430, 420, 420, 420, 420, 420, 420, 420]
    data, under = world(days, crash)
    out = run_options(sigs, {"Kip": "QQQ"}, data, under, days, SelectionRule(), settings)
    assert out.trades[0].exit_reason == "kill switch" and len(out.trades) == 1
    assert any(e["kind"] == "kill" for e in out.risk_events)
    assert any("halted" in r["reason"] for r in out.rejections)


def test_the_past_does_not_change_when_later_option_prices_do():
    days = sessions(10)
    data, under = world(days, [440, 441, 443, 445, 447, 449, 451, 453, 455, 457])
    sigs = {"Kip": [signal("Kip", days[1], days[3]), signal("Kip", days[5], days[8])]}
    base = run_options(sigs, {"Kip": "QQQ"}, data, under, days, SelectionRule(), RiskSettings())
    for (c, d), q in list(data.bars.items()):
        if d >= days[5]:
            data.bars[(c, d)] = Quote(c, q.kind, q.strike, q.expiration, q.open * 3, q.high * 3, q.low * 3, q.close * 3, q.volume, q.trades)
    other = run_options(sigs, {"Kip": "QQQ"}, data, under, days, SelectionRule(), RiskSettings())
    assert vars(base.trades[0]) == vars(other.trades[0])
    assert [e for e in base.equity if e[0] < days[4]] == [e for e in other.equity if e[0] < days[4]]


def test_adjusted_contracts_with_odd_strikes_are_never_chosen():
    data = FakeData()
    priced(data, D, strikes=(434.78, 439.78, 444.78))                                # left over from a corporate action
    assert select_contract(data, "QQQ", "LONG", D, 440.0, 2000.0, SelectionRule())[0] is None
    priced(data, D, strikes=(435, 440, 445))
    assert select_contract(data, "QQQ", "LONG", D, 440.0, 2000.0, SelectionRule())[0].strike == 440


def test_option_runs_are_stored_with_their_refusals_and_marked_simulated(tmp_path):
    from whynot.sim.ledger import Ledger
    days = sessions(8)
    data, under = world(days, [440, 441, 444, 448, 452, 455, 455, 455])
    out = run_options({"Kip": [signal("Kip", days[1], days[5]), signal("Kip", days[0], days[2])]}, {"Kip": "QQQ"}, data, under, days,
                      SelectionRule(), RiskSettings())
    ledger = Ledger(tmp_path / "w.db")
    run = ledger.save_options("demo", "VALIDATION", out, {"target_delta": 0.5})
    trades, refused = ledger.option_trades(run), ledger.option_rejections(run)
    assert len(trades) == 1 and trades[0]["contract"] == out.trades[0].contract and trades[0]["simulated"] == 1
    assert trades[0]["underlying_ret"] == 0.02 and len(refused) == 1 and refused[0]["stage"] == "selection"
