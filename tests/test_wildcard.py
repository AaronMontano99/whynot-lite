"""Era's wild card: news heat on single stocks. Everything here is made-up data; no network."""

from datetime import date, timedelta

import pytest

from whynot.sim.daily import HoldoutLocked
from whynot.sim.wildcard import Bar, WildRule, candidates, heat, run_wild

RULE = WildRule(baseline_days=5, liquidity_days=3)


def sessions(n, first=date(2018, 1, 1)):
    out, d = [], first
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def flat(days, price=100.0, volume=1_000_000):
    return {d: Bar(price, price + 1, price - 1, price, volume) for d in days}


def world(n=12, hot_day=6, move=0.05):
    """One quiet stock that on `hot_day` gets ten headlines and jumps."""
    days = sessions(n)
    bars = {"HOT": flat(days), "DULL": flat(days)}
    jump = 100 * (1 + move)
    bars["HOT"][days[hot_day]] = Bar(100, max(jump, 100) + 1, min(jump, 100) - 1, jump, 1_000_000)
    for d in days[hot_day + 1:]:
        bars["HOT"][d] = Bar(jump, jump + 1, jump - 1, jump, 1_000_000)
    counts = {d: {"DULL": 20} for d in days}                      # always in the news: ordinary for it
    counts[days[hot_day]]["HOT"] = 10
    return days, counts, bars


def test_heat_is_measured_against_the_stocks_own_usual():
    assert heat(20, [20] * 60) < 1
    assert heat(10, [0] * 60) == 10
    assert heat(10, []) == 0


def test_a_stock_qualifies_only_with_a_burst_of_news_and_a_real_move():
    days, counts, bars = world()
    found = candidates(6, days, counts, bars, RULE)
    assert [c["symbol"] for c in found] == ["HOT"] and found[0]["direction"] == "LONG"
    assert candidates(5, days, counts, bars, RULE) == []                         # the day before: nothing yet
    assert candidates(6, *world(move=0.01), RULE) == []                          # news but no move
    assert candidates(6, *world(move=-0.05), RULE)[0]["direction"] == "SHORT"
    cheap = world()
    cheap[2]["HOT"] = {d: Bar(b.open / 20, b.high / 20, b.low / 20, b.close / 20, b.volume) for d, b in cheap[2]["HOT"].items()}
    assert candidates(6, *cheap, RULE) == []                                     # under ten dollars
    thin = world()
    thin[2]["HOT"] = {d: Bar(b.open, b.high, b.low, b.close, 1000) for d, b in thin[2]["HOT"].items()}
    assert candidates(6, *thin, RULE) == []                                      # too little trading


def test_the_order_fills_at_the_next_open_and_leaves_after_the_holding_time():
    days, counts, bars = world()
    r = run_wild(days, counts, bars, days[0], days[-1], RULE, equity=20000.0)
    (t,) = r.trades
    assert t.symbol == "HOT" and t.entry_day == days[7] and t.exit_day == days[10]      # decided on 6; three sessions held
    assert t.entry_price == pytest.approx(105 * 1.0005) and t.exit_price == pytest.approx(105 * 0.9995)
    assert t.shares == int(10000 / t.entry_price) and t.pnl < 0                         # flat price: only the costs are lost
    assert r.equity[-1][1] == pytest.approx(20000 + t.pnl)
    assert [d.action for d in r.decisions if d.day == days[6]] == ["ENTER"]


def test_a_stop_fills_at_the_stop_or_at_the_open_when_the_price_gaps_through():
    days, counts, bars = world()
    bars["HOT"][days[8]] = Bar(104, 105, 97, 98, 1_000_000)                    # trades down through the stop during the day
    (t,) = run_wild(days, counts, bars, days[0], days[-1], RULE).trades
    stop = 105 * 1.0005 * 0.94
    assert t.exit_reason == "stop" and t.exit_day == days[8] and t.exit_price == pytest.approx(stop * (1 - 0.0005 * 2))
    bars["HOT"][days[8]] = Bar(90, 91, 89, 90, 1_000_000)                      # opens below it
    (t,) = run_wild(days, counts, bars, days[0], days[-1], RULE).trades
    assert t.exit_price == pytest.approx(90 * (1 - 0.0005 * 2))


def test_changing_the_future_changes_nothing_already_decided():
    days, counts, bars = world(n=20)
    before = run_wild(days, counts, bars, days[0], days[9], RULE)
    for d in days[10:]:
        bars["HOT"][d] = Bar(1, 2, 0.5, 1, 5)
        counts[d] = {"HOT": 999, "DULL": 0}
    after = run_wild(days, counts, bars, days[0], days[-1], RULE)
    cut = lambda r: ([(d.day, d.action, d.reason) for d in r.decisions if d.day <= days[9]], [e for e in r.equity if e[0] <= days[9]])
    assert cut(before) == cut(after)


