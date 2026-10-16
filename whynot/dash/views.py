"""What each dashboard section shows, as plain dictionaries read from the database."""

import functools
import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import date, datetime

from .. import regime, walkforward
from ..ingest.catalog import Catalog
from ..ingest.timeutil import ET, et_day
from ..live.lifecycle import Lifecycle
from ..live.state import LiveDB, agent_mark
from ..partitions import DEVELOPMENT_END
from ..researchlab.memory import ResearchDB
from ..riskengine import RESET_PHRASE, RiskSettings
from ..sim.agents import AGENTS
from ..sim.ledger import Ledger
from ..sim.run import COST_BPS, DAILY_START, INTRADAY_START

ALLOCATION = 20_000.0
THEMES = {"Kip": "Futuristic", "Nemo": "Ocean", "Chud": "Matrix", "Era": "Wrist watch", "Aura": "Glowing, godly"}
NOT_TRADING = {"Era": "Not paper trading: its trades are intraday and only daily option data is available so far."}


def prepare(path: str) -> None:
    """Make sure every table exists, so the read-only views never meet a missing one."""
    for make in (Catalog, walkforward.RegimeStore, Ledger, ResearchDB, LiveDB, Lifecycle):
        make(path)


_opened = threading.local()


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    if getattr(_opened, "cons", None) is not None:
        _opened.cons.append(con)
    return con


def _closes(view):
    """Close whatever the view opened, also when it fails; the server runs for weeks and must not run out of file handles."""
    @functools.wraps(view)
    def run(*args, **kwargs):
        if getattr(_opened, "cons", None) is not None:      # called from inside another view
            return view(*args, **kwargs)
        _opened.cons = []
        try:
            return view(*args, **kwargs)
        finally:
            for con in _opened.cons:
                con.close()
            _opened.cons = None
    return run


def _rows(con, sql, args=()) -> list:
    return [dict(r) for r in con.execute(sql, args)]


def _regime(con, symbol: str) -> dict:
    r = con.execute("SELECT * FROM regime_day WHERE symbol=? AND variant='expanding' ORDER BY day DESC LIMIT 1", (symbol,)).fetchone()
    if r is None:
        return {"day": None, "active": None, "top": None, "probs": {}, "next_probs": {}, "size": 0.0, "reason": "no regime record yet",
                "frozen": False, "expected_days": None}
    return {"day": r["day"], "active": r["active"], "top": r["top"], "probs": json.loads(r["probs"]), "next_probs": json.loads(r["next_probs"]),
            "size": r["size"], "reason": r["reason"], "frozen": bool(r["frozen"]), "expected_days": r["expected_days"]}


def _drawdown(pnls: list) -> float:
    total = peak = worst = 0.0
    for p in pnls:
        total += p
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst / ALLOCATION


