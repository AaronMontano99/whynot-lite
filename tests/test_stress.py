"""Stress environments: real crisis price paths where the data is on this machine, and
synthetic shocks that need no data. These check mechanics, never performance."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from whynot.ops import stress
from whynot.regime import Candle

DB = Path("data/whynot.db")
D0 = date(2010, 1, 4)


def path_of(prices):
    """Candles from (open, high, low, close) rows on consecutive days."""
    return [Candle(D0 + timedelta(days=i), o, h, l, c, 1e6) for i, (o, h, l, c) in enumerate(prices)]


def test_a_flash_crash_inside_one_day_fills_at_the_stop_not_at_the_bottom():
    cs = path_of([(100, 101, 99, 100)] * 3 + [(100, 100.5, 70, 99.5)] + [(100, 101, 99, 100)] * 3)      # a 30% wick that recovers by the close
    r = stress.replay(cs, cs[0].day, cs[-1].day, cost_bps=0)
    stopped = [t for t in r.trades if t.exit_reason == "stop"]
    assert len(stopped) == 1 and stopped[0].exit_price == pytest.approx(95.0) and stopped[0].ret == pytest.approx(-0.05)
    assert stress.violations(r, cs, cost_bps=0) == []


def test_a_gap_through_the_stop_fills_at_the_open_and_the_loss_is_the_gap():
    cs = path_of([(100, 101, 99, 100)] * 3 + [(78, 80, 75, 79)] + [(79, 80, 78, 79)] * 3)                # opens 22% lower, as in October 1987
    r = stress.replay(cs, cs[0].day, cs[-1].day, cost_bps=0)
    t = r.trades[0]
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(78.0) and t.ret == pytest.approx(-0.22)
    assert stress.violations(r, cs, cost_bps=0) == []


def test_a_trading_halt_is_days_without_candles_and_the_engine_simply_resumes():
    cs = path_of([(100, 101, 99, 100)] * 10)
    halted = cs[:4] + cs[8:]                                                                              # four sessions never happened
    r = stress.replay(halted, halted[0].day, halted[-1].day, cost_bps=0)
    assert [d.day for d in r.decisions] == [c.day for c in halted] and stress.violations(r, halted, cost_bps=0) == []
    assert r.open_position is not None and r.open_position.entry_day == halted[1].day                     # it held through the gap in the calendar


def test_the_checker_itself_catches_a_wrong_fill():
    cs = path_of([(100, 101, 99, 100)] * 3 + [(78, 80, 75, 79)] + [(79, 80, 78, 79)] * 3)
    r = stress.replay(cs, cs[0].day, cs[-1].day, cost_bps=0)
    r.trades[0].exit_price = 95.0                                                                         # pretend the stop filled at its price despite the gap
    assert any("gapped" in v for v in stress.violations(r, cs, cost_bps=0))


@pytest.mark.skipif(not DB.exists(), reason="needs the downloaded history")
@pytest.mark.parametrize("name,symbol,start,end,note", stress.PERIODS, ids=[p[0] for p in stress.PERIODS])
def test_mechanics_hold_through_each_real_crisis(name, symbol, start, end, note):
    from whynot.ingest.catalog import Catalog
    cs = stress.candles_for(Catalog(str(DB)), symbol)
    assert cs[0].day <= start, f"{symbol} has no data for {name}"                                         # only where the instrument existed
    r = stress.replay(cs, start, end)
    assert len(r.equity) > 50 and stress.violations(r, cs) == []
    assert any(t.exit_reason == "stop" for t in r.trades)                                                 # every one of these periods hit the stop


@pytest.mark.skipif(not DB.exists(), reason="needs the downloaded history")
def test_instruments_are_not_used_before_they_existed():
    from whynot.ingest.catalog import Catalog, ProxyDataError
    cat = Catalog(str(DB))
    assert stress.candles_for(cat, "QQQ")[0].day >= date(1999, 3, 10) and stress.candles_for(cat, "SPY")[0].day >= date(1993, 1, 29)
    with pytest.raises(ProxyDataError):
        cat.bars(cat.find("^GSPC", "1Day", "yahoo"))                                                      # the 1987 stand-in must be asked for by name
