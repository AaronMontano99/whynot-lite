"""Health checks. Each names one thing, says whether it is all right, and why not."""

import json
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

from ..ingest.timeutil import et_time
from . import backup

JOBS = ("com.whynot.paper.open", "com.whynot.paper.close", "com.whynot.dash")


def loaded_jobs() -> set:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15).stdout
    return {line.split("\t")[-1].strip() for line in out.splitlines() if "com.whynot" in line}


def dashboard_responds(port: int = 8090) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def checks(path: str, broker: dict, now: datetime, jobs: set | None = None, dashboard_up: bool | None = None) -> list:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        out = []

        def add(name, ok, detail=""):
            out.append({"name": name, "ok": bool(ok), "detail": detail})

        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        add("database", quick == "ok", "" if quick == "ok" else quick[:200])

        due = None                                          # the latest session that ended at least 90 minutes ago
        for s in con.execute("SELECT day, close FROM session WHERE day<=? ORDER BY day DESC LIMIT 10", (now.date().isoformat(),)):
            if now.timestamp() >= et_time(date.fromisoformat(s["day"]), s["close"]) + 5400:
                due = s["day"]
                break
        done = (con.execute("SELECT value FROM live_checkpoint WHERE name='close'").fetchone() or [None])[0]
        add("last close processed", due is None or (done or "") >= due, f"processed through {done or 'nothing'}; the session of {due} is due" if due else "")

        jobs = loaded_jobs() if jobs is None else jobs
        missing = [j for j in JOBS if j not in jobs]
        add("scheduled jobs", not missing, "not loaded: " + ", ".join(missing) if missing else "")

        add("broker", broker.get("ok"), broker.get("error", "") if not broker.get("ok") else "")
        if broker.get("ok"):
            ours = {}
            for p in con.execute("SELECT contract, qty FROM live_position"):
                ours[p["contract"]] = ours.get(p["contract"], 0) + p["qty"]
            theirs = {c: v["qty"] for c, v in broker.get("positions", {}).items()}
            diff = [f"{c}: ours {ours.get(c, 0)}, broker {theirs.get(c, 0)}" for c in sorted(set(ours) | set(theirs)) if ours.get(c, 0) != theirs.get(c, 0)]
            add("books match the broker", not diff, "; ".join(diff))

        up = dashboard_responds() if dashboard_up is None else dashboard_up
        add("dashboard", up, "" if up else "http://127.0.0.1:8090 is not answering")

        last = backup.latest(Path(path).parent / "backups")
        fresh = last is not None and time.time() - last.stat().st_mtime < 2 * 86400
        add("recent backup", fresh, "" if fresh else f"latest: {last.name if last else 'none'}")

        free = shutil.disk_usage(Path(path).parent).free / 1e9
        add("disk space", free >= 5, f"{free:.0f} GB free")

        saved = con.execute("SELECT value FROM live_checkpoint WHERE name='risk'").fetchone()
        risk = json.loads(saved[0]) if saved else {}
        add("trading allowed", not risk.get("halted"), risk.get("halt_reason", ""))

        stuck = con.execute("SELECT COUNT(*) FROM live_intent WHERE status IN ('pending', 'submitted') AND decided_day < date(?, '-4 day')",
                            (now.date().isoformat(),)).fetchone()[0]
        add("no stuck orders", stuck == 0, f"{stuck} order(s) have waited more than four days" if stuck else "")
        return out
    finally:
        con.close()
