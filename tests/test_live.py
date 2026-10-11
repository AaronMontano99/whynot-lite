"""Milestone 6: paper trading. The lifecycle, the after-close and at-open steps, broker
reconciliation and restart safety, all against a local simulated broker."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from whynot.live.broker import SimBroker
from whynot.live.lifecycle import Lifecycle
from whynot.live.state import LiveDB
from whynot.live.steps import close_step, open_step, reconcile
from whynot.opt import pricing
from whynot.opt.engine import Quote, SelectionRule
from whynot.researchlab.memory import OWNER_PHRASE
from whynot.riskengine import RiskEngine, RiskSettings

ET = ZoneInfo("America/New_York")
D1, D2, D3 = date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)
EXP = date(2026, 11, 20)
CALL = "QQQ261120C00600000"


class Chain:
    """Today's real traded option bars, as the after-close step sees them."""

    def __init__(self, day, spot=600.0, vol=0.18):
        self.day, self.quotes = day, []
        for k in (580, 590, 600, 610, 620):
            for kind in ("call", "put"):
                p = round(pricing.price(kind, spot, k, (EXP - day).days / 365, vol), 2)
                self.quotes.append(Quote(f"QQQ261120{'C' if kind == 'call' else 'P'}{k * 1000:08d}", kind, k, EXP, p, p, p, p, 3000, 300))

    def expirations(self, underlying):
        return [EXP]

    def chain(self, underlying, kind, exp, day):
        return [q for q in self.quotes if q.kind == kind] if day == self.day else []

    def bar(self, contract, day):
        return next((q for q in self.quotes if q.contract == contract and day == self.day), None)


def decision(action, direction="LONG", state="CALM_UP", fraction=1.0, stopped=False):
    return SimpleNamespace(action=action, direction=direction, state=state, fraction=fraction, reason="test", probs={"CALM_UP": 0.9}, stopped=stopped)


@pytest.fixture
def kit(tmp_path):
    db = LiveDB(tmp_path / "w.db")
    broker = SimBroker(equity=100_000.0)
    risk = RiskEngine(RiskSettings(), 100_000.0)
    return SimpleNamespace(db=db, broker=broker, risk=risk, rule=SelectionRule(), symbols={"Kip": "QQQ"}, spots={"QQQ": 600.0})


def after_close(kit, day, what):
    return close_step(day, kit.symbols, lambda agent: what, Chain(day), kit.spots, kit.broker, kit.db, kit.risk, kit.rule)


def at_open(kit, day, hour=9, minute=52, **kw):
    now = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)
    return open_step(day, kit.broker, kit.db, kit.risk, kit.rule, now=now, **kw)


def quote(kit, contract=CALL, bid=14.6, ask=14.8, day=D2, minute=37):
    kit.broker.set_quote(contract, bid, ask, datetime(day.year, day.month, day.day, 9, minute, tzinfo=ET))


# ---------------------------------------------------------------- the lifecycle

def test_paper_trading_needs_the_owner_and_initialization_never_restarts(tmp_path):
    life = Lifecycle(tmp_path / "w.db")
    assert life.mode == "HISTORICAL_INITIALIZATION"
    with pytest.raises(PermissionError):
        life.enter_paper(phrase="the backtest finished, so go", agents=["Kip"], note="")
    with pytest.raises(RuntimeError):
        life.enter_paper(phrase=OWNER_PHRASE, agents=["Kip"], note="")              # initialization has not been reported complete
    life.complete_initialization("data/reports/backtest_development.md")
    assert life.mode == "HISTORICAL_INITIALIZATION"                                # finishing does not start paper trading by itself
    life.enter_paper(phrase=OWNER_PHRASE, agents=["Kip", "Nemo"], note="owner: paper trade the daily agents")
    assert life.mode == "LIVE_PAPER_TRADING" and life.agents == ["Kip", "Nemo"]
    life.to("RESEARCH"); life.to("VALIDATION"); life.to("LIVE_PAPER_TRADING")
    with pytest.raises(RuntimeError):
        life.to("HISTORICAL_INITIALIZATION")
    with pytest.raises(RuntimeError):
        life.complete_initialization("again")                                      # it ends once
    again = Lifecycle(tmp_path / "w.db")
    assert again.mode == "LIVE_PAPER_TRADING" and again.agents == ["Kip", "Nemo"]  # and survives a restart
    assert [e["mode"] for e in again.history()][-3:] == ["RESEARCH", "VALIDATION", "LIVE_PAPER_TRADING"]


