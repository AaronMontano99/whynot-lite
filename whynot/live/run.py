"""Paper trading as it runs on this machine: the commands behind `whynot paper ...`."""

import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from .. import walkforward
from ..ingest import pipeline
from ..ingest.alpaca import Alpaca
from ..ingest.catalog import Catalog
from ..ingest.timeutil import ET, et_time
from ..opt.data import CatalogOptionData
from ..opt.engine import SelectionRule
from ..researchlab.memory import OWNER_PHRASE
from ..riskengine import RiskSettings
from ..sim.agents import AGENTS
from ..sim.daily import run_daily
from ..sim.run import COST_BPS, regime_records
from .broker import AlpacaPaperBroker
from .lifecycle import Lifecycle
from .state import LiveDB
from .steps import close_step, open_step, reconcile

LIVE_START = date(2026, 10, 8)              # the first session after the end of stored history
PAPER_AGENTS = ["Kip", "Nemo", "Chud", "Aura"]
# A record of the owner approval and when it was given; the code only stores it.
APPROVAL = ("Owner approval recorded 2026-10-07 for the four daily agents. The risk settings (2% a position, 5% an agent, 6% one "
            "direction, 10% of the portfolio, 2% daily and 4% weekly loss limits, 10% drawdown kill switch) were listed to the owner "
            "beforehand and none were changed. All four champions are provisional version-one strategies that have not "
            "passed the gates; this is paper trading at probe size to collect forward evidence.")


def start(path="data/whynot.db") -> str:
    life = Lifecycle(path)
    if life.mode != "HISTORICAL_INITIALIZATION":
        return f"already in {life.mode}"
    try:
        life.complete_initialization("data/reports/backtest_development.md, options_2024.md, research_cycle_1.md")
    except RuntimeError:
        pass
    life.enter_paper(OWNER_PHRASE, PAPER_AGENTS, APPROVAL)
    LiveDB(path).note("paper trading approved", agents=PAPER_AGENTS, note=APPROVAL)
    return f"LIVE_PAPER_TRADING for {', '.join(PAPER_AGENTS)}; first decisions at the close of {LIVE_START}"


def last_session(cat, now: datetime) -> date | None:
    """The most recent session that has finished."""
    today = now.astimezone(ET).date()
    for s in reversed(cat.sessions((today - timedelta(days=10)).isoformat(), today.isoformat())):
        if now.timestamp() >= et_time(date.fromisoformat(s["day"]), s["close"]):
            return date.fromisoformat(s["day"])
    return None


def _refresh_options(cat, client, symbol, day, spot, rule, log) -> None:
    """Today's real traded bars for the contracts a new trade could choose from."""
    wanted = []
    for d in range(rule.min_dte, 75):
        exp = day + timedelta(days=d)
        if exp.weekday() == 4 and 15 <= exp.day <= 21:
            rows = [(c["symbol"], symbol, c["type"], float(c["strike_price"]), c["expiration_date"], c["status"])
                    for c in client.option_contracts(symbol, "active", exp.isoformat(), exp.isoformat())]
            cat.add_contracts(rows, source="alpaca")
            wanted += [r[0] for r in rows if abs(r[3] / spot - 1) <= 0.06]
    got = pipeline.ingest_option_bars(cat, client, wanted, day.isoformat(), day.isoformat()) if wanted else 0
    log(f"  {symbol}: {len(wanted)} candidate contracts, {got} traded today")


def close(path="data/whynot.db", log=print, now=None) -> None:
    life, db, cat = Lifecycle(path), LiveDB(path), Catalog(path)
    if life.mode != "LIVE_PAPER_TRADING":
        return log(f"not paper trading (mode is {life.mode}); nothing done")
    client = Alpaca.from_env()
    broker, settings, rule = AlpacaPaperBroker(client), RiskSettings(), SelectionRule()
    pipeline.ingest_calendar(cat, client, start=(date.today() - timedelta(days=30)).isoformat())
    day = last_session(cat, now or datetime.now(ET))
    if day is None or day < LIVE_START:
        return log(f"paper trading starts with the close of {LIVE_START}; the last finished session is {day}")
    if (db.checkpoint("close") or "") >= day.isoformat():
        return log(f"{day} is already processed")
    risk = db.load_risk(settings, broker.account()["equity"])
    problems = reconcile(broker, db, risk)
    if problems:
        return log("STOPPED: " + "; ".join(problems))
    store = walkforward.RegimeStore(path)
    symbols = {a: AGENTS[a].symbol for a in life.agents}
    candles, spots = {}, {}
    for symbol in sorted(set(symbols.values())):
        pipeline._optional(log, pipeline.ingest_yahoo, cat, symbol, "ETF_HISTORY")
        candles[symbol] = walkforward.candles_from_catalog(cat, symbol)
        if not candles[symbol] or candles[symbol][-1].day != day:
            risk.failure(f"no daily candle for {symbol} on {day}")
            db.save_risk(risk)
            db.note("stale data", symbol=symbol, day=day, latest=candles[symbol][-1].day if candles[symbol] else None)
            return log(f"STALE DATA: the latest {symbol} candle is {candles[symbol][-1].day}, not {day}. Nothing decided; will try again on the next run.")
        spots[symbol] = candles[symbol][-1].close
        known = {m.version: m for m in store.models(symbol, "expanding")}
        store.save(symbol, "expanding", walkforward.walk(candles[symbol], walkforward.FIRST_FIT, known=known))
        try:
            _refresh_options(cat, client, symbol, day, spots[symbol], rule, log)
        except Exception as err:                                # no option data means no decisions today, not a crash
            risk.failure(f"option data for {symbol} could not be read")
            db.save_risk(risk)
            db.note("data error", symbol=symbol, day=day, error=str(err)[:200])
            return log(f"DATA ERROR: today's option prices for {symbol} could not be read ({str(err)[:120]}). Nothing decided; will try again on the next run.")
    risk.success()

    def decide(agent):
        a = AGENTS[agent]
        recs = regime_records(store, a.symbol, day)
        out = run_daily(a, candles[a.symbol], recs, LIVE_START, day, COST_BPS[a.symbol])
        d = out.decisions[-1]
        rec = recs.get(day)
        return SimpleNamespace(action=d.action if d.day == day else "NONE", direction=d.direction, state=d.state, fraction=d.fraction or 1.0,
                               reason=d.reason, probs=dict(rec.probs) if rec else {},
                               stopped=any(t.exit_day == day and t.exit_reason == "stop" for t in out.trades))

    rate = (cat.bars(cat.find("DFF", "1Day", "fred")) or [{"close": 0.0}])[-1]["close"] / 100.0
    queued = close_step(day, symbols, decide, CatalogOptionData(cat), spots, broker, db, risk, rule, rate)
    for agent in symbols:
        d = decide(agent)
        log(f"  {agent}: {d.action} ({d.state}) {d.reason}")
    log(f"{day}: {len(queued)} order(s) queued for the next open" + (f"; TRADING HALTED: {risk.halt_reason}" if risk.halted else ""))
    for i in db.intents():
        if i["decided_day"] == day.isoformat():
            log(f"  {i['status']}: {i['agent']} {i['side']} {i['qty'] or ''} {i['contract'] or ''} - {i['reason']}")