@_closes
def state(path: str, broker: dict | None = None) -> dict:
    """The city: one entry per tower, plus the vault."""
    con, broker = connect(path), broker or {"ok": False, "label": "Alpaca paper account", "error": "not asked"}
    mode = (con.execute("SELECT mode FROM lifecycle_event ORDER BY id DESC LIMIT 1").fetchone() or ["HISTORICAL_INITIALIZATION"])[0]
    meta = con.execute("SELECT value FROM lifecycle_meta WHERE name='paper_agents'").fetchone()
    trading = json.loads(meta[0]) if meta else []
    saved = con.execute("SELECT value FROM live_checkpoint WHERE name='risk'").fetchone()
    risk = json.loads(saved[0]) if saved else {}
    today = datetime.now(ET).date().isoformat()
    held = broker.get("positions", {}) if broker.get("ok") else {}
    latest = con.execute("SELECT MAX(day) FROM live_decision").fetchone()[0]
    bots = []
    for a in AGENTS.values():
        champ = con.execute("SELECT version, status FROM champion WHERE agent=? AND current=1", (a.id,)).fetchone()
        trades = _rows(con, "SELECT pnl, exit_day FROM live_trade WHERE agent=? ORDER BY id", (a.id,))
        pos = con.execute("SELECT * FROM live_position WHERE agent=?", (a.id,)).fetchone()
        sig = con.execute("SELECT * FROM live_decision WHERE agent=? AND day=?", (a.id, latest)).fetchone() if latest else None
        waiting = con.execute("SELECT 1 FROM live_intent WHERE agent=? AND status IN ('pending', 'submitted') AND side='BUY'", (a.id,)).fetchone()
        mark = held.get(pos["contract"]) if pos else None
        realized = sum(t["pnl"] for t in trades)
        since, moved = agent_mark(con, pos, mark)
        unreal = since if mark else (None if pos else 0.0)
        active = a.id in trading and mode == "LIVE_PAPER_TRADING"
        bots.append({
            "id": a.id, "name": f"AGENT {a.id.upper()}", "symbol": a.symbol, "focus": a.focus, "theme": THEMES.get(a.id, ""), "bot": a.bot,
            "status": "PAPER TRADING" if active else "NOT TRADING",
            "status_note": "" if active else NOT_TRADING.get(a.id, "Not approved for paper trading."),
            "state": ("STANDBY" if not active else "LOCKED" if risk.get("halted") else "IN_TRADE" if pos else "ARMED" if waiting else "SCANNING"),
            "regime": _regime(con, a.symbol),
            "strategy": {"version": champ["version"] if champ else a.version, "status": champ["status"] if champ else "provisional"},
            "signal": {"action": sig["action"], "direction": sig["direction"], "reason": sig["reason"], "day": sig["day"]} if sig
                      else {"action": None, "direction": None, "reason": "no decision yet", "day": None},
            "position": None if not pos else {"contract": pos["contract"], "qty": pos["qty"], "direction": pos["direction"], "entry_day": pos["entry_day"],
                                              "entry_price": pos["entry_price"], "price": mark["price"] if mark else None,
                                              "unrealized": unreal, "simulated": bool(pos["simulated"])},
            "day_pnl": ((since if pos["entry_day"] == today else moved) if mark else 0.0)
                       + sum(t["pnl"] for t in trades if t["exit_day"] == today),
            "total_pnl": realized + (unreal or 0.0), "trades": len(trades),
            "win_rate": sum(t["pnl"] > 0 for t in trades) / len(trades) if trades else None,
            "drawdown": _drawdown([t["pnl"] for t in trades]),
        })
    all_trades = _rows(con, "SELECT pnl FROM live_trade")
    ok = bool(broker.get("ok"))
    vault = {"equity": broker["equity"] if ok else None, "day_pnl": broker["equity"] - broker["last_equity"] if ok else None,
             "total_pnl": broker["equity"] - 100_000.0 if ok else None, "trades": len(all_trades),
             "win_rate": sum(t["pnl"] > 0 for t in all_trades) / len(all_trades) if all_trades else None,
             "halted": bool(risk.get("halted")), "halt_reason": risk.get("halt_reason", ""), "broker": broker.get("label"),
             "note": "" if ok else f"broker not reachable: {broker.get('error', '')}"}
    return {"type": "state", "standby": mode != "LIVE_PAPER_TRADING", "live": True, "mode": mode, "ts": datetime.now(ET).isoformat(timespec="seconds"),
            "last_close": (con.execute("SELECT value FROM live_checkpoint WHERE name='close'").fetchone() or [None])[0], "bots": bots, "vault": vault}


@_closes
def live(path: str) -> dict:
    con = connect(path)
    latest = con.execute("SELECT MAX(day) FROM live_decision").fetchone()[0]
    return {"waiting": _rows(con, "SELECT * FROM live_intent WHERE status IN ('pending', 'submitted') ORDER BY id"),
            "positions": _rows(con, "SELECT * FROM live_position ORDER BY agent"),
            "decisions": _rows(con, "SELECT * FROM live_decision WHERE day=? ORDER BY agent", (latest,)) if latest else [],
            "orders": _rows(con, "SELECT * FROM live_intent ORDER BY id DESC LIMIT 40"),
            "trades": _rows(con, "SELECT * FROM live_trade ORDER BY id DESC LIMIT 100"),
            "log": _rows(con, "SELECT * FROM live_log ORDER BY id DESC LIMIT 30")}


