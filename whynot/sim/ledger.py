"""Every backtest run, every decision in it (including refusals) and every trade, kept
side by side. A run is never overwritten; running again adds a new run."""

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bt_run (
  id INTEGER PRIMARY KEY, agent TEXT NOT NULL, bot TEXT, symbol TEXT, strategy_version TEXT NOT NULL, partition TEXT NOT NULL,
  label TEXT NOT NULL, start TEXT, end TEXT, params TEXT NOT NULL, summary TEXT, simulated INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bt_decision (
  run_id INTEGER NOT NULL, seq INTEGER NOT NULL, day TEXT NOT NULL, time TEXT, action TEXT NOT NULL, reason TEXT, state TEXT,
  direction TEXT, fraction REAL, PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS bt_trade (
  run_id INTEGER NOT NULL, seq INTEGER NOT NULL, agent TEXT, symbol TEXT, strategy_version TEXT, direction TEXT, rule TEXT, state TEXT,
  probs TEXT, features TEXT, entry_day TEXT, entry_time TEXT, entry_price REAL, entry_reason TEXT, exit_day TEXT, exit_time TEXT,
  exit_price REAL, exit_reason TEXT, shares REAL, fraction REAL, stop REAL, costs REAL, pnl REAL, ret REAL, mfe REAL, mae REAL, days_held INTEGER,
  PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS opt_run (
  id INTEGER PRIMARY KEY, label TEXT NOT NULL, partition TEXT NOT NULL, params TEXT NOT NULL, summary TEXT, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS opt_trade (
  run_id INTEGER NOT NULL, seq INTEGER NOT NULL, agent TEXT, underlying TEXT, contract TEXT, kind TEXT, strike REAL, expiration TEXT, qty INTEGER,
  direction TEXT, state TEXT, entry_day TEXT, entry_price REAL, iv REAL, delta REAL, selection_reason TEXT, size_reason TEXT, underlying_ret REAL,
  exit_day TEXT, exit_price REAL, exit_reason TEXT, pnl REAL, option_ret REAL, flags TEXT, simulated INTEGER NOT NULL,
  PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS opt_rejection (
  run_id INTEGER NOT NULL, seq INTEGER NOT NULL, agent TEXT, day TEXT, stage TEXT, reason TEXT, direction TEXT, state TEXT,
  PRIMARY KEY (run_id, seq));
"""


class Ledger:
    def __init__(self, path="data/whynot.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def save(self, agent, result, partition: str, label: str, params: dict, summary: dict | None = None) -> int:
        days = [d for d, _ in result.equity]
        with self.db:
            run = self.db.execute(
                "INSERT INTO bt_run (agent, bot, symbol, strategy_version, partition, label, start, end, params, summary, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (agent.id, agent.bot, agent.symbol, agent.version, partition, label, str(days[0]) if days else None,
                 str(days[-1]) if days else None, json.dumps(params), json.dumps(summary or {}, default=str), int(time.time()))).lastrowid
            self.db.executemany("INSERT INTO bt_decision VALUES (?,?,?,?,?,?,?,?,?)", [
                (run, i, str(d.day), d.time, d.action, d.reason, d.state, d.direction, d.fraction) for i, d in enumerate(result.decisions)])
            self.db.executemany("INSERT INTO bt_trade VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                (run, i, agent.id, agent.symbol, agent.version, t.direction, t.rule, t.state, json.dumps(t.probs), json.dumps(t.features),
                 str(t.entry_day), t.entry_time, t.entry_price, t.entry_reason, str(t.exit_day), t.exit_time, t.exit_price, t.exit_reason,
                 t.shares, t.fraction, t.stop, t.costs, t.pnl, t.ret, t.mfe, t.mae, t.days_held) for i, t in enumerate(result.trades)])
        return run

    def runs(self, agent: str | None = None) -> list:
        sql, args = "SELECT * FROM bt_run", []
        if agent:
            sql, args = sql + " WHERE agent=?", [agent]
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id", args)]

    def decisions(self, run: int) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM bt_decision WHERE run_id=? ORDER BY seq", (run,))]

    def trades(self, run: int) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM bt_trade WHERE run_id=? ORDER BY seq", (run,))]

    def save_options(self, label: str, partition: str, result, params: dict, summary: dict | None = None) -> int:
        with self.db:
            run = self.db.execute("INSERT INTO opt_run (label, partition, params, summary, created_at) VALUES (?,?,?,?,?)",
                                  (label, partition, json.dumps(params), json.dumps(summary or {}, default=str), int(time.time()))).lastrowid
            self.db.executemany("INSERT INTO opt_trade VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                (run, i, t.agent, t.underlying, t.contract, t.kind, t.strike, str(t.expiration), t.qty, t.direction, t.state, str(t.entry_day),
                 t.entry_price, t.iv, t.delta, t.selection_reason, t.size_reason, t.underlying_ret, str(t.exit_day), t.exit_price, t.exit_reason,
                 t.pnl, t.option_ret, json.dumps(t.flags), int(t.simulated)) for i, t in enumerate(result.trades)])
            self.db.executemany("INSERT INTO opt_rejection VALUES (?,?,?,?,?,?,?,?)", [
                (run, i, r["agent"], str(r["day"]), r["stage"], r["reason"], r["direction"], r["state"]) for i, r in enumerate(result.rejections)])
        return run

    def option_trades(self, run: int) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM opt_trade WHERE run_id=? ORDER BY seq", (run,))]

    def option_rejections(self, run: int) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM opt_rejection WHERE run_id=? ORDER BY seq", (run,))]
