"""The daily research report, written by code from what is on record. Where something
is not known or not built, it says so."""

import json
import math
import sqlite3
import time
from datetime import date

from ..live.state import agent_mark
from ..sizing import MIN_FILLS
from ..sim.agents import AGENTS

SCHEMA = "CREATE TABLE IF NOT EXISTS daily_report (day TEXT PRIMARY KEY, text TEXT NOT NULL, at INTEGER NOT NULL);"


def _money(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def daily_report(path: str, day: date, broker: dict | None = None, checks: list | None = None) -> str:
    con, d = sqlite3.connect(f"file:{path}?mode=ro", uri=True), day.isoformat()
    con.row_factory = sqlite3.Row
    broker = broker or {"ok": False}
    held = broker.get("positions", {}) if broker.get("ok") else {}
    out = [f"# Daily research report: {d}", "",
           "Written by code from the day's records. Paper money only. " +
           (f"Account value ${broker['equity']:,.2f}, {_money(broker['equity'] - broker['last_equity'])} on the day." if broker.get("ok") else "The broker could not be reached for account figures."), ""]

    out += ["## MARKET", ""]
    regimes = con.execute("SELECT * FROM regime_day WHERE variant='expanding' AND day=? ORDER BY symbol", (d,)).fetchall()
    for r in regimes:
        probs = json.loads(r["probs"])
        ds = con.execute("SELECT id FROM dataset WHERE symbol=? AND timeframe='1Day' AND source='yahoo'", (r["symbol"],)).fetchone()
        closes = [x[0] for x in con.execute("SELECT close FROM bar WHERE dataset_id=? ORDER BY ts DESC LIMIT 21", (ds[0],))][::-1] if ds else []
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        vol = f"{(sum(x * x for x in rets) / len(rets)) ** 0.5 * math.sqrt(252):.0%} a year over the last 20 days" if len(rets) >= 5 else "no candles to measure"
        out.append(f"- **{r['symbol']}:** {r['active'] or 'no state yet'} ({probs.get(r['active'], 0):.0%})"
                   + (", **switched today**" if r["switched"] else "") + (", **new entries frozen**" if r["frozen"] else "")
                   + f". {r['reason']}. Volatility: {vol}.")
    if not regimes:
        out.append("- No regime record for this day.")
    out += ["- **Macroeconomic events:** not available. The news and economic calendar feed is not built yet.", ""]

    out += ["## BOTS", "", "Orders are decided at one close and filled at the next open, so today's fills come from yesterday's decisions.", "",
            "| Agent | Decision at today's close (acts at the next open) | Fills at today's open | Result today | Result so far | Open position | Premium at risk |",
            "|---|---|---|---|---|---|---|"]
    reasons = []
    best = worst = None
    for a in AGENTS.values():
        dec = con.execute("SELECT * FROM live_decision WHERE agent=? AND day=?", (a.id, d)).fetchone()
        trades = con.execute("SELECT * FROM live_trade WHERE agent=?", (a.id,)).fetchall()
        today = [t for t in trades if t["exit_day"] == d]
        pos = con.execute("SELECT * FROM live_position WHERE agent=?", (a.id,)).fetchone()
        mark = held.get(pos["contract"]) if pos else None
        since, moved = agent_mark(con, pos, mark)
        # a position opened today has only been ours since its fill, so its result today is its whole result
        day_pnl = sum(t["pnl"] for t in today) + (since if pos and pos["entry_day"] == d else moved)
        total = sum(t["pnl"] for t in trades) + since
        fills = len(today) + (1 if pos and pos["entry_day"] == d else 0)
        for t in today:
            best = t if best is None or t["pnl"] > best["pnl"] else best
            worst = t if worst is None or t["pnl"] < worst["pnl"] else worst
        if dec and dec["action"] not in ("NONE", "HOLD"):
            reasons.append(f"- {a.id} decided {dec['action']}: {dec['reason']}.")
        out.append(f"| {a.id} | {dec['action'] if dec else 'not trading'} | {fills} | {_money(day_pnl)} | {_money(total)} | "
                   f"{str(pos['qty']) + ' x ' + pos['contract'] if pos else 'none'} | {'$' + format(pos['entry_price'] * 100 * pos['qty'], ',.0f') if pos else '-'} |")
    out += [""] + reasons
    same = con.execute("SELECT contract, GROUP_CONCAT(agent, ' and ') FROM live_position GROUP BY contract HAVING COUNT(*) > 1").fetchall()
    out += [f"- {r[1]} hold the same contract ({r[0]}): that is one bet held twice, and the risk limits count it that way." for r in same]
    if best is not None:
        out.append(f"Largest gain today: {best['agent']} {_money(best['pnl'])} on {best['contract']} ({best['exit_reason']})."
                   + (f" Largest loss: {worst['agent']} {_money(worst['pnl'])} on {worst['contract']}." if worst["pnl"] < 0 else " No losing trade closed today."))
    else:
        out.append("No trade closed today.")
    out.append("")

    out += ["## MODEL", ""]
    for r in regimes:
        probs = json.loads(r["probs"])
        nxt = json.loads(r["next_probs"])
        out.append(f"- **{r['symbol']}** now: " + ", ".join(f"{s} {p:.0%}" for s, p in sorted(probs.items(), key=lambda kv: -kv[1])[:3])
                   + ". Tomorrow: " + ", ".join(f"{s} {p:.0%}" for s, p in sorted(nxt.items(), key=lambda kv: -kv[1])[:2]) + ".")
        recent = con.execute("SELECT SUM(switched), COUNT(*) FROM (SELECT switched FROM regime_day WHERE symbol=? AND variant='expanding' AND day<=? ORDER BY day DESC LIMIT 30)",
                             (r["symbol"], d)).fetchone()
        out.append(f"  State stability: {recent[0] or 0} switch(es) in the last {recent[1]} sessions." + (" **Drift warning:** " + r["reason"] if r["frozen"] else ""))
    fills = con.execute("SELECT COUNT(*) FROM live_trade").fetchone()[0]
    out += [f"- **Calibration:** not yet measurable. It needs {MIN_FILLS} finished paper trades for an agent in one market state; there are {fills} in all. "
            "Until then every trade stays at probe size.", ""]

    out += ["## RESEARCH", ""]
    start = int(time.mktime(day.timetuple()))
    exps = con.execute("SELECT * FROM experiment WHERE created_at BETWEEN ? AND ?", (start, start + 86400)).fetchall()
    queue = con.execute("SELECT COUNT(*) FROM approval WHERE status='pending'").fetchone()[0]
    open_cycles = con.execute("SELECT id, max_experiments FROM cycle WHERE status='open'").fetchall()
    out += [f"- Hypotheses generated today: {len({e['hypothesis'] for e in exps})}. Experiments attempted today: {len(exps)}.",
            "- Experiment budget remaining: " + ("; ".join(f"cycle {c['id']}: {c['max_experiments'] - con.execute('SELECT COUNT(*) FROM experiment WHERE cycle_id=?', (c['id'],)).fetchone()[0]}" for c in open_cycles)
                                                 or "no research cycle is open (a new cycle starts with 20)") + ".",
            f"- Challengers accepted today: {sum(e['decision'] == 'candidate' for e in exps)}. Rejected today: {sum(e['decision'] == 'rejected' for e in exps)}."]
    out += [f"  - {e['challenger_version']}: {e['reason']}" for e in exps if e["decision"] == "rejected"]
    out += [f"- Enough new evidence to start research: no. {fills} finished paper trade(s) so far; a cycle is worth opening after about {MIN_FILLS} for one agent, or when a failure pattern repeats.",
            "- Research lessons on record: " + str(con.execute("SELECT COUNT(*) FROM lesson").fetchone()[0]) + ".", ""]

    out += ["## RISK", ""]
    saved = con.execute("SELECT value FROM live_checkpoint WHERE name='risk'").fetchone()
    risk = json.loads(saved[0]) if saved else {}
    events = [e for e in risk.get("events", []) if e.get("day") == d]
    refused = con.execute("SELECT * FROM live_intent WHERE status='refused' AND (decided_day=? OR for_day=?)", (d, d)).fetchall()
    cancelled = con.execute("SELECT * FROM live_intent WHERE status='cancelled' AND for_day=?", (d,)).fetchall()
    out += [f"- Trading: {'**HALTED**: ' + risk.get('halt_reason', '') if risk.get('halted') else 'allowed'}.",
            f"- Kill-switch or halt events today: {sum(e.get('kind') in ('kill', 'halt') for e in events)}.",
            f"- Orders refused today: {len(refused)}." ]
    out += [f"  - {i['agent']} {i['side']} {i['contract'] or ''}: {i['reason']}" for i in refused]
    out += [f"- Execution anomalies: {len(cancelled)} order(s) sent but not filled and cancelled."] + [f"  - {i['agent']} {i['side']} {i['contract']}" for i in cancelled] + [""]

    out += ["## NEXT ACTIONS", "", f"- Waiting for your approval: {queue}."]
    waiting = con.execute("SELECT * FROM live_intent WHERE status IN ('pending', 'submitted')").fetchall()
    out.append("- Orders queued for the next open: " + ("; ".join(f"{i['agent']} {i['side']} {i['contract']}" for i in waiting) or "none") + ".")
    out.append("- Missing data: historical option bid/ask quotes (not offered by Alpaca); a news and economic calendar feed (not built).")
    bad = [c for c in (checks or []) if not c["ok"]]
    out.append("- Deployment issues: " + ("; ".join(f"{c['name']} ({c['detail']})" for c in bad) if bad else "none found by the health checks" if checks is not None else "health checks not run") + ".")
    return "\n".join(out) + "\n"


def save_report(path: str, day: date, text: str) -> None:
    con = sqlite3.connect(path, timeout=60)
    con.executescript(SCHEMA)
    with con:
        con.execute("INSERT OR REPLACE INTO daily_report VALUES (?,?,?)", (day.isoformat(), text, int(time.time())))


def latest_report(path: str) -> dict | None:
    con = sqlite3.connect(path, timeout=60)
    con.executescript(SCHEMA)
    row = con.execute("SELECT day, text FROM daily_report ORDER BY day DESC LIMIT 1").fetchone()
    return {"day": row[0], "text": row[1]} if row else None
