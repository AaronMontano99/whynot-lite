"""A small daily copy of everything that cannot be downloaded again: trades, positions,
orders, risk state, research memory, regime history, the lifecycle. Bulk market data
(candles, option bars, contract listings) is left out because it can be fetched again."""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

SKIP = {"bar", "option_bar", "contract"}


def backup(path="data/whynot.db", dest="data/backups", stamp: str | None = None, keep: int = 14) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"whynot-{stamp or datetime.now().strftime('%Y-%m-%d')}.db"
    out.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.execute("ATTACH DATABASE ? AS bk", (str(out),))
    for name, sql in src.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        if name in SKIP or not sql:
            continue
        src.execute(re.sub(r'^CREATE TABLE\s+(IF NOT EXISTS\s+)?["`\[]?\w+["`\]]?', f'CREATE TABLE bk."{name}"', sql, count=1, flags=re.I))
        src.execute(f'INSERT INTO bk."{name}" SELECT * FROM main."{name}"')
    src.commit()
    src.execute("DETACH DATABASE bk")
    src.close()
    check = sqlite3.connect(out)
    ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    check.close()
    if ok != "ok":
        out.unlink(missing_ok=True)
        raise RuntimeError(f"the backup failed its integrity check: {ok}")
    for old in sorted(dest.glob("whynot-*.db"))[:-keep]:
        old.unlink()
    return out


def latest(dest="data/backups") -> Path | None:
    files = sorted(Path(dest).glob("whynot-*.db"))
    return files[-1] if files else None
