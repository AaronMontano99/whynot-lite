"""What paper trading must remember across restarts: the order queue, open positions,
finished trades, the risk engine's state, the last day processed, and a log."""

import json
import sqlite3
import time
from pathlib import Path

from ..riskengine import RiskEngine

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_intent (
  id INTEGER PRIMARY KEY, agent TEXT NOT NULL, underlying TEXT, side TEXT NOT NULL, contract TEXT, qty INTEGER, direction TEXT, state TEXT,
  est_price REAL, volume REAL, trades REAL, decided_day TEXT NOT NULL, for_day TEXT, status TEXT NOT NULL, reason TEXT,
  client_order_id TEXT, order_id TEXT, fill_price REAL, simulated INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS live_position (
  agent TEXT PRIMARY KEY, underlying TEXT, contract TEXT NOT NULL, qty INTEGER NOT NULL, direction TEXT, state TEXT, entry_day TEXT,
  entry_price REAL, reason TEXT, simulated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS live_trade (
  id INTEGER PRIMARY KEY, agent TEXT, underlying TEXT, contract TEXT, qty INTEGER, direction TEXT, state TEXT, entry_day TEXT, entry_price REAL,
  exit_day TEXT, exit_price REAL, exit_reason TEXT, pnl REAL, ret REAL, simulated INTEGER NOT NULL, broker TEXT);
CREATE TABLE IF NOT EXISTS live_decision (
  day TEXT NOT NULL, agent TEXT NOT NULL, action TEXT, direction TEXT, state TEXT, reason TEXT, PRIMARY KEY (day, agent));
CREATE TABLE IF NOT EXISTS live_checkpoint (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS live_log (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, detail TEXT, at INTEGER NOT NULL);
"""


class LiveDB:
    def __init__(self, path="data/whynot.db"):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- log and checkpoints

    def note(self, kind: str, **detail) -> None:
        with self.db:
            self.db.execute("INSERT INTO live_log (kind, detail, at) VALUES (?,?,?)", (kind, json.dumps(detail, default=str), int(time.time())))

    def log(self, limit: int = 200) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM (SELECT * FROM live_log ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))]

    def checkpoint(self, name: str) -> str | None:
        row = self.db.execute("SELECT value FROM live_checkpoint WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def set_checkpoint(self, name: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO live_checkpoint VALUES (?,?)", (name, value))

    def record_decisions(self, day, decisions: dict) -> None:
        """What each agent decided at a close, whether or not it led to an order."""
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO live_decision VALUES (?,?,?,?,?,?)", [
                (str(day), agent, d.action, getattr(d, "direction", None), getattr(d, "state", None), getattr(d, "reason", None))
                for agent, d in decisions.items()])

    def decisions(self, day=None) -> list:
        if day is None:
            row = self.db.execute("SELECT MAX(day) FROM live_decision").fetchone()
            day = row[0] if row else None
        return [dict(r) for r in self.db.execute("SELECT * FROM live_decision WHERE day=? ORDER BY agent", (str(day),))] if day else []

    # ---- the order queue

    def add_intent(self, **f) -> dict:
        cols = ("agent", "underlying", "side", "contract", "qty", "direction", "state", "est_price", "volume", "trades", "decided_day", "status", "reason")
        with self.db:
            i = self.db.execute(f"INSERT INTO live_intent ({', '.join(cols)}, updated_at) VALUES ({', '.join('?' * len(cols))}, ?)",
                                (*[str(f[c]) if c == "decided_day" else f.get(c) for c in cols], int(time.time()))).lastrowid
        return self.intent(i)

    def intent(self, i: int) -> dict:
        return dict(self.db.execute("SELECT * FROM live_intent WHERE id=?", (i,)).fetchone())

    def intents(self, status: str | None = None) -> list:
        sql, args = "SELECT * FROM live_intent", ()
        if status:
            sql, args = sql + " WHERE status=?", (status,)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id", args)]

    def update_intent(self, i: int, **f) -> dict:
        with self.db:
            self.db.execute(f"UPDATE live_intent SET {', '.join(k + '=?' for k in f)}, updated_at=? WHERE id=?", (*f.values(), int(time.time()), i))
        return self.intent(i)

    # ---- positions and trades

    def position(self, agent: str) -> dict | None:
        row = self.db.execute("SELECT * FROM live_position WHERE agent=?", (agent,)).fetchone()
        return dict(row) if row else None

    def positions(self) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM live_position ORDER BY agent")]

    def open_position(self, **f) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO live_position VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (f["agent"], f["underlying"], f["contract"], f["qty"], f["direction"], f["state"], str(f["entry_day"]), f["entry_price"],
                             f.get("reason"), int(f["simulated"])))

    def close_position(self, agent: str, day, price: float, reason: str, broker_label: str) -> dict:
        p = self.position(agent)
        pnl = (price - p["entry_price"]) * 100 * p["qty"]
        with self.db:
            self.db.execute("INSERT INTO live_trade (agent, underlying, contract, qty, direction, state, entry_day, entry_price, exit_day, exit_price,"
                            " exit_reason, pnl, ret, simulated, broker) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (agent, p["underlying"], p["contract"], p["qty"], p["direction"], p["state"], p["entry_day"], p["entry_price"], str(day), price,
                             reason, pnl, pnl / (p["entry_price"] * 100 * p["qty"]), p["simulated"], broker_label))
            self.db.execute("DELETE FROM live_position WHERE agent=?", (agent,))
        return p

    def trades(self) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM live_trade ORDER BY id")]

    # ---- the risk engine

    def save_risk(self, risk: RiskEngine) -> None:
        self.set_checkpoint("risk", json.dumps(risk.to_state(), default=str))

    def load_risk(self, settings, equity: float) -> RiskEngine:
        saved = self.checkpoint("risk")
        return RiskEngine.from_state(settings, json.loads(saved)) if saved else RiskEngine(settings, equity)
