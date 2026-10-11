"""The catalog: one SQLite file holding every bar with its source, its adjustment status
and the moment it became available, plus the market calendar, corporate actions, gaps,
quality checks and the record of every download."""

import sqlite3
import time
from datetime import date
from pathlib import Path

CATEGORIES = ("LONG_TERM_MARKET_HISTORY", "ETF_HISTORY", "OPTIONS_HISTORY", "LIVE_MARKET_DATA")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dataset (
  id INTEGER PRIMARY KEY, category TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
  source TEXT NOT NULL, authentic INTEGER NOT NULL, adjustment TEXT NOT NULL, proxy_for TEXT, note TEXT DEFAULT '',
  UNIQUE (symbol, timeframe, source, adjustment));
CREATE TABLE IF NOT EXISTS bar (
  dataset_id INTEGER NOT NULL, ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL,
  volume REAL, vwap REAL, trades INTEGER, available_at INTEGER NOT NULL,
  PRIMARY KEY (dataset_id, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS revision (
  dataset_id INTEGER NOT NULL, ts INTEGER NOT NULL, seen_at INTEGER NOT NULL, old_close REAL, new_close REAL,
  PRIMARY KEY (dataset_id, ts, new_close));
CREATE TABLE IF NOT EXISTS session (day TEXT PRIMARY KEY, open TEXT NOT NULL, close TEXT NOT NULL, source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS corporate_action (
  symbol TEXT NOT NULL, ex_date TEXT NOT NULL, kind TEXT NOT NULL, value REAL, source TEXT NOT NULL,
  PRIMARY KEY (symbol, ex_date, kind));
CREATE TABLE IF NOT EXISTS gap (
  dataset_id INTEGER NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, expected INTEGER, found INTEGER,
  PRIMARY KEY (dataset_id, day, kind));
CREATE TABLE IF NOT EXISTS quality (
  dataset_id INTEGER NOT NULL, name TEXT NOT NULL, passed INTEGER NOT NULL, detail TEXT, checked_at INTEGER NOT NULL,
  PRIMARY KEY (dataset_id, name));
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL, started INTEGER NOT NULL, finished INTEGER,
  requested_start TEXT, requested_end TEXT, rows INTEGER DEFAULT 0, status TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS contract (
  symbol TEXT PRIMARY KEY, underlying TEXT NOT NULL, kind TEXT NOT NULL, strike REAL NOT NULL,
  expiration TEXT NOT NULL, status TEXT, source TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS contract_by_expiry ON contract (underlying, expiration);
CREATE TABLE IF NOT EXISTS option_bar (
  contract TEXT NOT NULL, timeframe TEXT NOT NULL, ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL,
  volume REAL, vwap REAL, trades INTEGER, available_at INTEGER NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (contract, timeframe, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS finding (topic TEXT PRIMARY KEY, text TEXT NOT NULL, checked_at INTEGER NOT NULL);
"""


class ProxyDataError(Exception):
    """A stand-in series was asked for without saying a stand-in is acceptable."""


class Catalog:
    def __init__(self, path="data/whynot.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # ---- datasets

    def dataset(self, category, symbol, timeframe, source, authentic, adjustment, proxy_for=None, note="") -> int:
        if category not in CATEGORIES:
            raise ValueError(f"unknown category {category!r}")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO dataset (category, symbol, timeframe, source, authentic, adjustment, proxy_for, note)"
                " VALUES (?,?,?,?,?,?,?,?)", (category, symbol, timeframe, source, int(authentic), adjustment, proxy_for, note))
        return self.db.execute("SELECT id FROM dataset WHERE symbol=? AND timeframe=? AND source=? AND adjustment=?",
                               (symbol, timeframe, source, adjustment)).fetchone()[0]

    def describe(self, ds: int) -> dict:
        return dict(self.db.execute("SELECT * FROM dataset WHERE id=?", (ds,)).fetchone())

    def datasets(self) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM dataset ORDER BY category, symbol, timeframe, source")]

    def find(self, symbol, timeframe, source) -> int | None:
        row = self.db.execute("SELECT id FROM dataset WHERE symbol=? AND timeframe=? AND source=?",
                              (symbol, timeframe, source)).fetchone()
        return row[0] if row else None

    # ---- bars

    def add_bars(self, ds: int, rows: list) -> int:
        """Rows are (ts, open, high, low, close, volume, vwap, trades, available_at).
        The first value stored for a timestamp is kept; a later different value is logged
        as a revision and never replaces it. Returns the number of new bars."""
        if not rows:
            return 0
        for r in rows:
            if r[8] <= r[0]:
                raise ValueError(f"bar at {r[0]} claims to be available at {r[8]}, before it finished")
        lo, hi = min(r[0] for r in rows), max(r[0] for r in rows)
        have = dict(self.db.execute("SELECT ts, close FROM bar WHERE dataset_id=? AND ts BETWEEN ? AND ?", (ds, lo, hi)))
        fresh = [(ds, *r) for r in rows if r[0] not in have]
        changed = [(ds, r[0], int(time.time()), have[r[0]], r[4]) for r in rows
                   if r[0] in have and have[r[0]] is not None and abs(have[r[0]] - r[4]) > 1e-9]
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO bar VALUES (?,?,?,?,?,?,?,?,?,?)", fresh)
            self.db.executemany("INSERT OR IGNORE INTO revision VALUES (?,?,?,?,?)", changed)
        return len({r[1] for r in fresh})

    def bars(self, ds: int, start=None, end=None, as_of=None, allow_proxy=False) -> list:
        """Bars in time order. With as_of, only bars that had become available by then."""
        if not allow_proxy and self.describe(ds)["proxy_for"]:
            raise ProxyDataError(f"dataset {ds} is a stand-in for {self.describe(ds)['proxy_for']}; pass allow_proxy=True")
        sql, args = "SELECT * FROM bar WHERE dataset_id=?", [ds]
        for clause, value in (("ts >= ?", start), ("ts <= ?", end), ("available_at <= ?", as_of)):
            if value is not None:
                sql += " AND " + clause
                args.append(value)
        return self.db.execute(sql + " ORDER BY ts", args).fetchall()

    def iter_bars(self, ds: int):
        """Every bar of a dataset as plain tuples, streamed, for checks over millions of rows."""
        yield from self.db.execute("SELECT ts, open, high, low, close, volume, vwap, trades, available_at FROM bar"
                                   " WHERE dataset_id=? ORDER BY ts", (ds,))

    def coverage(self, ds: int) -> dict:
        first, last, rows = self.db.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM bar WHERE dataset_id=?", (ds,)).fetchone()
        return {"first": first, "last": last, "rows": rows}

    def revisions(self, ds: int) -> list:
        return self.db.execute("SELECT * FROM revision WHERE dataset_id=? ORDER BY ts", (ds,)).fetchall()

    # ---- the market calendar

    def set_calendar(self, rows: list, source: str) -> None:
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO session VALUES (?,?,?,?)", [(d, o, c, source) for d, o, c in rows])

    def sessions(self, start: str, end: str) -> list:
        return [dict(r) for r in self.db.execute("SELECT day, open, close FROM session WHERE day BETWEEN ? AND ? ORDER BY day",
                                                 (start, end))]

    def session_close(self, day: date) -> str | None:
        row = self.db.execute("SELECT close FROM session WHERE day=?", (day.isoformat(),)).fetchone()
        return row[0] if row else None

    # ---- corporate actions, gaps, quality, runs, findings

    def add_actions(self, rows: list, source: str) -> None:
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO corporate_action VALUES (?,?,?,?,?)", [(*r, source) for r in rows])

    def actions(self, symbol: str, kind: str | None = None) -> list:
        sql, args = "SELECT * FROM corporate_action WHERE symbol=?", [symbol]
        if kind:
            sql, args = sql + " AND kind=?", args + [kind]
        return self.db.execute(sql + " ORDER BY ex_date", args).fetchall()

    def add_gaps(self, ds: int, gaps: list) -> None:
        with self.db:
            self.db.execute("DELETE FROM gap WHERE dataset_id=?", (ds,))
            self.db.executemany("INSERT OR REPLACE INTO gap VALUES (?,?,?,?,?)",
                                [(ds, g["day"], g["kind"], g.get("expected"), g.get("found")) for g in gaps])

    def gaps(self, ds: int) -> list:
        return self.db.execute("SELECT * FROM gap WHERE dataset_id=? ORDER BY day", (ds,)).fetchall()

    def add_quality(self, ds: int, name: str, passed: bool, detail: str = "") -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO quality VALUES (?,?,?,?,?)", (ds, name, int(passed), detail, int(time.time())))

    def quality(self, ds: int) -> list:
        return self.db.execute("SELECT * FROM quality WHERE dataset_id=? ORDER BY name", (ds,)).fetchall()

    def start_run(self, ds: int, start: str, end: str) -> int:
        with self.db:
            return self.db.execute("INSERT INTO run (dataset_id, started, requested_start, requested_end, status)"
                                   " VALUES (?,?,?,?, 'running')", (ds, int(time.time()), start, end)).lastrowid

    def finish_run(self, run: int, rows: int, status: str, error: str | None = None) -> None:
        with self.db:
            self.db.execute("UPDATE run SET finished=?, rows=?, status=?, error=? WHERE id=?",
                            (int(time.time()), rows, status, error, run))

    def runs(self, ds: int) -> list:
        return self.db.execute("SELECT * FROM run WHERE dataset_id=? ORDER BY id", (ds,)).fetchall()

    def add_finding(self, topic: str, text: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO finding VALUES (?,?,?)", (topic, text, int(time.time())))

    def findings(self) -> list:
        return self.db.execute("SELECT * FROM finding ORDER BY topic").fetchall()

    # ---- options

    def add_contracts(self, rows: list, source: str) -> None:
        """Rows are (symbol, underlying, kind, strike, expiration, status)."""
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO contract VALUES (?,?,?,?,?,?,?)", [(*r, source) for r in rows])

    def contracts(self, underlying: str, expiration_from: str | None = None, expiration_to: str | None = None) -> list:
        return self.db.execute("SELECT * FROM contract WHERE underlying=? AND expiration BETWEEN ? AND ? ORDER BY expiration, strike",
                               (underlying, expiration_from or "0000", expiration_to or "9999")).fetchall()

    def add_option_bars(self, contract: str, timeframe: str, rows: list, source: str) -> int:
        before = self.db.total_changes
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO option_bar VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                [(contract, timeframe, *r, source) for r in rows])
        return self.db.total_changes - before

    def option_bars(self, contract: str, timeframe: str = "1Day", as_of=None) -> list:
        sql, args = "SELECT * FROM option_bar WHERE contract=? AND timeframe=?", [contract, timeframe]
        if as_of is not None:
            sql, args = sql + " AND available_at <= ?", args + [as_of]
        return self.db.execute(sql + " ORDER BY ts", args).fetchall()

    def integrity_ok(self) -> bool:
        return self.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
