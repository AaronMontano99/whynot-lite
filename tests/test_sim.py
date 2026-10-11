"""Milestone 3: the five agents' deterministic engine and the walk-forward backtest."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from whynot.regime import Candle
from whynot.sim import indicators, stats
from whynot.sim.agents import AGENTS, Agent, Playbook, RULES, write_playbooks
from whynot.sim.daily import HoldoutLocked, run_daily
from whynot.sim.intraday import five_minute, run_intraday
from whynot.sim.ledger import Ledger

ET = ZoneInfo("America/New_York")
D0 = date(2010, 1, 4)


def day(i):
    return D0 + timedelta(days=i)


def flat_candles(n, price=100.0):
    return [Candle(day(i), price, price + 1, price - 1, price, 1e6) for i in range(n)]


def rec(active="CALM_UP", size=1.0, frozen=False, switched=False, danger=0.0):
    return SimpleNamespace(active=active, size=size, frozen=frozen, switched=switched, top=active,
                           probs={active: 0.9}, next_probs={"STRESS": danger, "CRASH": 0.0}, reason="test")


def always(direction):
    """A rule that wants a position every day and never exits by itself."""
    return (lambda ctx: (direction, "test entry")), (lambda ctx: None)


@pytest.fixture
def toy(monkeypatch):
    monkeypatch.setitem(RULES, "always_long", always("LONG"))
    monkeypatch.setitem(RULES, "always_short", always("SHORT"))
    book = Playbook(rule="always_long", stop_pct=0.05, max_days=None)
    return Agent(id="Toy", bot="BOT_0", symbol="TST", timeframe="1Day", version="t1", focus="test",
                 playbooks={"CALM_UP": book, "STRESS": Playbook(rule="always_short", stop_pct=0.05, max_days=None), "CRASH": None},
                 static_state="CALM_UP")


def records(n, **kw):
    return {day(i): rec(**kw) for i in range(n)}


# ---------------------------------------------------------------- indicators

def test_indicators_use_the_past_only_and_are_right():
    closes = [10, 11, 12, 11, 13, 14]
    assert indicators.sma(closes, 3) == pytest.approx((11 + 13 + 14) / 3)
    assert indicators.sma(closes, 7) is None                               # not enough history yet
    assert indicators.rsi(closes, 2) == pytest.approx(100.0)               # two up days
    assert indicators.rsi([10, 11, 12, 11, 10], 2) == pytest.approx(0.0)
    assert indicators.rsi([10, 11, 12, 13, 12], 2) == pytest.approx(50.0)
    assert indicators.highest([1, 5, 3, 9], 2) == 5                        # the 2 bars before the last one


# ---------------------------------------------------------------- the daily engine

def test_a_decision_at_the_close_fills_at_the_next_open_and_pays_costs(toy):
    cs = flat_candles(6)
    cs[3] = Candle(day(3), 102.0, 103.0, 101.0, 102.0, 1e6)
    out = run_daily(toy, cs, records(6), start=day(2), end=day(5), cost_bps=10)
    first = out.decisions[0]
    assert (first.day, first.action) == (day(2), "ENTER")
    pos = out.trades[0] if out.trades else out.open_position
    assert pos.entry_day == day(3) and pos.entry_price == pytest.approx(102.0 * 1.001)     # next open, plus 10 bp


def test_a_stop_fills_at_the_stop_and_a_gap_fills_at_the_open(toy):
    cs = flat_candles(8)
    cs[4] = Candle(day(4), 100.0, 100.5, 94.0, 96.0, 1e6)                 # trades through the 5% stop intraday
    out = run_daily(toy, cs, {**records(8), **{day(i): rec(size=0.0, active="CRASH") for i in range(4, 8)}}, day(2), day(7), cost_bps=0)
    t = out.trades[0]
    assert t.exit_reason == "stop" and t.exit_day == day(4) and t.exit_price == pytest.approx(95.0)
    cs[4] = Candle(day(4), 90.0, 91.0, 89.0, 90.0, 1e6)                   # opens far below the stop
    t = run_daily(toy, cs, {**records(8), **{day(i): rec(size=0.0, active="CRASH") for i in range(4, 8)}}, day(2), day(7), cost_bps=0).trades[0]
    assert t.exit_price == pytest.approx(90.0) and t.exit_reason == "stop"
    assert t.mae == pytest.approx(-0.10) and t.pnl < 0                              # the loss stops at the exit price


def test_crash_rejects_entries_and_closes_what_is_open(toy):
    recs = records(8)
    for i in (4, 5):
        recs[day(i)] = rec(active="CRASH", size=0.0)
    out = run_daily(toy, flat_candles(8), recs, day(2), day(7), cost_bps=0)
    actions = {d.day: (d.action, d.reason) for d in out.decisions}
    assert actions[day(4)][0] == "EXIT" and "CRASH" in actions[day(4)][1]
    assert actions[day(5)][0] == "REJECT" and "CRASH" in actions[day(5)][1]          # the rule wanted in; the state said no
    assert actions[day(6)][0] == "ENTER"
    assert out.trades[0].exit_day == day(5) and out.trades[0].exit_reason.startswith("state")


def test_frozen_blocks_new_entries_but_keeps_the_position(toy):
    recs = records(8)
    for i in (3, 4, 5):
        recs[day(i)] = rec(size=0.0, frozen=True)
    held = run_daily(toy, flat_candles(8), recs, day(2), day(7), cost_bps=0)
    assert not held.trades and held.open_position.entry_day == day(3)
    assert {d.action for d in held.decisions if day(3) <= d.day <= day(5)} == {"HOLD"}
    fresh = run_daily(toy, flat_candles(8), recs, day(3), day(5), cost_bps=0)
    assert {d.action for d in fresh.decisions} == {"REJECT"} and fresh.open_position is None


def test_an_early_cut_halves_the_position(toy):
    recs = records(8)
    recs[day(4)] = rec(size=0.5)
    out = run_daily(toy, flat_candles(8), recs, day(2), day(7), cost_bps=0)
    assert [d.action for d in out.decisions if d.day == day(4)] == ["RESIZE"]
    full = out.equity[0][1] // 100.0
    assert out.open_position.shares == pytest.approx(full // 2, abs=1)
    assert [d.action for d in out.decisions if d.day == day(5)] == ["HOLD"]         # size is never added back mid-trade


def test_the_past_does_not_change_when_the_future_does(toy):
    cs, recs = flat_candles(40), records(40)
    for i in range(40):
        cs[i] = Candle(day(i), 100 + i % 7, 103 + i % 7, 98 + i % 7, 101 + (i * 3) % 7, 1e6)
    base = run_daily(toy, cs, recs, day(2), day(39), cost_bps=5)
    wild = cs[:25] + [Candle(c.day, c.open * 3, c.high * 9, c.low / 2, c.close * 5, c.volume) for c in cs[25:]]
    wild_recs = {**recs, **{day(i): rec(active="CRASH", size=0.0) for i in range(25, 40)}}
    other = run_daily(toy, wild, wild_recs, day(2), day(39), cost_bps=5)
    past = lambda out: [(d.day, d.action, d.reason) for d in out.decisions if d.day < day(24)]
    assert past(base) == past(other) and len(past(base)) == 22
    assert [e for e in base.equity if e[0] < day(25)] == [e for e in other.equity if e[0] < day(25)]
    again = run_daily(toy, cs, recs, day(2), day(39), cost_bps=5)
    assert again.decisions == base.decisions and again.equity == base.equity         # replay is exact


def test_every_day_gets_a_decision_and_a_day_without_a_regime_is_flat(toy):
    recs = records(10)
    del recs[day(5)]
    out = run_daily(toy, flat_candles(10), recs, day(2), day(9), cost_bps=0)
    assert [d.day for d in out.decisions] == [day(i) for i in range(2, 10)]
    missing = next(d for d in out.decisions if d.day == day(5))
    assert missing.action == "EXIT" and "no regime" in missing.reason               # rule 6: stale or missing means flat


def test_the_locked_holdout_cannot_be_backtested(toy):
    cs = [Candle(date(2024, 12, 20) + timedelta(days=i), 100, 101, 99, 100, 1e6) for i in range(30)]
    with pytest.raises(HoldoutLocked):
        run_daily(toy, cs, {}, date(2024, 12, 23), date(2025, 1, 10), cost_bps=0)
    run_daily(toy, cs, {}, date(2024, 12, 23), date(2024, 12, 31), cost_bps=0)       # validation is allowed


def test_ungated_run_ignores_the_regime_for_the_static_baseline(toy):
    recs = {day(i): rec(active="CRASH", size=0.0) for i in range(8)}
    assert run_daily(toy, flat_candles(8), recs, day(2), day(7), cost_bps=0).open_position is None
    static = run_daily(toy, flat_candles(8), recs, day(2), day(7), cost_bps=0, gated=False)
    assert static.open_position is not None and static.open_position.direction == "LONG"


# ---------------------------------------------------------------- the five agents

def test_five_agents_with_a_playbook_for_every_state(tmp_path):
    assert [(a.id, a.bot, a.symbol) for a in AGENTS.values()] == [
        ("Kip", "BOT_1", "QQQ"), ("Nemo", "BOT_2", "SPY"), ("Chud", "BOT_3", "IWM"), ("Era", "BOT_4", "QQQ"), ("Aura", "BOT_5", "QQQ")]
    for a in AGENTS.values():
        assert set(a.playbooks) == {"CALM_UP", "CALM", "CHOP", "STRESS", "CRASH"}
        assert a.playbooks["CRASH"] is None                                          # crash is flat for everyone
        assert all(p.rule in RULES for p in a.playbooks.values() if p)
    write_playbooks(tmp_path)
    text = (tmp_path / "Nemo" / "CHOP.md").read_text()
    for heading in ("Entry", "Exit", "Stop", "Take profit", "Max size", "Invalidation"):
        assert heading in text
    assert "flat" in (tmp_path / "Kip" / "CRASH.md").read_text().lower()


def test_mean_reversion_rule_buys_a_sharp_dip_in_an_uptrend_only():
    entry, _ = RULES["revert"]
    up = [100 + i * 0.5 for i in range(210)]
    ctx = lambda closes: SimpleNamespace(closes=closes, highs=closes, lows=closes, rec=rec(), prev_rec=rec(), pos=None)
    assert entry(ctx(up)) is None                                                    # no dip
    assert entry(ctx(up + [up[-1] - 3, up[-1] - 6]))[0] == "LONG"                    # two hard down days above the 200-day average
    down = [300 - i * 0.5 for i in range(210)]
    assert entry(ctx(down + [down[-1] - 3, down[-1] - 6])) is None                   # a dip in a downtrend is not bought


# ---------------------------------------------------------------- intraday (Agent Era)

def minute_bars(d, path):
    """One regular session of 1-minute bars following a list of (minute, price) turning points."""
    start = int(datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET).timestamp())
    bars, price = [], path[0][1]
    for m in range(390):
        nxt = next((p for mm, p in path if mm == m), None)
        price = nxt if nxt is not None else price
        bars.append((start + 60 * m, price, price + 0.05, price - 0.05, price, 1000))
    return bars


def test_five_minute_bars_are_built_from_whole_minutes_only():
    bars = five_minute(minute_bars(date(2018, 3, 5), [(0, 100.0), (7, 101.0)]))
    assert len(bars) == 78 and bars[0].open == 100.0 and bars[1].close == 101.0
    assert bars[1].high == pytest.approx(101.05) and bars[1].low == pytest.approx(99.95)


def test_opening_range_breakout_enters_on_the_next_bar_stops_at_the_range_and_is_flat_by_the_close():
    era, d = AGENTS["Era"], date(2018, 3, 5)
    recs = {date(2018, 3, 2): rec(), date(2018, 3, 5): rec(active="CRASH", size=0.0)}     # today's own record must not be used
    up = minute_bars(d, [(0, 100.0), (10, 100.4), (20, 99.8), (42, 100.9), (50, 101.2), (200, 102.0)])
    out = run_intraday(era, {d: up}, recs, d, d, cost_bps=0)
    t = out.trades[0]
    assert t.direction == "LONG" and t.entry_price == pytest.approx(100.9) and t.exit_reason == "session end"   # the open of the bar after the breakout
    assert t.exit_price == pytest.approx(102.0) and len(out.trades) == 1
    failed = minute_bars(d, [(0, 100.0), (10, 100.4), (20, 99.8), (42, 100.9), (50, 101.2), (122, 99.0)])
    t = run_intraday(era, {d: failed}, recs, d, d, cost_bps=0).trades[0]
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(99.75)           # the low of the opening range
    quiet = run_intraday(era, {d: minute_bars(d, [(0, 100.0)])}, recs, d, d, cost_bps=0)
    assert not quiet.trades and quiet.decisions[-1].action == "NONE"


def test_intraday_uses_yesterdays_regime_and_stands_aside_in_a_crash():
    era, d = AGENTS["Era"], date(2018, 3, 5)
    up = minute_bars(d, [(0, 100.0), (10, 100.4), (20, 99.8), (42, 100.9), (50, 101.2)])
    out = run_intraday(era, {d: up}, {date(2018, 3, 2): rec(active="CRASH", size=0.0)}, d, d, cost_bps=0)
    assert not out.trades and any(x.action == "REJECT" and "CRASH" in x.reason for x in out.decisions)


# ---------------------------------------------------------------- statistics and the ledger

def test_statistics_on_a_known_series():
    equity = [(day(i), v) for i, v in enumerate([100, 110, 99, 108.9, 108.9])]
    s = stats.curve(equity)
    assert s["total_return"] == pytest.approx(0.089) and s["max_drawdown"] == pytest.approx(0.10)
    trades = [SimpleNamespace(ret=r, pnl=r * 100, state="CALM_UP", entry_day=day(i)) for i, r in enumerate([0.02, -0.01, 0.03, -0.01])]
    t = stats.trades(trades)
    assert t["count"] == 4 and t["hit_rate"] == 0.5 and t["expectancy"] == pytest.approx(0.0075)
    assert t["profit_factor"] == pytest.approx(2.5) and t["payoff"] == pytest.approx(2.5)
    assert t["t_stat"] == pytest.approx(0.0075 / (0.02061552812808830 / 2), rel=1e-6)
    gates = stats.gates({"sharpe": 1.6, "max_drawdown": 0.1}, {"hit_rate": 0.5, "t_stat": 2.5, "count": 40}, beats_hold=True, beats_static=True)
    assert gates["sharpe"] and gates["max_drawdown"] and gates["t_stat"] and not gates["hit_rate"] and not gates["all"]


def test_runs_are_kept_side_by_side_and_never_overwritten(toy, tmp_path):
    ledger = Ledger(tmp_path / "w.db")
    out = run_daily(toy, flat_candles(8), records(8) | {day(6): rec(active="CRASH", size=0.0)}, day(2), day(7), cost_bps=0)
    a = ledger.save(toy, out, partition="DEVELOPMENT", label="first", params={"cost_bps": 0})
    b = ledger.save(toy, out, partition="DEVELOPMENT", label="again", params={"cost_bps": 0})
    assert a != b and len(ledger.runs("Toy")) == 2
    assert len(ledger.decisions(a)) == len(out.decisions) and len(ledger.trades(a)) == len(out.trades) == 1
    row = ledger.trades(a)[0]
    assert row["state"] == "CALM_UP" and row["strategy_version"] == "t1" and row["exit_reason"].startswith("state")
    assert any(d["action"] == "REJECT" for d in ledger.decisions(a)) or any(d["action"] == "EXIT" for d in ledger.decisions(a))
