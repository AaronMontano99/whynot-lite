"""The news store: headlines are data with a publication time, counted only up to the moment of a decision."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from whynot.ingest.news import NewsStore, ingest_news
from whynot.ingest.timeutil import epoch

ET = ZoneInfo("America/New_York")


def at(d, hh, mm=0):
    return epoch(datetime(2019, 6, d, hh, mm, tzinfo=ET))


def item(i, ts, symbols, headline="x"):
    stamp = datetime.fromtimestamp(ts, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "created_at": stamp, "updated_at": stamp, "symbols": symbols, "headline": headline, "source": "benzinga"}


def test_the_first_version_of_a_headline_is_kept(tmp_path):
    s = NewsStore(tmp_path / "news.db")
    assert s.add([item(1, at(3, 10), ["AAA"], "first")]) == 1
    assert s.add([item(1, at(3, 15), ["AAA", "BBB"], "rewritten later")]) == 0
    assert s.counts(at(3, 9), at(3, 16)) == {"AAA": 1}


def test_counts_stop_at_the_decision_time_and_skip_roundups(tmp_path):
    s = NewsStore(tmp_path / "news.db")
    s.add([item(1, at(3, 10), ["AAA"]), item(2, at(3, 16), ["AAA"]), item(3, at(3, 16, 1), ["AAA"]),
           item(4, at(3, 9), ["AAA"]),                                    # at the start of the window: belongs to the one before
           item(5, at(3, 11), ["AAA", "BBB", "CCC", "DDD"]),              # a roundup that names four
           item(6, at(3, 12), ["BBB", "AAA"])])
    assert s.counts(at(3, 9), at(3, 16)) == {"AAA": 3, "BBB": 1}
    assert s.counts(at(3, 9), at(3, 16), max_symbols=4) == {"AAA": 4, "BBB": 2, "CCC": 1, "DDD": 1}


class FakeClient:
    def __init__(self):
        self.asked = []

    def news(self, start, end):
        self.asked.append(start[:10])
        yield item(int(start[8:10]), at(int(start[8:10]), 12), ["AAA"])


def test_a_download_resumes_where_it_stopped(tmp_path):
    s, c = NewsStore(tmp_path / "news.db"), FakeClient()
    assert ingest_news(c, s, date(2019, 6, 3), date(2019, 6, 5)) == 3
    assert c.asked == ["2019-06-03", "2019-06-04", "2019-06-05"]
    assert ingest_news(c, s, date(2019, 6, 3), date(2019, 6, 6)) == 1
    assert c.asked[-1] == "2019-06-06" and len(c.asked) == 4
    assert s.covered() == (date(2019, 6, 3), date(2019, 6, 6))


def test_the_latest_day_is_asked_again_because_it_may_not_have_been_over(tmp_path):
    s, c = NewsStore(tmp_path / "news.db"), FakeClient()
    ingest_news(c, s, date(2019, 6, 3), date(2019, 6, 4), today=date(2019, 6, 4))
    ingest_news(c, s, date(2019, 6, 3), date(2019, 6, 4), today=date(2019, 6, 5))
    assert c.asked == ["2019-06-03", "2019-06-04", "2019-06-04"]