# ---------------------------------------------------------------- after the close

def test_an_entry_becomes_one_pending_order_and_running_twice_changes_nothing(kit):
    made = after_close(kit, D1, decision("ENTER"))
    assert len(made) == 1 and made[0]["side"] == "BUY" and made[0]["contract"] == CALL and made[0]["status"] == "pending"
    assert made[0]["qty"] == 1 and made[0]["for_day"] is None                      # it waits for the next session, whenever that is
    assert after_close(kit, D1, decision("ENTER")) == []                           # the day is already processed
    assert len(kit.db.intents()) == 1 and kit.db.checkpoint("close") == D1.isoformat()


def test_a_refused_entry_is_recorded_and_no_order_is_queued(kit):
    kit.risk.kill("test halt")
    after_close(kit, D1, decision("ENTER"))
    rows = kit.db.intents()
    assert len(rows) == 1 and rows[0]["status"] == "refused" and "halted" in rows[0]["reason"]
    assert kit.db.intents(status="pending") == []


def test_nothing_to_do_is_still_logged(kit):
    assert after_close(kit, D1, decision("NONE")) == []
    assert any(e["kind"] == "close step" for e in kit.db.log())


# ---------------------------------------------------------------- at the open

def test_the_order_goes_in_once_fills_and_becomes_a_position(kit):
    after_close(kit, D1, decision("ENTER"))
    quote(kit)
    done = at_open(kit, D2)
    order = kit.broker.orders()[0]
    assert order["client_order_id"] == f"Kip-{CALL}-{D2}-BUY" and order["limit_price"] == 14.8      # at the ask, never above it
    assert done[0]["status"] == "filled" and done[0]["fill_price"] == 14.8
    pos = kit.db.position("Kip")
    assert pos["contract"] == CALL and pos["qty"] == 1 and pos["entry_price"] == 14.8 and pos["simulated"] == 1
    assert ("Kip", CALL) in kit.risk.positions
    at_open(kit, D2)                                                                                # a second run the same morning
    assert len(kit.broker.orders()) == 1


def test_a_stale_quote_or_a_price_that_ran_away_means_no_order(kit):
    after_close(kit, D1, decision("ENTER"))
    quote(kit, day=D1, minute=30)                                                                   # yesterday's quote
    done = at_open(kit, D2)
    assert done[0]["status"] == "refused" and "stale" in done[0]["reason"] and kit.broker.orders() == []
    kit2 = SimpleNamespace(**{**vars(kit), "db": LiveDB(str(kit.db.path) + "2")})
    after_close(kit2, D1, decision("ENTER"))
    quote(kit2, bid=29.0, ask=30.0)                                                                 # $3,000 a contract: above the 2% position limit
    done = at_open(kit2, D2)
    assert done[0]["status"] == "refused" and "position" in done[0]["reason"]


def test_an_order_that_does_not_fill_is_cancelled_and_nothing_is_held(kit):
    after_close(kit, D1, decision("ENTER"))
    quote(kit)
    kit.broker.fill_orders = False
    assert at_open(kit, D2)[0]["status"] == "submitted"
    done = at_open(kit, D2, hour=10, minute=31)                                                      # past the deadline
    assert done[0]["status"] == "cancelled" and kit.db.position("Kip") is None
    assert kit.broker.orders()[0]["status"] == "canceled" and ("Kip", CALL) not in kit.risk.positions


def test_an_exit_sells_the_position_at_the_bid_and_books_the_result(kit):
    after_close(kit, D1, decision("ENTER"))
    quote(kit)
    at_open(kit, D2)
    made = after_close(kit, D2, decision("EXIT"))
    assert made[0]["side"] == "SELL" and made[0]["contract"] == CALL
    quote(kit, bid=16.0, ask=16.4, day=D3)
    done = at_open(kit, D3)
    assert done[0]["status"] == "filled" and done[0]["fill_price"] == 16.0
    assert kit.db.position("Kip") is None and ("Kip", CALL) not in kit.risk.positions
    trade = kit.db.trades()[0]
    assert trade["pnl"] == pytest.approx((16.0 - 14.8) * 100) and trade["agent"] == "Kip" and trade["simulated"] == 1


