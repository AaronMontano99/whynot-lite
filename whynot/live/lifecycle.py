"""The four modes, and who may move between them.

HISTORICAL_INITIALIZATION ends once and is never re-entered. Finishing it does not start
paper trading: that takes the owner's phrase. After that the system moves between
LIVE_PAPER_TRADING, RESEARCH and VALIDATION."""

import json
import sqlite3
import time
from pathlib import Path

from ..researchlab.memory import OWNER_PHRASE

MODES = ("HISTORICAL_INITIALIZATION", "LIVE_PAPER_TRADING", "RESEARCH", "VALIDATION")
ALLOWED = {("LIVE_PAPER_TRADING", "RESEARCH"), ("RESEARCH", "VALIDATION"), ("RESEARCH", "LIVE_PAPER_TRADING"), ("VALIDATION", "LIVE_PAPER_TRADING")}


class Lifecycle:
    def __init__(self, path="data/whynot.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS lifecycle_event (id INTEGER PRIMARY KEY, mode TEXT NOT NULL, note TEXT, at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS lifecycle_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);""")

    def _meta(self, name, default=None):
        row = self.db.execute("SELECT value FROM lifecycle_meta WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, name, value):
        self.db.execute("INSERT OR REPLACE INTO lifecycle_meta VALUES (?,?)", (name, json.dumps(value)))

    @property
    def mode(self) -> str:
        row = self.db.execute("SELECT mode FROM lifecycle_event ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else "HISTORICAL_INITIALIZATION"

    @property
    def agents(self) -> list:
        return self._meta("paper_agents", [])

    def history(self) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM lifecycle_event ORDER BY id")]

    def _move(self, mode: str, note: str) -> None:
        self.db.execute("INSERT INTO lifecycle_event (mode, note, at) VALUES (?,?,?)", (mode, note, int(time.time())))

    def complete_initialization(self, report: str) -> None:
        if self._meta("initialized"):
            raise RuntimeError("historical initialization already finished; it does not run again")
        with self.db:
            self._set("initialized", {"report": report, "at": int(time.time())})

    def enter_paper(self, phrase: str, agents: list, note: str) -> None:
        if phrase != OWNER_PHRASE:
            raise PermissionError("only the owner can start paper trading, with the exact phrase")
        if not self._meta("initialized"):
            raise RuntimeError("historical initialization has not been reported complete")
        if self.mode != "HISTORICAL_INITIALIZATION":
            raise RuntimeError(f"already in {self.mode}")
        with self.db:
            self._set("paper_agents", list(agents))
            self._move("LIVE_PAPER_TRADING", note)

    def to(self, mode: str, note: str = "") -> None:
        if (self.mode, mode) not in ALLOWED:
            raise RuntimeError(f"the system does not move from {self.mode} to {mode}")
        with self.db:
            self._move(mode, note)