@_closes
def regimes(path: str) -> dict:
    from ..live.run import LIVE_START
    con, out = connect(path), {}
    for symbol in ("SPY", "QQQ", "IWM"):
        r = _regime(con, symbol)
        m = con.execute("SELECT * FROM regime_model WHERE symbol=? AND variant='expanding' ORDER BY version DESC LIMIT 1", (symbol,)).fetchone()
        states = []
        if m:
            p, labels = json.loads(m["params"]), json.loads(m["labels"])
            states = [{"state": s, "expected_days": round(1 / max(1 - p["transmat"][i][i], 1e-9), 1), "volatility_z": round(p["means"][i][1], 2),
                       "to": {t: round(p["transmat"][i][j], 3) for j, t in enumerate(labels)}} for i, s in enumerate(labels)]
        r.update(states=states, model_version=m["version"] if m else None, fitted_through=m["fitted_through"] if m else None,
                 since_live=_rows(con, "SELECT day, active, size, frozen, switched FROM regime_day WHERE symbol=? AND variant='expanding' AND day>=? ORDER BY day",
                                  (symbol, LIVE_START.isoformat())),
                 policy=regime.POLICY)
        out[symbol] = r
    return out


@_closes
def research(path: str) -> dict:
    con = connect(path)
    limits = con.execute("SELECT value FROM setting WHERE name='limits'").fetchone()
    exps = _rows(con, "SELECT id, cycle_id, agent, hypothesis, change, challenger_version, champion_version, decision, reason, dataset, created_at"
                      " FROM experiment ORDER BY id DESC LIMIT 100")
    return {"champions": _rows(con, "SELECT agent, version, status, approved_by, since FROM champion WHERE current=1 ORDER BY id"),
            "cycles": _rows(con, "SELECT id, direction, agent, hypothesis, status, outcome, max_experiments, max_configs, opened_at, closed_at FROM cycle ORDER BY id DESC"),
            "experiments": exps, "approval_queue": _rows(con, "SELECT * FROM approval WHERE status='pending'"),
            "holdout_looks": con.execute("SELECT COUNT(*) FROM holdout_look").fetchone()[0],
            "limits": json.loads(limits[0]) if limits else {"max_experiments": 20, "max_configs": 5, "max_failed_cycles": 3},
            "lessons": _rows(con, "SELECT * FROM lesson ORDER BY id DESC LIMIT 50")}


@_closes
def journal(path: str, q: str = "", agent: str = "", source: str = "", limit: int = 300) -> dict:
    """Every trade on record, from paper trading and from the stored backtests, each
    labeled with what produced it."""
    con, rows = connect(path), []
    for t in _rows(con, "SELECT * FROM live_trade ORDER BY id DESC"):
        rows.append({"source": t["broker"] or ("Simulated" if t["simulated"] else "Alpaca paper account"), "simulated": bool(t["simulated"]),
                     "agent": t["agent"], "symbol": t["underlying"], "instrument": t["contract"], "direction": t["direction"], "state": t["state"],
                     "entry_day": t["entry_day"], "entry_price": t["entry_price"], "exit_day": t["exit_day"], "exit_price": t["exit_price"],
                     "entry_reason": "", "exit_reason": t["exit_reason"], "pnl": t["pnl"], "ret": t["ret"]})
    for t in _rows(con, "SELECT t.*, r.label FROM opt_trade t JOIN opt_run r ON r.id=t.run_id WHERE r.id=(SELECT MIN(id) FROM opt_run) ORDER BY t.seq"):
        rows.append({"source": "Option backtest 2024 (simulated)", "simulated": True, "agent": t["agent"], "symbol": t["underlying"],
                     "instrument": t["contract"], "direction": t["direction"], "state": t["state"], "entry_day": t["entry_day"],
                     "entry_price": t["entry_price"], "exit_day": t["exit_day"], "exit_price": t["exit_price"], "entry_reason": t["selection_reason"],
                     "exit_reason": t["exit_reason"], "pnl": t["pnl"], "ret": t["option_ret"]})
    for t in _rows(con, "SELECT t.* FROM bt_trade t WHERE t.run_id IN (SELECT MIN(id) FROM bt_run WHERE partition='DEVELOPMENT' AND label='v1 signal test' GROUP BY agent)"
                        " ORDER BY t.entry_day DESC"):
        rows.append({"source": "Backtest (simulated)", "simulated": True, "agent": t["agent"], "symbol": t["symbol"], "instrument": f"{t['symbol']} shares",
                     "direction": t["direction"], "state": t["state"], "entry_day": t["entry_day"], "entry_price": t["entry_price"],
                     "exit_day": t["exit_day"], "exit_price": t["exit_price"], "entry_reason": t["entry_reason"], "exit_reason": t["exit_reason"],
                     "pnl": t["pnl"], "ret": t["ret"]})
    needle = q.strip().lower()
    keep = [r for r in rows if (not agent or r["agent"] == agent) and (not source or source.lower() in r["source"].lower())
            and (not needle or needle in " ".join(str(v) for v in r.values()).lower())]
    return {"total": len(keep), "rows": keep[:limit]}