def test_a_stop_or_the_kill_switch_also_queues_a_sale(kit):
    after_close(kit, D1, decision("ENTER"))
    quote(kit)
    at_open(kit, D2)
    made = after_close(kit, D2, decision("NONE", stopped=True))
    assert made[0]["side"] == "SELL" and "stop" in made[0]["reason"]
    kit.db.set_checkpoint("close", D1.isoformat())
    kit.db.db.execute("DELETE FROM live_intent WHERE side='SELL'")
    kit.broker.equity = 89_000.0                                                                    # more than 10% below the peak
    made = after_close(kit, D2, decision("HOLD"))
    assert kit.risk.halted and made[0]["side"] == "SELL" and "kill switch" in made[0]["reason"]


# ---------------------------------------------------------------- reconciliation and restarts

def test_books_that_disagree_with_the_broker_halt_trading(kit):
    after_close(kit, D1, decision("ENTER"))
    quote(kit)
    at_open(kit, D2)
    assert reconcile(kit.broker, kit.db, kit.risk) == []
    kit.broker.positions_held["SPY261120C00670000"] = 2                                             # the broker has something we never bought
    problems = reconcile(kit.broker, kit.db, kit.risk)
    assert any("SPY261120C00670000" in p for p in problems) and kit.risk.halted
    kit.broker.positions_held.clear()
    assert any(CALL in p for p in reconcile(kit.broker, kit.db, kit.risk))                          # and we hold something the broker does not


def test_risk_state_survives_a_restart(kit):
    after_close(kit, D1, decision("ENTER"))
    quote(kit)
    at_open(kit, D2)
    kit.risk.kill("manual test")
    kit.db.save_risk(kit.risk)
    back = kit.db.load_risk(RiskSettings(), 100_000.0)
    assert back.halted and back.halt_reason == "manual test" and ("Kip", CALL) in back.positions
    assert back.peak == kit.risk.peak and back.seen == kit.risk.seen
    from whynot.riskengine import Order
    v = back.check(Order("Kip", "QQQ", "QQQ261120C00610000", "LONG", 1, 5.0, True, D3, D3, 5000, 400))
    assert not v.approved and "halted" in v.reasons[0]


def test_the_regime_walk_can_resume_from_stored_models_and_gives_the_same_history():
    import numpy as np
    from whynot import regime, walkforward
    from whynot.regime import Candle
    mp = pytest.MonkeyPatch()
    mp.setattr(regime, "RESTARTS", 2)
    try:
        rng, price, cs = np.random.default_rng(3), 100.0, []
        for i in range(1100):
            vol = 0.004 if (i // 90) % 2 == 0 else 0.02
            o = price
            price = o * float(np.exp(rng.normal(0.0003, vol)))
            cs.append(Candle(date(2010, 1, 1) + timedelta(days=i), o, max(o, price) * (1 + vol / 2), min(o, price) * (1 - vol / 2), price, float(rng.uniform(1e6, 2e6))))
        first = date(2010, 1, 1) + timedelta(days=909)
        full = walkforward.walk(cs, first)
        calls = []
        mp.setattr(regime, "refit", lambda *a: calls.append(1) or (_ for _ in ()).throw(AssertionError("refitted a stored model")))
        mp.setattr(regime, "select_and_fit", lambda *a: (_ for _ in ()).throw(AssertionError("refitted the first model")))
        again = walkforward.walk(cs, first, known={m.version: m for m in full.models})
        assert [vars(r) for r in again.records] == [vars(r) for r in full.records] and not calls
    finally:
        mp.undo()


def test_two_agents_in_the_same_contract_are_one_position_at_the_broker_and_that_is_not_a_mismatch(kit):
    kit.symbols = {"Kip": "QQQ", "Aura": "QQQ"}
    after_close(kit, D1, decision("ENTER"))
    quote(kit)
    done = at_open(kit, D2)
    assert [d["status"] for d in done] == ["filled", "filled"] and kit.broker.positions() == {CALL: 2}
    assert reconcile(kit.broker, kit.db, kit.risk) == [] and not kit.risk.halted
    after_close(kit, D2, decision("EXIT"))
    quote(kit, bid=16.0, ask=16.4, day=D3)
    at_open(kit, D3)
    assert kit.broker.positions() == {} and kit.db.positions() == [] and len(kit.db.trades()) == 2
