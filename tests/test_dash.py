"""Milestone 7: what the dashboard shows. The views read the database and never write it."""

import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from whynot import walkforward
from whynot.dash import views
from whynot.ingest.catalog import Catalog
from whynot.live.lifecycle import Lifecycle
from whynot.live.state import LiveDB
from whynot.regime import Candle
from whynot.researchlab.memory import OWNER_PHRASE, ResearchDB
from whynot.riskengine import RiskEngine, RiskSettings
from whynot.sim.agents import AGENTS
from whynot.sim.daily import Decision, Result, Trade, run_daily
from whynot.sim.ledger import Ledger

TOWER_FIELDS = {"name", "symbol", "regime", "strategy", "status", "signal", "position", "day_pnl", "total_pnl", "win_rate", "drawdown"}
CALL = "QQQ261120C00600000"
BROKER = {"ok": True, "label": "Alpaca paper account", "equity": 100_250.0, "last_equity": 100_100.0,
          "positions": {CALL: {"qty": 1, "price": 16.0, "unrealized": 120.0, "unrealized_today": 50.0}}}


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "w.db")
    Catalog(p)
    views.prepare(p)
    store = walkforward.RegimeStore(p)
    rec = walkforward.Record(day=date(2026, 10, 8), version=3, probs={"CALM_UP": 0.9, "CHOP": 0.1}, next_probs={"CALM_UP": 0.88, "CHOP": 0.12},
                             top="CALM_UP", entropy=0.3, loglik=-4.0, active="CALM_UP", size=1.0, reason="CALM_UP at 0.90", switched=False,
                             frozen=False, expected_days=30.0)
    for symbol in ("SPY", "QQQ", "IWM"):
        store.save(symbol, "expanding", walkforward.Result([], [rec]))
    ResearchDB(p).seed_champions(AGENTS)
    life = Lifecycle(p)
    life.complete_initialization("r")
    life.enter_paper(OWNER_PHRASE, ["Kip", "Nemo", "Chud", "Aura"], "owner said so")
    live = LiveDB(p)
    live.record_decisions(date(2026, 10, 8), {"Kip": SimpleNamespace(action="ENTER", direction="LONG", state="CALM_UP", reason="trend up")})
    live.open_position(agent="Kip", underlying="QQQ", contract=CALL, qty=1, direction="LONG", state="CALM_UP", entry_day="2026-10-01",
                       entry_price=14.8, reason="x", simulated=False)
    live.open_position(agent="Nemo", underlying="SPY", contract="SPY261120C00670000", qty=1, direction="LONG", state="CHOP",
                       entry_day="2026-10-09", entry_price=10.0, reason="x", simulated=False)
    live.close_position("Nemo", date(2026, 10, 12), 9.0, "signal exit", "Alpaca paper account")
    live.add_intent(agent="Aura", underlying="QQQ", side="BUY", contract="QQQ261120C00610000", qty=1, direction="LONG", state="CALM_UP",
                    est_price=9.0, decided_day=date(2026, 10, 12), status="pending", reason="breakout")
    risk = RiskEngine(RiskSettings(), 100_000.0)
    risk.start_day(date(2026, 10, 12), 100_000.0)
    live.save_risk(risk)
    return p


def test_every_tower_gets_every_figure_the_spec_lists(path):
    s = views.state(path, BROKER)
    assert [b["id"] for b in s["bots"]] == ["Kip", "Nemo", "Chud", "Era", "Aura"]
    for b in s["bots"]:
        assert TOWER_FIELDS <= set(b)
        assert b["regime"]["active"] == "CALM_UP" and b["strategy"]["version"].endswith("-v1") and b["strategy"]["status"] == "provisional"
    kip, nemo, era, aura = (next(b for b in s["bots"] if b["id"] == n) for n in ("Kip", "Nemo", "Era", "Aura"))
    assert kip["status"] == "PAPER TRADING" and kip["state"] == "IN_TRADE" and kip["signal"]["action"] == "ENTER"
    assert kip["position"]["contract"] == CALL and kip["position"]["unrealized"] == 120.0 and kip["position"]["simulated"] is False
    assert kip["day_pnl"] == 50.0 and kip["total_pnl"] == 120.0
    assert nemo["total_pnl"] == -100.0 and nemo["win_rate"] == 0.0 and nemo["trades"] == 1 and nemo["drawdown"] == pytest.approx(100 / 20_000)
    assert era["status"] == "NOT TRADING" and era["state"] == "STANDBY" and "intraday" in era["status_note"]
    assert aura["state"] == "ARMED"                                                   # an order is waiting for the open
    assert s["vault"]["equity"] == 100_250.0 and s["vault"]["day_pnl"] == 150.0 and s["vault"]["broker"] == "Alpaca paper account"
    assert s["mode"] == "LIVE_PAPER_TRADING" and s["standby"] is False


def test_without_the_broker_the_page_still_works_and_says_so(path):
    s = views.state(path, {"ok": False, "label": "Alpaca paper account", "error": "no network"})
    assert s["vault"]["equity"] is None and "no network" in s["vault"]["note"]
    assert next(b for b in s["bots"] if b["id"] == "Kip")["position"]["unrealized"] is None


def test_the_views_cannot_write(path):
    con = views.connect(path)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM live_position")
    assert len(LiveDB(path).positions()) == 1


