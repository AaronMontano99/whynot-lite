"""Alerts. Every event worth knowing about is queued once, then sent through whichever
channels are set up: Telegram if a bot token and chat id are in .env, and always a
notification on this Mac. A channel that fails keeps the alert for the next try."""

import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

PREFIX = "[WHYNOT paper]"
SCHEMA = """CREATE TABLE IF NOT EXISTS alert (
  id INTEGER PRIMARY KEY, day TEXT, kind TEXT NOT NULL, text TEXT NOT NULL, at INTEGER NOT NULL, sent_via TEXT, sent_at INTEGER, error TEXT,
  UNIQUE (day, kind, text));"""


def _env(path=".env") -> dict:
    out = dict(os.environ)
    f = Path(path)
    if f.exists():
        out.update(line.strip().split("=", 1) for line in f.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#"))
    return out


def configured_channels(env: dict | None = None) -> list:
    env = _env() if env is None else env
    return (["telegram"] if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID") else []) + ["mac notification"]


def _telegram(text: str) -> bool:
    env = _env()
    data = urllib.parse.urlencode({"chat_id": env["TELEGRAM_CHAT_ID"], "text": text}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendMessage", data=data, timeout=20) as resp:
        return bool(json.loads(resp.read()).get("ok"))


def _mac(text: str) -> bool:
    safe = text.replace("\\", " ").replace('"', "'")[:230]
    return subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "Project WHYNOT"'], capture_output=True, timeout=15).returncode == 0


def _scrub(text: str) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or _env().get("TELEGRAM_BOT_TOKEN") or ""
    text = re.sub(r"bot[^/\s]+/", "bot***/", text)
    return text.replace(token, "***") if token else text


class Alerter:
    def __init__(self, path="data/whynot.db", senders=None):
        self.db = sqlite3.connect(path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        known = {"telegram": _telegram, "mac notification": _mac}
        self.senders = [(name, known[name]) for name in configured_channels()] if senders is None else senders

    def emit(self, kind: str, text: str, day=None) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO alert (day, kind, text, at) VALUES (?,?,?,?)", (str(day) if day else None, kind, text, int(time.time())))

    def flush(self) -> int:
        sent = 0
        for row in self.db.execute("SELECT * FROM alert WHERE sent_via IS NULL ORDER BY id").fetchall():
            message, error = f"{PREFIX} {row['kind'].upper()}: {row['text']}", None
            for name, send in self.senders:
                try:
                    if send(message):
                        with self.db:
                            self.db.execute("UPDATE alert SET sent_via=?, sent_at=?, error=NULL WHERE id=?", (name, int(time.time()), row["id"]))
                        sent += 1
                        break
                except Exception as err:
                    error = _scrub(f"{name}: {type(err).__name__}: {err}")[:200]
            else:
                if error:
                    with self.db:
                        self.db.execute("UPDATE alert SET error=? WHERE id=?", (error, row["id"]))
        return sent

    def recent(self, limit: int = 50) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM (SELECT * FROM alert ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))]


def from_day(alerter: Alerter, path: str, day) -> None:
    """Queue an alert for each thing that happened on a day: fills, refused orders, errors,
    regime switches, drift freezes and kill-switch events."""
    con, d = sqlite3.connect(f"file:{path}?mode=ro", uri=True), str(day)
    con.row_factory = sqlite3.Row
    for p in con.execute("SELECT * FROM live_position WHERE entry_day=?", (d,)):
        alerter.emit("fill", f"{p['agent']} bought {p['qty']} x {p['contract']} at {p['entry_price']}", day)
    for t in con.execute("SELECT * FROM live_trade WHERE exit_day=?", (d,)):
        alerter.emit("fill", f"{t['agent']} sold {t['qty']} x {t['contract']} at {t['exit_price']}: {'+' if t['pnl'] >= 0 else '-'}${abs(t['pnl']):,.2f} ({t['exit_reason']})", day)
    for i in con.execute("SELECT * FROM live_intent WHERE status IN ('refused', 'cancelled') AND (decided_day=? OR for_day=?)", (d, d)):
        alerter.emit("order refused", f"{i['agent']} {i['side']} {i['contract'] or ''}: {(i['reason'] or '')[-180:]}", day)
    for r in con.execute("SELECT * FROM regime_day WHERE variant='expanding' AND day=?", (d,)):
        if r["switched"]:
            alerter.emit("state switch", f"{r['symbol']} is now {r['active']}: {r['reason']}", day)
        if r["frozen"]:
            alerter.emit("drift warning", f"{r['symbol']}: {r['reason']}", day)
    for l in con.execute("SELECT * FROM live_log WHERE kind IN ('stale data', 'data error', 'reconcile failed') AND date(at, 'unixepoch', 'localtime')=?", (d,)):
        alerter.emit("error", f"{l['kind']}: {(l['detail'] or '')[:180]}", day)
    saved = con.execute("SELECT value FROM live_checkpoint WHERE name='risk'").fetchone()
    for e in (json.loads(saved[0]).get("events", []) if saved else []):
        if e.get("kind") in ("kill", "halt") and e.get("day") == d:
            alerter.emit("kill switch", e.get("reason", ""), day)