def test_never_more_than_the_allowed_positions_and_never_twice_in_one_stock():
    days, counts, bars = world(n=14)
    for name in ("B", "C"):
        bars[name] = dict(bars["HOT"])
    for d in days[6:9]:
        counts[d] = {"HOT": 10, "B": 9, "C": 8}
        for name in ("HOT", "B", "C"):
            b = bars[name][d]
            bars[name][d] = Bar(b.open, b.high, b.low, bars[name][days[days.index(d) - 1]].close * 1.05, b.volume)
    counts[days[8]]["C"] = 40                                                   # still hot on the day both places are taken
    r = run_wild(days, counts, bars, days[0], days[-1], RULE)
    entered = [d.reason.split(":")[0] for d in r.decisions if d.action == "ENTER"]
    assert entered[:2] == ["HOT", "B"] and any(d.action == "REJECT" and "already open" in d.reason for d in r.decisions)


def test_a_stock_with_no_price_at_the_open_is_not_bought():
    days, counts, bars = world()
    del bars["HOT"][days[7]]
    r = run_wild(days, counts, bars, days[0], days[-1], RULE)
    assert r.trades == [] and any(d.action == "REJECT" and "no price" in d.reason for d in r.decisions)


def test_the_locked_holdout_is_refused():
    days, counts, bars = world()
    with pytest.raises(HoldoutLocked):
        run_wild(days, counts, bars, date(2024, 6, 1), date(2025, 6, 1), RULE)


# ---------------------------------------------------------------- the data behind it

def test_headlines_are_counted_from_one_close_to_the_next(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from whynot.ingest.news import NewsStore
    from whynot.ingest.timeutil import epoch
    from whynot.sim.wild_run import counts_by_session
    ET = ZoneInfo("America/New_York")
    at = lambda d, hh, mm=0: epoch(datetime(2019, 6, d, hh, mm, tzinfo=ET))
    stamp = lambda ts: datetime.fromtimestamp(ts, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    news = NewsStore(tmp_path / "news.db")
    news.add([{"id": i, "created_at": stamp(ts), "symbols": ["AAA"]} for i, ts in enumerate(
        [at(3, 15), at(3, 17), at(4, 8), at(4, 16), at(4, 16, 5), at(8, 10)])])          # the 8th is a Saturday
    closes = [(date(2019, 6, 3), at(3, 16)), (date(2019, 6, 4), at(4, 16)), (date(2019, 6, 5), at(5, 13)), (date(2019, 6, 10), at(10, 16))]
    got = counts_by_session(news, closes)
    assert got[date(2019, 6, 4)] == {"AAA": 3}            # after Monday's close, through Tuesday's close
    assert got[date(2019, 6, 5)] == {"AAA": 1}            # five past four on Tuesday belongs to Wednesday
    assert got[date(2019, 6, 10)] == {"AAA": 1}           # the weekend's news is known by Monday's close
    assert date(2019, 6, 3) not in got                    # no earlier close to count from


def test_prices_are_downloaded_once_and_the_first_value_is_kept(tmp_path):
    from whynot.sim.wild_run import StockDays

    class Client:
        calls = 0

        def stock_days(self, symbols, start, end):
            Client.calls += 1
            for s in symbols:
                yield s, {"t": "2018-01-03T05:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5 + Client.calls, "v": 100}

    store = StockDays(tmp_path / "news.db")
    assert store.ingest(Client(), ["AAA", "BBB"], date(2018, 1, 1), date(2018, 12, 31)) == 2
    assert store.ingest(Client(), ["AAA", "BBB"], date(2018, 1, 1), date(2018, 12, 31)) == 0 and Client.calls == 1
    store.ingest(Client(), ["AAA", "CCC"], date(2018, 1, 1), date(2018, 12, 31))
    bars = store.bars(date(2018, 12, 31))
    assert bars["AAA"][date(2018, 1, 3)].close == 11.5 and set(bars) == {"AAA", "BBB", "CCC"}
    assert store.bars(date(2018, 1, 2)) == {}


def test_a_symbol_the_provider_refuses_does_not_lose_the_others(tmp_path):
    from whynot.sim.wild_run import StockDays

    class Client:
        def stock_days(self, symbols, start, end):
            if "BAD/X" in symbols:
                raise RuntimeError("HTTP 400")
            for s in symbols:
                yield s, {"t": "2018-01-03T05:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100}

    store = StockDays(tmp_path / "news.db")
    assert store.ingest(Client(), ["AAA", "BAD/X", "BBB", "CCC"], date(2018, 1, 1), date(2018, 12, 31)) == 3
    assert set(store.bars(date(2018, 12, 31))) == {"AAA", "BBB", "CCC"}
