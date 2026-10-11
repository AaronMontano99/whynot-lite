"""Milestone 8: alerts, the daily research report, backups and health checks."""

import sqlite3
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from whynot import walkforward
from whynot.dash import views
from whynot.ingest.catalog import Catalog
from whynot.live.lifecycle import Lifecycle
from whynot.live.state import LiveDB
from whynot.ops import alerts, backup, health
from whynot.ops.report import daily_report
from whynot.researchlab.memory import OWNER_PHRASE, ResearchDB
from whynot.riskengine import RiskEngine, RiskSettings
from whynot.sim.agents import AGENTS

ET = ZoneInfo("America/New_York")
DAY = date(2026, 10, 9)
CALL = "QQQ261120C00753000"
BROKER = {"ok": True, "label": "Alpaca paper account", "equity": 100_270.0, "last_equity": 100_000.0,
          "positions": {CALL: {"qty": 2, "price": 20.1, "unrealized": 260.0, "unrealized_today": 260.0}}}


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "w.db")
    views.prepare(p)
    cat = Catalog(p)
    cat.set_calendar([("2026-10-08", "09:30", "16:00"), ("2026-10-09", "09:30", "16:00")], source="test")
    store = walkforward.RegimeStore(p)
    for symbol, active, switched, frozen in (("SPY", "CALM_UP", False, False), ("QQQ", "CHOP", True, False), ("IWM", "STRESS", False, True)):
        rec = walkforward.Record(day=DAY, version=3, probs={active: 0.9, "CALM": 0.1}, next_probs={active: 0.85, "CALM": 0.15}, top=active, entropy=0.3,
                                 loglik=-4.0, active=active, size=0.0 if frozen else 1.0, reason="frozen, no new entries: drift" if frozen else f"{active} at 0.90",
                                 switched=switched, frozen=frozen, expected_days=20.0)
        store.save(symbol, "expanding", walkforward.Result([], [rec]))
    research = ResearchDB(p)
    research.seed_champions(AGENTS)
    life = Lifecycle(p)
    life.complete_initialization("r")
    life.enter_paper(OWNER_PHRASE, ["Kip", "Nemo", "Chud", "Aura"], "owner said so")
    live = LiveDB(p)
    live.record_decisions(DAY, {"Kip": SimpleNamespace(action="HOLD", direction="LONG", state="CHOP", reason="holding"),
                                "Nemo": SimpleNamespace(action="EXIT", direction="LONG", state="CALM_UP", reason="price came back")})
    live.open_position(agent="Kip", underlying="QQQ", contract=CALL, qty=1, direction="LONG", state="CHOP", entry_day=DAY, entry_price=18.78, reason="x", simulated=False)
    live.open_position(agent="Nemo", underlying="SPY", contract="SPY261120C00778000", qty=1, direction="LONG", state="CALM_UP", entry_day="2026-10-08",
                       entry_price=14.0, reason="x", simulated=False)
    live.close_position("Nemo", DAY, 15.2, "signal exit: price came back", "Alpaca paper account")
    live.add_intent(agent="Aura", underlying="QQQ", side="BUY", contract=CALL, qty=1, direction="LONG", state="CHOP", est_price=18.8, decided_day=DAY,
                    status="refused", reason="risk: position risk $2,500 is above the 2.0% limit ($2,000)")
    live.set_checkpoint("close", DAY.isoformat())
    risk = RiskEngine(RiskSettings(), 100_000.0)
    risk.start_day(DAY, 100_000.0)
    live.save_risk(risk)
    return p


# ---------------------------------------------------------------- the daily research report

def test_the_daily_report_has_every_section_and_says_what_it_does_not_know(path):
    text = daily_report(path, DAY, BROKER)
    for heading in ("## MARKET", "## BOTS", "## MODEL", "## RESEARCH", "## RISK", "## NEXT ACTIONS"):
        assert heading in text
    assert "QQQ" in text and "CHOP" in text and "switched" in text.lower()           # a regime transition is called out
    assert "frozen" in text.lower() and "IWM" in text                                # so is a drift freeze
    assert "Nemo" in text and "+$120.00" in text                                     # the day's finished trade
    assert "position risk" in text                                                   # the refused order, with its reason
    assert "not built" in text.lower()                                               # macro events: said plainly, not invented
    assert "Experiments attempted today: 0" in text and "Waiting for your approval: 0" in text
    assert "calibration" in text.lower() and "30" in text                            # too few fills to judge, and it says so


