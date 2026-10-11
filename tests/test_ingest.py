"""Milestone 1: historical data ingestion and validation. No test touches the network."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from whynot.ingest import validate
from whynot.ingest.alpaca import Alpaca, AlpacaError
from whynot.ingest.catalog import Catalog, ProxyDataError
from whynot.ingest.pipeline import ingest_daily, ingest_option_bars, report
from whynot.ingest.timeutil import daily_available_at, epoch, occ_parse

ET = ZoneInfo("America/New_York")
SESSIONS = [("2024-11-26", "09:30", "16:00"), ("2024-11-27", "09:30", "16:00"),
            ("2024-11-29", "09:30", "13:00"), ("2024-12-02", "09:30", "16:00")]   # the 28th is Thanksgiving


def et(y, m, d, hh=0, mm=0):
    return epoch(datetime(y, m, d, hh, mm, tzinfo=ET))


@pytest.fixture
def cat(tmp_path):
    c = Catalog(tmp_path / "whynot.db")
    c.set_calendar(SESSIONS, source="test")
    return c


def daily_rows(cat, days, close=100.0):
    return [(et(*d), close, close + 1, close - 1, close, 1000, close, 10, daily_available_at(cat, date(*d))) for d in days]


def spy(cat, **kw):
    spec = dict(category="ETF_HISTORY", symbol="SPY", timeframe="1Day", source="alpaca", authentic=True, adjustment="raw")
    spec.update(kw)
    return cat.dataset(**spec)


# ---------------------------------------------------------------- the catalog

def test_dataset_records_source_category_and_adjustment(cat):
    ds = spy(cat)
    assert spy(cat) == ds                                   # the same description is the same dataset
    d = cat.describe(ds)
    assert (d["category"], d["source"], d["adjustment"], d["authentic"]) == ("ETF_HISTORY", "alpaca", "raw", 1)
    with pytest.raises(ValueError):
        cat.dataset(category="MADE_UP", symbol="SPY", timeframe="1Day", source="x", authentic=True, adjustment="raw")


def test_ingesting_twice_adds_nothing_and_keeps_the_first_value(cat):
    ds = spy(cat)
    rows = daily_rows(cat, [(2024, 11, 26), (2024, 11, 27)])
    assert cat.add_bars(ds, rows) == 2
    assert cat.add_bars(ds, rows) == 0
    revised = [(r[0], r[1], r[2], r[3], 105.0, *r[5:]) for r in rows[:1]]     # the provider later changes a close
    assert cat.add_bars(ds, revised) == 0
    assert [b["close"] for b in cat.bars(ds)] == [100.0, 100.0]               # history is not rewritten
    rev = cat.revisions(ds)
    assert len(rev) == 1 and (rev[0]["old_close"], rev[0]["new_close"]) == (100.0, 105.0)


def test_a_daily_bar_is_available_only_at_that_sessions_close(cat):
    assert daily_available_at(cat, date(2024, 11, 27)) == et(2024, 11, 27, 16, 0)
    assert daily_available_at(cat, date(2024, 11, 29)) == et(2024, 11, 29, 13, 0)        # early close


def test_reader_never_returns_a_bar_before_it_was_available(cat):
    ds = spy(cat)
    cat.add_bars(ds, daily_rows(cat, [(2024, 11, 26), (2024, 11, 27)]))
    during_session = et(2024, 11, 27, 15, 59)
    assert [b["ts"] for b in cat.bars(ds, as_of=during_session)] == [et(2024, 11, 26)]
    assert len(cat.bars(ds, as_of=et(2024, 11, 27, 16, 0))) == 2


def test_rows_must_say_when_they_became_available(cat):
    ds = spy(cat)
    ts = et(2024, 11, 26)
    with pytest.raises(ValueError):
        cat.add_bars(ds, [(ts, 1, 2, 0.5, 1, 10, 1, 1, ts - 5)])                   # known before it happened


def test_proxy_data_is_labeled_and_refused_unless_asked_for(cat):
    ds = cat.dataset(category="LONG_TERM_MARKET_HISTORY", symbol="^GSPC", timeframe="1Day", source="yahoo",
                     authentic=True, adjustment="raw", proxy_for="SPY")
    cat.add_bars(ds, daily_rows(cat, [(2024, 11, 26)]))
    with pytest.raises(ProxyDataError):
        cat.bars(ds)
    assert len(cat.bars(ds, allow_proxy=True)) == 1
    assert cat.describe(ds)["proxy_for"] == "SPY"


def test_nothing_is_returned_for_a_period_before_the_data_exists(cat):
    ds = spy(cat)
    cat.add_bars(ds, daily_rows(cat, [(2024, 11, 26)]))
    assert cat.bars(ds, end=et(1987, 10, 19)) == []
    assert cat.coverage(ds)["first"] == et(2024, 11, 26)


def test_integrity_check_passes_on_a_healthy_catalog(cat):
    assert cat.integrity_ok()


# ---------------------------------------------------------------- validation

def test_ohlc_check_flags_impossible_bars(cat):
    good = daily_rows(cat, [(2024, 11, 26)])[0]
    bad_high = (good[0] + 86400, 100, 99, 98, 100, 10, 100, 1, good[8] + 86400)     # high below open
    negative = (good[0] + 2 * 86400, -1, 1, -2, 1, 10, 1, 1, good[8] + 2 * 86400)
    problems = validate.ohlc([good, bad_high, negative])
    assert {p["ts"] for p in problems} == {bad_high[0], negative[0]}


def test_missing_trading_day_and_bar_on_a_holiday_are_flagged(cat):
    days = [date(2024, 11, 26), date(2024, 11, 28), date(2024, 12, 2)]              # no 27th or 29th; a bar on the holiday
    gaps = validate.daily_calendar(days, cat.sessions("2024-11-26", "2024-12-02"))
    assert sorted((g["day"], g["kind"]) for g in gaps) == [
        ("2024-11-27", "missing_session"), ("2024-11-28", "not_a_session"), ("2024-11-29", "missing_session")]


def test_missing_minutes_are_counted_against_the_real_session_length(cat):
    full = [et(2024, 11, 29, 9, 30) + 60 * i for i in range(210)]                   # early close: 210 minutes
    assert validate.minutes(full, cat.sessions("2024-11-29", "2024-11-29")) == []
    gaps = validate.minutes(full[:200], cat.sessions("2024-11-29", "2024-11-29"))
    assert gaps == [{"day": "2024-11-29", "kind": "missing_minutes", "expected": 210, "found": 200}]


def test_unexplained_jump_is_flagged_and_a_split_explains_it(cat):
    rows = daily_rows(cat, [(2024, 11, 26)], close=100.0) + daily_rows(cat, [(2024, 11, 27)], close=50.0)
    assert [o["day"] for o in validate.outliers(rows, splits={})] == ["2024-11-27"]
    assert validate.outliers(rows, splits={"2024-11-27": 2.0}) == []


def test_cross_source_disagreement_is_flagged(cat):
    a = {"2024-11-26": 100.0, "2024-11-27": 101.0}
    b = {"2024-11-26": 100.1, "2024-11-27": 104.0, "2024-12-02": 99.0}
    assert validate.cross_source(a, b, tolerance=0.005) == [{"day": "2024-11-27", "a": 101.0, "b": 104.0}]


# ---------------------------------------------------------------- the Alpaca client

class FakeTransport:
    """Serves canned pages and records what was asked for."""

    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def __call__(self, url, params, headers):
        self.calls.append((url, dict(params)))
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def bar(day, close=100.0):
    return {"t": datetime(*day, tzinfo=ET).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 1000, "vw": close, "n": 10}


def test_client_follows_pages_until_there_are_none():
    t = FakeTransport([{"bars": [bar((2024, 11, 26))], "next_page_token": "abc"},
                       {"bars": [bar((2024, 11, 27))], "next_page_token": None}])
    got = list(Alpaca("KEYID", "SECRET", transport=t).stock_bars("SPY", "1Day", "2024-11-26", "2024-11-28"))
    assert len(got) == 2 and t.calls[1][1]["page_token"] == "abc"
    assert t.calls[0][1]["adjustment"] == "raw"                                    # unadjusted, always


def test_client_errors_never_contain_the_keys():
    boom = RuntimeError("403 for https://x?key=SECRETVALUE with header KEYIDVALUE")
    client = Alpaca("KEYIDVALUE", "SECRETVALUE", transport=FakeTransport([boom]))
    with pytest.raises(AlpacaError) as err:
        list(client.stock_bars("SPY", "1Day", "2024-11-26", "2024-11-28"))
    assert "SECRETVALUE" not in str(err.value) and "KEYIDVALUE" not in str(err.value)
    assert "SECRETVALUE" not in repr(client) and "KEYIDVALUE" not in repr(client)


# ---------------------------------------------------------------- the pipeline

def test_daily_ingest_resumes_after_the_last_stored_bar(cat):
    t = FakeTransport([{"bars": [bar((2024, 11, 26)), bar((2024, 11, 27))], "next_page_token": None},
                       {"bars": [bar((2024, 11, 27)), bar((2024, 11, 29))], "next_page_token": None}])
    client = Alpaca("K", "S", transport=t)
    ds = ingest_daily(cat, client, "SPY", start="2016-01-01", end="2024-11-27")
    ingest_daily(cat, client, "SPY", start="2016-01-01", end="2024-11-29")
    assert t.calls[1][1]["start"].startswith("2024-11-27")                        # not from 2016 again
    assert len(cat.bars(ds)) == 3
    runs = cat.runs(ds)
    assert [r["status"] for r in runs] == ["ok", "ok"] and [r["rows"] for r in runs] == [2, 1]
    assert cat.bars(ds)[-1]["available_at"] == et(2024, 11, 29, 13, 0)


def test_a_failed_ingest_is_recorded_and_stores_nothing_partial(cat):
    t = FakeTransport([RuntimeError("network down")])
    with pytest.raises(AlpacaError):
        ingest_daily(cat, Alpaca("K", "S", transport=t), "SPY", start="2016-01-01", end="2024-11-27")
    ds = spy(cat)
    assert cat.bars(ds) == [] and cat.runs(ds)[0]["status"] == "failed"


def test_occ_symbols_are_parsed_exactly():
    assert occ_parse("SPY240315C00500000") == ("SPY", "2024-03-15", "call", 500.0)
    assert occ_parse("QQQ240104P00346780") == ("QQQ", "2024-01-04", "put", 346.78)
    with pytest.raises(ValueError):
        occ_parse("SPY-not-an-option")


def test_option_bars_are_stored_only_for_contracts_that_really_traded(cat):
    cat.add_contracts([("SPY240315C00500000", "SPY", "call", 500.0, "2024-03-15", "inactive"),
                       ("SPY240315C00900000", "SPY", "call", 900.0, "2024-03-15", "inactive")], source="alpaca")
    t = FakeTransport([{"bars": {"SPY240315C00500000": [bar((2024, 3, 4), 13.9)]}, "next_page_token": None}])
    ingest_option_bars(cat, Alpaca("K", "S", transport=t), ["SPY240315C00500000", "SPY240315C00900000"],
                       start="2024-03-01", end="2024-03-15")
    assert [b["close"] for b in cat.option_bars("SPY240315C00500000")] == [13.9]
    assert cat.option_bars("SPY240315C00900000") == []                            # no trade, no invented price


def test_report_separates_authentic_data_from_proxies_and_lists_gaps(cat):
    ds = spy(cat)
    cat.add_bars(ds, daily_rows(cat, [(2024, 11, 26), (2024, 11, 29)]))
    px = cat.dataset(category="LONG_TERM_MARKET_HISTORY", symbol="^GSPC", timeframe="1Day", source="yahoo",
                     authentic=True, adjustment="raw", proxy_for="SPY")
    cat.add_bars(px, daily_rows(cat, [(2024, 11, 26)]))
    cat.add_gaps(ds, [{"day": "2024-11-27", "kind": "missing_session"}])
    text = report(cat)
    assert "ETF_HISTORY" in text and "LONG_TERM_MARKET_HISTORY" in text
    assert "proxy for SPY" in text and "2024-11-27" in text and "missing_session" in text


def test_a_contract_with_no_trades_at_all_is_an_empty_answer_not_an_error(cat):
    t = FakeTransport([{"bars": {}, "next_page_token": None}])
    assert ingest_option_bars(cat, Alpaca("K", "S", transport=t), ["SPY240315C00900000"], "2024-03-01", "2024-03-15") == 0


def test_a_request_for_todays_option_bars_does_not_reach_into_the_restricted_last_minutes(cat):
    """Found on the first live close: Alpaca answers 403 if the range ends after 'now minus 15 minutes'."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    t = FakeTransport([{"bars": {}, "next_page_token": None}, {"bars": {}, "next_page_token": None}])
    client = Alpaca("K", "S", transport=t)
    ingest_option_bars(cat, client, ["SPY240315C00500000"], today, today)
    ingest_option_bars(cat, client, ["SPY240315C00500000"], "2024-03-01", "2024-03-15")
    assert "end" not in t.calls[0][1] and t.calls[1][1]["end"] == "2024-03-15T23:59:59Z"
