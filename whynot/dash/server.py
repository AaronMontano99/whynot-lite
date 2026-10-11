"""The dashboard server: static pages, read-only JSON, and a WebSocket that pushes the
city's state. It runs as its own process and holds no trading logic."""

import asyncio
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import views

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


class BrokerCache:
    """Asks the broker for the account and positions every half minute, read-only, in the background."""

    def __init__(self):
        self.value = {"ok": False, "label": "Alpaca paper account", "error": "not asked yet"}

    def loop(self):
        while True:
            try:
                from ..ingest.alpaca import Alpaca
                from ..live.broker import AlpacaPaperBroker
                self.value = AlpacaPaperBroker(Alpaca.from_env()).snapshot()
            except Exception as err:
                self.value = {"ok": False, "label": "Alpaca paper account", "error": str(err)[:120]}
            time.sleep(30)


def create_app(path: str = "data/whynot.db") -> FastAPI:
    views.prepare(path)
    app, broker, clients, slow = FastAPI(title="Project WHYNOT"), BrokerCache(), set(), {}

    def cached(name, seconds, make):
        hit = slow.get(name)
        if hit is None or time.time() - hit[0] > seconds:
            slow[name] = (time.time(), make())
        return slow[name][1]

    def guarded(make):
        try:
            return make()
        except PermissionError as err:
            return JSONResponse({"error": str(err)}, status_code=403)
        except (KeyError, ValueError) as err:
            return JSONResponse({"error": f"bad request: {err}"}, status_code=400)

    @app.on_event("startup")
    async def _startup():
        threading.Thread(target=broker.loop, daemon=True).start()
        app.state.push = asyncio.create_task(push())

    async def push():
        while True:
            await asyncio.sleep(2)
            if not clients:
                continue
            try:
                text = json.dumps(await asyncio.to_thread(views.state, path, broker.value), default=str)
            except Exception as err:
                text = json.dumps({"type": "error", "error": str(err)[:200]})
            for ws in list(clients):
                try:
                    await ws.send_text(text)
                except Exception:
                    clients.discard(ws)

    app.get("/api/state")(lambda: views.state(path, broker.value))
    app.get("/api/live")(lambda: views.live(path))
    app.get("/api/regimes")(lambda: views.regimes(path))
    app.get("/api/research")(lambda: views.research(path))
    app.get("/api/risk")(lambda: views.risk(path, broker.value))
    app.get("/api/health")(lambda: cached("health", 60, lambda: views.health(path)))

    @app.get("/api/daily")
    def _daily():
        from ..ops.report import latest_report
        return latest_report(path) or {"day": None, "text": "No daily report yet. The first is written after the next close."}

    @app.get("/api/checks")
    def _checks():
        from datetime import datetime
        from ..ingest.timeutil import ET
        from ..ops import alerts, health
        return cached("checks", 120, lambda: {"checks": health.checks(path, broker.value, datetime.now(ET), dashboard_up=True),
                                              "alerts": alerts.Alerter(path, senders=[]).recent(30)[::-1], "channels": alerts.configured_channels()})

    @app.get("/api/journal")
    def _journal(q: str = "", agent: str = "", source: str = ""):
        return views.journal(path, q, agent, source)

    @app.get("/api/replay")
    def _replay(agent: str, start: str, end: str):
        return guarded(lambda: cached(f"replay {agent} {start} {end}", 3600, lambda: views.replay(path, agent, start, end)))

    @app.get("/api/report/{name}")
    def _report(name: str):
        allowed = {"data": "data_availability.md", "regime": "regime.md", "backtest": "backtest_development.md", "options": "options_2024.md",
                   "research": "research_cycle_1.md"}
        f = Path("data/reports") / allowed.get(name, "")
        return {"text": f.read_text() if name in allowed and f.exists() else "No such report yet."}

    @app.websocket("/ws")
    async def _ws(ws: WebSocket):
        await ws.accept()
        await ws.send_text(json.dumps(views.state(path, broker.value), default=str))
        clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            clients.discard(ws)

    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
    return app