@_closes
def risk(path: str, broker: dict | None = None) -> dict:
    con = connect(path)
    saved = con.execute("SELECT value FROM live_checkpoint WHERE name='risk'").fetchone()
    s = json.loads(saved[0]) if saved else {}
    equity = broker["equity"] if broker and broker.get("ok") else s.get("equity")
    by_agent, by_direction = {}, {}
    for bot, _, p in s.get("positions", []):
        by_agent[bot] = by_agent.get(bot, 0.0) + p["risk"]
        by_direction[p["direction"]] = by_direction.get(p["direction"], 0.0) + p["risk"]
    peak = s.get("peak") or equity
    return {"halted": bool(s.get("halted")), "halt_reason": s.get("halt_reason", ""), "equity": equity, "peak": peak,
            "drawdown": 1 - equity / peak if equity and peak else 0.0,
            "day_loss": 1 - equity / s["day_start"] if equity and s.get("day_start") else 0.0,
            "week_loss": 1 - equity / s["week_start"] if equity and s.get("week_start") else 0.0,
            "at_risk": {"total": sum(by_agent.values()), "by_agent": by_agent, "by_direction": by_direction},
            "orders_today": s.get("orders_today", 0), "failures": s.get("failures", 0), "settings": asdict(RiskSettings()),
            "events": s.get("events", [])[-40:][::-1],
            "how_to_reset": f"The kill switch is reset only by you. Tell Claude to reset it, or it stays off. The phrase the code requires is: {RESET_PHRASE}"}