def open_(path="data/whynot.db", log=print, wait=True) -> None:
    life, db = Lifecycle(path), LiveDB(path)
    if life.mode != "LIVE_PAPER_TRADING":
        return log(f"not paper trading (mode is {life.mode}); nothing done")
    client = Alpaca.from_env()
    broker = AlpacaPaperBroker(client)
    if not broker.clock()["is_open"]:
        return log("the market is not open; nothing sent")
    day = datetime.now(ET).date()
    risk = db.load_risk(RiskSettings(), broker.account()["equity"])
    problems = reconcile(broker, db, risk)
    if problems:
        return log("STOPPED: " + "; ".join(problems))
    while True:
        done = open_step(day, broker, db, risk, SelectionRule(), now=datetime.now(ET))
        if not wait or not any(i["status"] == "submitted" for i in done):
            break
        time.sleep(30)
    for i in done:
        log(f"  {i['status']}: {i['agent']} {i['side']} {i['qty']} {i['contract']} at {i['fill_price'] or i['est_price']} - {i['reason']}")
    log(f"{day}: {len(done)} order(s) handled")


def status(path="data/whynot.db") -> str:
    life, db = Lifecycle(path), LiveDB(path)
    out = [f"mode: {life.mode}; agents: {', '.join(life.agents) or 'none'}", f"last close processed: {db.checkpoint('close') or 'none yet'}"]
    try:
        broker = AlpacaPaperBroker(Alpaca.from_env())
        risk = db.load_risk(RiskSettings(), broker.account()["equity"])
        out.append(f"{broker.label}: equity ${broker.account()['equity']:,.2f}; trading {'HALTED: ' + risk.halt_reason if risk.halted else 'allowed'}")
    except Exception as err:
        out.append(f"broker not reachable: {err}")
    out.append("positions: " + ("; ".join(f"{p['agent']} {p['qty']} x {p['contract']} at {p['entry_price']}" for p in db.positions()) or "none"))
    out.append("waiting orders: " + ("; ".join(f"{i['agent']} {i['side']} {i['contract']}" for i in db.intents(status='pending')) or "none"))
    trades = db.trades()
    out.append(f"finished trades: {len(trades)}, profit or loss ${sum(t['pnl'] for t in trades):+,.2f}")
    return "\n".join(out)


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{exe}</string><string>paper</string><string>{action}</string></array>
  <key>WorkingDirectory</key><string>{cwd}</string>
  <key>StandardOutPath</key><string>{cwd}/data/paper.log</string>
  <key>StandardErrorPath</key><string>{cwd}/data/paper.log</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>StartCalendarInterval</key><array>{times}</array>
</dict></plist>
"""


def schedule_files(cwd: Path) -> dict:
    """launchd jobs: the open step at 09:52 New York time and the close step at 16:25 and
    17:10 (a second try in case the day's candle was late), Monday to Friday, in this Mac's own clock."""
    def local(hh, mm):
        t = datetime.now(ET).replace(hour=hh, minute=mm, second=0, microsecond=0).astimezone()
        return t.hour, t.minute
    jobs = {"open": [local(9, 52)], "close": [local(16, 25), local(17, 10)]}
    out = {}
    for action, times in jobs.items():
        entries = "".join(f"<dict><key>Weekday</key><integer>{wd}</integer><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>"
                          for wd in range(1, 6) for h, m in times)
        out[f"com.whynot.paper.{action}"] = PLIST.format(label=f"com.whynot.paper.{action}", exe=cwd / ".venv/bin/whynot", action=action, cwd=cwd, times=entries)
    return out