def test_the_journal_is_searchable_and_every_row_says_what_made_it(path):
    ledger = Ledger(path)
    t = Trade("LONG", "trend_long", "CALM_UP", date(2010, 3, 1), 45.0, 400, 1.0, 42.0, "trend up", exit_day=date(2010, 3, 9), exit_price=47.0,
              exit_reason="close fell below its 50-day average", pnl=800.0, ret=0.044)
    ledger.save(AGENTS["Kip"], Result([], [t], [(date(2010, 3, 1), 20_000.0), (date(2010, 3, 9), 20_800.0)]), "DEVELOPMENT", "v1 signal test", {})
    rows = views.journal(path)["rows"]
    kinds = {r["source"] for r in rows}
    assert "Alpaca paper account" in kinds and "Backtest (simulated)" in kinds
    assert all(r["simulated"] in (True, False) for r in rows)
    assert [r["agent"] for r in views.journal(path, q="50-day")["rows"]] == ["Kip"]
    assert {r["agent"] for r in views.journal(path, agent="Nemo")["rows"]} == {"Nemo"}
    assert views.journal(path, q="nothing matches this")["rows"] == []


def test_risk_research_live_and_health_views(path):
    r = views.risk(path, BROKER)
    assert r["halted"] is False and r["settings"]["max_drawdown"] == 0.10 and "kill switch" in r["how_to_reset"]
    res = views.research(path)
    assert len(res["champions"]) == 5 and res["approval_queue"] == [] and res["holdout_looks"] == 0 and res["limits"]["max_experiments"] == 20
    live = views.live(path)
    assert [i["agent"] for i in live["waiting"]] == ["Aura"] and live["positions"][0]["agent"] == "Kip"
    assert live["decisions"][0]["action"] == "ENTER" and len(live["trades"]) == 1
    h = views.health(path)
    assert h["last_close_processed"] is None and isinstance(h["datasets"], list) and h["mode"] == "LIVE_PAPER_TRADING"
    reg = views.regimes(path)
    assert reg["QQQ"]["active"] == "CALM_UP" and reg["QQQ"]["probs"]["CALM_UP"] == 0.9 and reg["QQQ"]["expected_days"] == 30.0


# ---------------------------------------------------------------- historical replay

def candles(n=400):
    out, price = [], 100.0
    for i in range(n):
        price *= 1.004 if (i // 40) % 2 == 0 else 0.997
        out.append(Candle(date(2010, 1, 4) + timedelta(days=i), price, price * 1.01, price * 0.99, price, 1e6))
    return out


def recs(cs):
    return {c.day: SimpleNamespace(active="CALM_UP", size=1.0, frozen=False, switched=False, top="CALM_UP", probs={"CALM_UP": 0.9},
                                   next_probs={}, reason="t") for c in cs}


def test_replay_is_the_engine_itself_day_by_day_and_always_the_same():
    cs = candles()
    start, end = cs[100].day, cs[399].day
    frames = views.replay_frames(AGENTS["Kip"], cs, recs(cs), start, end, cost_bps=1.0)
    direct = run_daily(AGENTS["Kip"], cs, recs(cs), start, end, 1.0)
    assert [f["day"] for f in frames["days"]] == [d.isoformat() for d, _ in direct.equity]
    assert [f["equity"] for f in frames["days"]] == [round(v, 2) for _, v in direct.equity]
    assert [f["action"] for f in frames["days"]] == [d.action for d in direct.decisions]
    assert len(frames["trades"]) == len(direct.trades) and frames["version"] == "kip-v1" and frames["simulated"] is True
    last = frames["days"][-1]
    assert last["realized"] + last["unrealized"] == pytest.approx(last["equity"] - 20_000, abs=0.02)
    assert max(f["drawdown"] for f in frames["days"]) > 0 and frames["days"][0]["regime"] == "CALM_UP"
    assert views.replay_frames(AGENTS["Kip"], cs, recs(cs), start, end, cost_bps=1.0) == frames        # speed is the viewer's business; the result is fixed


def test_replay_is_refused_outside_the_development_years(path):
    for start, end in (("2021-01-04", "2021-06-30"), ("2025-02-03", "2025-06-30"), ("2019-06-03", "2020-01-31")):
        with pytest.raises(PermissionError):
            views.replay(path, "Kip", start, end)
    with pytest.raises(KeyError):
        views.replay(path, "Nobody", "2010-01-04", "2010-06-30")


def test_a_position_opened_today_counts_its_whole_result_as_todays_and_a_shared_contract_is_split(path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    live = LiveDB(path)
    live.open_position(agent="Kip", underlying="QQQ", contract=CALL, qty=1, direction="LONG", state="CALM_UP", entry_day=today, entry_price=14.8, reason="x", simulated=False)
    live.open_position(agent="Aura", underlying="QQQ", contract=CALL, qty=1, direction="LONG", state="CALM_UP", entry_day=today, entry_price=14.8, reason="x", simulated=False)
    both = {**BROKER, "positions": {CALL: {"qty": 2, "price": 16.0, "unrealized": 240.0, "unrealized_today": 900.0}}}
    s = views.state(path, both)
    kip, aura = (next(b for b in s["bots"] if b["id"] == n) for n in ("Kip", "Aura"))
    assert kip["day_pnl"] == aura["day_pnl"] == 120.0 and kip["total_pnl"] == 120.0 and kip["position"]["unrealized"] == 120.0
