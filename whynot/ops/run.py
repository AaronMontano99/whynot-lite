"""What runs around the two trading steps: after the close, the report, the review, the
backup, the health checks and the alerts; after the open, the alerts for what filled."""

from datetime import date, datetime

from ..ingest.timeutil import ET
from . import alerts, backup, health
from .report import daily_report, save_report
from .review import nightly_review


def _broker() -> dict:
    try:
        from ..ingest.alpaca import Alpaca
        from ..live.broker import AlpacaPaperBroker
        return AlpacaPaperBroker(Alpaca.from_env()).snapshot()
    except Exception as err:
        return {"ok": False, "label": "Alpaca paper account", "error": str(err)[:120]}


def after_session(path="data/whynot.db", review: bool = True, log=print) -> None:
    from ..live.state import LiveDB
    done = LiveDB(path).checkpoint("close")
    alerter, broker, now = alerts.Alerter(path), _broker(), datetime.now(ET)
    try:
        saved = backup.backup(path)
        log(f"backup written: {saved.name}")
    except Exception as err:
        alerter.emit("error", f"backup failed: {str(err)[:150]}", now.date())
    found = health.checks(path, broker, now)
    for c in found:
        if not c["ok"]:
            alerter.emit("health", f"{c['name']}: {c['detail']}", now.date())
    if done and LiveDB(path).checkpoint("report") != done:
        day = date.fromisoformat(done)
        text = daily_report(path, day, broker, found)
        if review:
            notes, why = nightly_review(text)
            text += "\n## CLAUDE'S REVIEW (advice only; it changes nothing)\n\n" + (notes or f"Not available tonight: {why}.") + "\n"
        save_report(path, day, text)
        from pathlib import Path
        out = Path(path).parent / "reports" / "daily"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{done}.md").write_text(text)
        LiveDB(path).set_checkpoint("report", done)
        alerts.from_day(alerter, path, day)
        alerter.emit("daily report", f"{done}: " + (f"account ${broker['equity']:,.2f}, {broker['equity'] - broker['last_equity']:+,.2f} today" if broker.get("ok") else "written"), day)
        log(f"daily report written for {done}")
    log(f"health: {sum(c['ok'] for c in found)} of {len(found)} checks passed" + "".join(f"\n  FAILED {c['name']}: {c['detail']}" for c in found if not c["ok"]))
    log(f"alerts sent: {alerter.flush()}")


def after_open(path="data/whynot.db", log=print) -> None:
    alerter, day = alerts.Alerter(path), datetime.now(ET).date()
    alerts.from_day(alerter, path, day)
    log(f"alerts sent: {alerter.flush()}")