def test_the_report_is_stored_once_per_day_and_served_to_the_dashboard(path):
    from whynot.ops.report import latest_report, save_report
    save_report(path, DAY, "first")
    save_report(path, DAY, "second")
    assert latest_report(path) == {"day": DAY.isoformat(), "text": "second"}


# ---------------------------------------------------------------- alerts

def test_alerts_are_queued_once_and_sent_through_whatever_is_configured(path):
    sent = []
    a = alerts.Alerter(path, senders=[("test", lambda text: sent.append(text) or True)])
    a.emit("fill", "Kip bought 1 QQQ call at 18.78", day=DAY)
    a.emit("fill", "Kip bought 1 QQQ call at 18.78", day=DAY)                        # the same event twice is one alert
    a.emit("kill switch", "drawdown 10.2% reached the limit", day=DAY)
    assert a.flush() == 2 and len(sent) == 2 and sent[1].startswith("[WHYNOT paper] KILL SWITCH")
    assert a.flush() == 0                                                            # nothing is sent twice
    assert [r["sent_via"] for r in a.recent()] == ["test", "test"]


def test_a_failing_channel_keeps_the_alert_for_next_time_and_never_shows_the_token(path, monkeypatch):
    def boom(text):
        raise RuntimeError("https://api.telegram.org/botSECRETTOKEN/sendMessage refused")
    a = alerts.Alerter(path, senders=[("telegram", boom)])
    a.emit("error", "the close step could not read option data", day=DAY)
    assert a.flush() == 0 and a.recent()[0]["sent_via"] is None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRETTOKEN")
    assert "SECRETTOKEN" not in (a.recent()[0]["error"] or "")
    assert alerts.configured_channels({"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}) == ["mac notification"]
    assert alerts.configured_channels({"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "1"}) == ["telegram", "mac notification"]


def test_the_days_events_become_alerts(path):
    a = alerts.Alerter(path, senders=[])
    alerts.from_day(a, path, DAY)
    kinds = sorted(r["kind"] for r in a.recent())
    assert kinds == ["drift warning", "fill", "fill", "order refused", "state switch"]   # Kip bought, Nemo sold, Aura refused, QQQ switched, IWM froze


# ---------------------------------------------------------------- backups

def test_a_backup_holds_everything_that_cannot_be_downloaded_again(path, tmp_path):
    dest = tmp_path / "backups"
    f = backup.backup(path, dest)
    con = sqlite3.connect(f)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"live_trade", "live_position", "experiment", "champion", "regime_day", "lifecycle_event", "dataset"} <= tables
    assert not {"bar", "option_bar", "contract"} & tables                            # bulk market data is re-downloadable and left out
    assert con.execute("SELECT COUNT(*) FROM live_trade").fetchone()[0] == 1
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for i in range(20):
        backup.backup(path, dest, stamp=f"2026-09-{i + 1:02d}", keep=14)
    assert len(list(dest.glob("whynot-*.db"))) == 14


# ---------------------------------------------------------------- health

def test_health_checks_name_what_is_wrong(path):
    now = datetime(2026, 10, 9, 19, 0, tzinfo=ET)
    ok = {c["name"]: c for c in health.checks(path, BROKER, now, jobs={"com.whynot.paper.open", "com.whynot.paper.close", "com.whynot.dash"}, dashboard_up=True)}
    assert ok["database"]["ok"] and ok["last close processed"]["ok"] and ok["scheduled jobs"]["ok"] and ok["broker"]["ok"]
    assert not ok["books match the broker"]["ok"]                                    # we hold 1 QQQ call, the broker says 2
    LiveDB(path).set_checkpoint("close", "2026-10-08")
    bad = {c["name"]: c for c in health.checks(path, {"ok": False, "error": "timeout"}, now, jobs={"com.whynot.dash"}, dashboard_up=False)}
    assert not bad["last close processed"]["ok"] and "2026-10-09" in bad["last close processed"]["detail"]
    assert not bad["scheduled jobs"]["ok"] and "com.whynot.paper.open" in bad["scheduled jobs"]["detail"]
    assert not bad["broker"]["ok"] and not bad["dashboard"]["ok"]
    assert not bad["recent backup"]["ok"]
