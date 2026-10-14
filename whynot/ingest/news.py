"""Headlines as data: when each was published and which symbols it names. Kept in its own
file so a long download never holds the trading database. Nothing here reads a headline
for meaning; the only thing derived is how many there were."""

import json
import sqlite3
from datetime import date, timedelta

from .timeutil import parse_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS item (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, symbols TEXT NOT NULL, headline TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS tag (symbol TEXT NOT NULL, ts INTEGER NOT NULL, item_id INTEGER NOT NULL, names INTEGER NOT NULL,
                                PRIMARY KEY (symbol, item_id));
CREATE INDEX IF NOT EXISTS tag_ts ON tag (ts);
CREATE TABLE IF NOT EXISTS done (day TEXT PRIMARY KEY, items INTEGER NOT NULL);
"""


class NewsStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=60)
        self.db.executescript(SCHEMA)

    def add(self, items) -> int:
        """Store headlines not seen before. A later rewrite of one already stored is ignored: the first version is what was known."""
        new = 0
        for it in items:
            symbols, ts = sorted(set(it.get("symbols") or [])), parse_ts(it["created_at"])
            cur = self.db.execute("INSERT OR IGNORE INTO item VALUES (?,?,?,?,?)",
                                  (it["id"], ts, json.dumps(symbols), it.get("headline"), it.get("source")))
            if cur.rowcount:
                new += 1
                self.db.executemany("INSERT OR IGNORE INTO tag VALUES (?,?,?,?)", [(s, ts, it["id"], len(symbols)) for s in symbols])
        self.db.commit()
        return new

    def counts(self, after: int, until: int, max_symbols: int = 3) -> dict:
        """Headlines per symbol published after `after` and no later than `until`. One that names more than `max_symbols` is a roundup and is skipped."""
        return dict(self.db.execute("SELECT symbol, COUNT(*) FROM tag WHERE ts>? AND ts<=? AND names<=? GROUP BY symbol",
                                    (after, until, max_symbols)))

    def mark(self, day: date, items: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO done VALUES (?,?)", (day.isoformat(), items))
        self.db.commit()

    def has(self, day: date) -> bool:
        return self.db.execute("SELECT 1 FROM done WHERE day=?", (day.isoformat(),)).fetchone() is not None

    def covered(self):
        lo, hi = self.db.execute("SELECT MIN(day), MAX(day) FROM done").fetchone()
        return (date.fromisoformat(lo), date.fromisoformat(hi)) if lo else None


def ingest_news(client, store: NewsStore, start: date, end: date, log=None, today: date | None = None) -> int:
    """Download day by day (UTC days). Days already finished are skipped, except one that was still running when it was fetched."""
    today, asked, day = today or date.today(), 0, start
    while day <= end:
        if not store.has(day):
            n = store.add(client.news(f"{day.isoformat()}T00:00:00Z", f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z"))
            asked += 1
            if day < today:                          # a day still in progress is fetched again next time
                store.mark(day, n)
            if log and asked % 50 == 0:
                log(f"news: through {day} ({asked} days fetched)")
        day += timedelta(days=1)
    return asked