@_closes
def health(path: str) -> dict:
    con = connect(path)
    data = []
    for d in _rows(con, "SELECT * FROM dataset ORDER BY category, symbol, timeframe"):
        last = con.execute("SELECT MAX(ts) FROM bar WHERE dataset_id=?", (d["id"],)).fetchone()[0]
        run = con.execute("SELECT status, finished, error FROM run WHERE dataset_id=? ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
        data.append({"category": d["category"], "symbol": d["symbol"], "candles": d["timeframe"], "source": d["source"],
                     "kind": f"proxy for {d['proxy_for']}" if d["proxy_for"] else "authentic", "prices": d["adjustment"],
                     "last": et_day(last).isoformat() if last else None,
                     "gaps": con.execute("SELECT COUNT(*) FROM gap WHERE dataset_id=?", (d["id"],)).fetchone()[0],
                     "last_download": run["status"] if run else None, "error": run["error"] if run else None})
    opt = con.execute("SELECT COUNT(DISTINCT contract), MAX(ts) FROM option_bar").fetchone()
    mode = (con.execute("SELECT mode FROM lifecycle_event ORDER BY id DESC LIMIT 1").fetchone() or ["HISTORICAL_INITIALIZATION"])[0]
    return {"mode": mode, "last_close_processed": (con.execute("SELECT value FROM live_checkpoint WHERE name='close'").fetchone() or [None])[0],
            "datasets": data, "options": {"contracts_with_bars": opt[0], "last": et_day(opt[1]).isoformat() if opt[1] else None},
            "findings": _rows(con, "SELECT topic, text FROM finding ORDER BY topic"),
            "log": _rows(con, "SELECT * FROM live_log ORDER BY id DESC LIMIT 25"),
            "lifecycle": _rows(con, "SELECT * FROM lifecycle_event ORDER BY id")}


# ---------------------------------------------------------------- historical replay

def replay_frames(agent, candles: list, records: dict, start: date, end: date, cost_bps: float, sessions=None) -> dict:
    """Run the same engine live trading uses over the range and lay the result out day
    by day. How fast it is then played back is the viewer's choice and changes nothing."""
    from ..sim.daily import run_daily
    from ..sim.intraday import run_intraday
    out = (run_intraday(agent, sessions, records, start, end, cost_bps) if agent.timeframe == "5Min"
           else run_daily(agent, candles, records, start, end, cost_bps))
    by_day = {}
    for d in out.decisions:
        by_day.setdefault(d.day, []).append(d)
    done, i, realized, peak, days = sorted(out.trades, key=lambda t: t.exit_day), 0, 0.0, None, []
    for day, value in out.equity:
        while i < len(done) and done[i].exit_day <= day:
            realized += done[i].pnl
            i += 1
        peak = value if peak is None else max(peak, value)
        ds = by_day.get(day, [])
        held = next((t for t in out.trades if t.entry_day <= day and (t.exit_day is None or t.exit_day > day)), None)
        if held is None and out.open_position is not None and out.open_position.entry_day <= day:
            held = out.open_position
        days.append({"day": day.isoformat(), "regime": ds[-1].state if ds else None, "action": " / ".join(d.action for d in ds) if ds else "NONE",
                     "reason": "; ".join(d.reason for d in ds if d.reason)[:240], "equity": round(value, 2), "realized": round(realized, 2),
                     "unrealized": round(value - ALLOCATION - realized, 2), "drawdown": round(1 - value / peak, 5),
                     "position": f"{held.direction} {held.shares:g} sh from {held.entry_price:.2f}" if held else None})
    return {"agent": agent.id, "symbol": agent.symbol, "version": agent.version, "simulated": True,
            "note": "Signals on the fund itself (shares), not options: no real option prices exist for these years. "
                    "Version one of the strategy, with nothing learned later applied.",
            "days": days,
            "trades": [{"direction": t.direction, "state": t.state, "entry_day": str(t.entry_day), "entry_price": round(t.entry_price, 2),
                        "exit_day": str(t.exit_day), "exit_price": round(t.exit_price, 2), "exit_reason": t.exit_reason,
                        "pnl": round(t.pnl, 2), "ret": round(t.ret, 4)} for t in out.trades]}


def replay(path: str, agent: str, start: str, end: str) -> dict:
    from ..sim.run import CatalogSessions, regime_records
    a = AGENTS[agent]
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    first = INTRADAY_START if a.timeframe == "5Min" else DAILY_START
    if s < first or e > DEVELOPMENT_END or s > e:
        raise PermissionError(f"Replay for Agent {agent} covers {first} to {DEVELOPMENT_END}, the development years. Later years are held back: "
                              "validation is looked at once per candidate strategy, and the holdout is locked.")
    cat, store = Catalog(path), walkforward.RegimeStore(path)
    try:
        records = regime_records(store, a.symbol, e)
        if a.timeframe == "5Min":
            return replay_frames(a, [], records, s, e, COST_BPS[a.symbol], sessions=CatalogSessions(cat, a.symbol, s, e))
        candles = [c for c in walkforward.candles_from_catalog(cat, a.symbol) if c.day <= e]
        return replay_frames(a, candles, records, s, e, COST_BPS[a.symbol])
    finally:
        cat.db.close(), store.db.close()
