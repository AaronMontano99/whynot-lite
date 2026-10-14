"""Era's wild card on the development years: the data it needs and the one run of it."""

import sqlite3
from datetime import date
from pathlib import Path

from ..ingest.news import NewsStore
from ..ingest.timeutil import et_day, et_time, parse_ts
from ..partitions import DEVELOPMENT_END
from . import stats
from .wildcard import Bar, WildRule, run_wild

NEWS_START = date(2015, 10, 1)          # the first headlines the provider has are from 2015
WILD_START = date(2016, 3, 1)           # prices start in 2016; 60 sessions of news and 20 of prices come first
EQUITY = 20_000.0


class StockDays:
    """Daily prices for the stocks the news names, beside the headlines and away from the trading database."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=60)
        self.db.executescript("CREATE TABLE IF NOT EXISTS stock_day (symbol TEXT, day TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,"
                              " PRIMARY KEY (symbol, day)); CREATE TABLE IF NOT EXISTS stock_done (symbol TEXT PRIMARY KEY, first TEXT, last TEXT);")

    def missing(self, symbols, start: date, end: date) -> list:
        have = {r[0] for r in self.db.execute("SELECT symbol FROM stock_done WHERE first<=? AND last>=?", (start.isoformat(), end.isoformat()))}
        return sorted(set(symbols) - have)

    def ingest(self, client, symbols, start: date, end: date, batch: int = 40, log=None) -> int:
        todo, rows = self.missing(symbols, start, end), 0
        for k in range(0, len(todo), batch):
            part = todo[k:k + batch]
            got = self._fetch(client, part, start, end)
            with self.db:
                self.db.executemany("INSERT OR IGNORE INTO stock_day VALUES (?,?,?,?,?,?,?)", got)      # first seen is kept
                self.db.executemany("INSERT OR REPLACE INTO stock_done VALUES (?,?,?)", [(s, start.isoformat(), end.isoformat()) for s in part])
            rows += len(got)
            if log:
                log(f"prices: {min(k + batch, len(todo))} of {len(todo)} stocks")
        return rows

    def _fetch(self, client, symbols, start: date, end: date) -> list:
        """One request for all of them; if the provider refuses the request, halve it until the symbol it does not know is alone, and skip that one."""
        try:
            return [(s, et_day(parse_ts(b["t"])).isoformat(), b["o"], b["h"], b["l"], b["c"], b["v"])
                    for s, b in client.stock_days(symbols, start.isoformat(), end.isoformat())]
        except Exception:
            if len(symbols) == 1:
                return []
            half = len(symbols) // 2
            return self._fetch(client, symbols[:half], start, end) + self._fetch(client, symbols[half:], start, end)

    def bars(self, through: date) -> dict:
        out = {}
        for s, d, o, h, l, c, v in self.db.execute("SELECT * FROM stock_day WHERE day<=?", (through.isoformat(),)):
            out.setdefault(s, {})[date.fromisoformat(d)] = Bar(o, h, l, c, v)
        return out


def session_closes(cat, start: date, end: date) -> list:
    """(day, the moment that session closed) for every session in the range."""
    return [(date.fromisoformat(d), et_time(date.fromisoformat(d), c))
            for d, c in cat.db.execute("SELECT day, close FROM session WHERE day>=? AND day<=? ORDER BY day", (start.isoformat(), end.isoformat()))]


def counts_by_session(news: NewsStore, closes: list, max_symbols: int = 3) -> dict:
    """For each session, the headlines published after the previous close and up to its own."""
    return {day: news.counts(before, close, max_symbols) for (_, before), (day, close) in zip(closes, closes[1:])}


def symbols_needed(counts: dict, rule: WildRule) -> list:
    return sorted({s for by in counts.values() for s, n in by.items() if n >= rule.min_headlines})


def summary(result, label: str) -> dict:
    c, t = stats.curve(result.equity), stats.trades(result.trades)
    return {"label": label, "trades": t["count"], "total_return": c["total_return"], "cagr": c["cagr"], "sharpe": c["sharpe"],
            "max_drawdown": c["max_drawdown"], "hit_rate": t["hit_rate"], "expectancy": t["expectancy"], "t_stat": t["t_stat"],
            "profit_factor": t["profit_factor"], "worst": t["worst"], "by_year": t["by_year"],
            "long": stats.trades([x for x in result.trades if x.direction == "LONG"]),
            "short": stats.trades([x for x in result.trades if x.direction == "SHORT"])}
