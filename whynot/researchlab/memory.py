"""The research memory and its rules, in one SQLite file.

What the code enforces, whatever a researcher (human or AI) would like:
  - a cycle cannot open without a hypothesis, datasets, metrics, a stopping rule and a time budget;
  - at most 20 experiments per cycle and 5 configurations per hypothesis;
  - after 3 failed cycles in a row on one direction, the owner must review before a fourth;
  - a failed experiment is not repeated unless new evidence is stated;
  - limits change only with the owner's phrase, and the change is logged;
  - only the owner promotes a challenger, declines one, or opens the locked holdout."""

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

OWNER_PHRASE = "I, the owner, approve this"
FAILED = "no challenger passed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, detail TEXT, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cycle (
  id INTEGER PRIMARY KEY, direction TEXT NOT NULL, agent TEXT NOT NULL, hypothesis TEXT NOT NULL, motivation TEXT, datasets TEXT NOT NULL,
  metrics TEXT NOT NULL, stopping TEXT NOT NULL, max_seconds INTEGER NOT NULL, max_experiments INTEGER NOT NULL, max_configs INTEGER NOT NULL,
  status TEXT NOT NULL, outcome TEXT, report TEXT, opened_at INTEGER NOT NULL, closed_at INTEGER);
CREATE TABLE IF NOT EXISTS experiment (
  id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL, agent TEXT NOT NULL, hypothesis TEXT NOT NULL, motivation TEXT, change TEXT NOT NULL,
  fingerprint TEXT NOT NULL, dataset TEXT NOT NULL, champion_version TEXT, challenger_version TEXT NOT NULL, params TEXT NOT NULL,
  strategy TEXT, dev_results TEXT, validation_results TEXT, holdout_access TEXT NOT NULL DEFAULT 'none', failure_analysis TEXT,
  new_evidence TEXT, decision TEXT NOT NULL, reason TEXT, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS approval (
  experiment_id INTEGER PRIMARY KEY, agent TEXT NOT NULL, challenger_version TEXT NOT NULL, status TEXT NOT NULL, note TEXT,
  decided_by TEXT, decided_at INTEGER);
CREATE TABLE IF NOT EXISTS champion (
  id INTEGER PRIMARY KEY, agent TEXT NOT NULL, version TEXT NOT NULL, strategy TEXT, status TEXT NOT NULL, approved_by TEXT,
  since INTEGER NOT NULL, current INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS review (id INTEGER PRIMARY KEY, direction TEXT NOT NULL, note TEXT, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS holdout_look (id INTEGER PRIMARY KEY, agent TEXT NOT NULL, version TEXT NOT NULL, result TEXT, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lesson (
  id INTEGER PRIMARY KEY, agent TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'hypothesis', cause TEXT, text TEXT NOT NULL, trade_ref TEXT, at INTEGER NOT NULL);
"""


class BudgetExceeded(Exception):
    pass


class ReviewRequired(Exception):
    pass


class DuplicateExperiment(Exception):
    pass


@dataclass(frozen=True)
class Limits:
    max_experiments: int = 20           # per research cycle
    max_configs: int = 5                # parameter configurations per hypothesis
    max_failed_cycles: int = 3          # in a row, on one direction, before the owner must review


def _owner(phrase: str, what: str) -> None:
    if phrase != OWNER_PHRASE:
        raise PermissionError(f"only the owner can {what}, with the exact phrase")


class ResearchDB:
    def __init__(self, path="data/whynot.db"):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT value FROM setting WHERE name='limits'").fetchone()
        self.limits = Limits(**json.loads(row[0])) if row else Limits()

    def _event(self, kind: str, **detail) -> None:
        self.db.execute("INSERT INTO event (kind, detail, at) VALUES (?,?,?)", (kind, json.dumps(detail, default=str), int(time.time())))

    def events(self) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM event ORDER BY id")]

    def _rows(self, sql, args=()):
        out = []
        for r in self.db.execute(sql, args):
            d = dict(r)
            for k in ("params", "strategy", "dev_results", "validation_results", "result"):
                if d.get(k):
                    d[k] = json.loads(d[k])
            out.append(d)
        return out

    # ---- limits

    def set_limits(self, limits: Limits, phrase: str) -> None:
        _owner(phrase, "change the research limits")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO setting VALUES ('limits', ?)", (json.dumps(asdict(limits)),))
            self._event("limits changed", old=asdict(self.limits), new=asdict(limits))
        self.limits = limits

    # ---- cycles

    def open_cycle(self, direction, agent, hypothesis, motivation, datasets, metrics, stopping, max_seconds) -> int:
        plan = {"hypothesis": hypothesis, "datasets": datasets, "metrics": metrics, "stopping": stopping, "max_seconds": max_seconds}
        for name, value in plan.items():
            if not value:
                raise ValueError(f"a research cycle needs its {name} written down before it starts")
        last = self.db.execute("SELECT outcome, closed_at FROM cycle WHERE direction=? AND status='closed' ORDER BY id DESC LIMIT ?",
                               (direction, self.limits.max_failed_cycles)).fetchall()
        if len(last) == self.limits.max_failed_cycles and all(r["outcome"] == FAILED for r in last):
            reviewed = self.db.execute("SELECT MAX(at) FROM review WHERE direction=?", (direction,)).fetchone()[0]
            if reviewed is None or reviewed < last[0]["closed_at"]:
                raise ReviewRequired(f"{len(last)} research cycles in a row on {direction!r} found nothing; the owner must review before another")
        with self.db:
            return self.db.execute(
                "INSERT INTO cycle (direction, agent, hypothesis, motivation, datasets, metrics, stopping, max_seconds, max_experiments, max_configs,"
                " status, opened_at) VALUES (?,?,?,?,?,?,?,?,?,?, 'open', ?)",
                (direction, agent, hypothesis, motivation, datasets, metrics, stopping, int(max_seconds), self.limits.max_experiments,
                 self.limits.max_configs, int(time.time()))).lastrowid

    def cycle(self, cycle: int) -> dict:
        return dict(self.db.execute("SELECT * FROM cycle WHERE id=?", (cycle,)).fetchone())

    def cycles(self) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM cycle ORDER BY id")]

    def close_cycle(self, cycle: int, report: str = "") -> str:
        passed = self.db.execute("SELECT COUNT(*) FROM experiment WHERE cycle_id=? AND decision IN ('candidate', 'promoted')", (cycle,)).fetchone()[0]
        outcome = f"{passed} candidate{'s' if passed != 1 else ''} for approval" if passed else FAILED
        with self.db:
            self.db.execute("UPDATE cycle SET status='closed', outcome=?, report=?, closed_at=? WHERE id=?", (outcome, report, int(time.time()), cycle))
        return outcome

    def owner_review(self, direction: str, phrase: str, note: str) -> None:
        _owner(phrase, "clear a research direction after repeated failures")
        with self.db:
            self.db.execute("INSERT INTO review (direction, note, at) VALUES (?,?,?)", (direction, note, int(time.time()) + 1))

    def budget_left(self, cycle: int) -> dict:
        c = self.cycle(cycle)
        used = self.db.execute("SELECT COUNT(*) FROM experiment WHERE cycle_id=?", (cycle,)).fetchone()[0]
        return {"experiments": c["max_experiments"] - used, "seconds": c["max_seconds"] - (int(time.time()) - c["opened_at"])}

    # ---- experiments

    def check_allowed(self, cycle: int, hypothesis: str, fingerprint: str, new_evidence: str | None = None) -> None:
        """Raise before any compute is spent if this experiment is outside the rules."""
        c = self.cycle(cycle)
        if c["status"] != "open":
            raise BudgetExceeded("this research cycle is closed")
        if int(time.time()) - c["opened_at"] > c["max_seconds"]:
            raise BudgetExceeded(f"the time budget of {c['max_seconds']} seconds for this cycle is spent")
        used = self.db.execute("SELECT COUNT(*) FROM experiment WHERE cycle_id=?", (cycle,)).fetchone()[0]
        if used >= c["max_experiments"]:
            raise BudgetExceeded(f"the budget of {c['max_experiments']} experiments for this cycle is spent")
        same = self.db.execute("SELECT COUNT(*) FROM experiment WHERE cycle_id=? AND hypothesis=?", (cycle, hypothesis)).fetchone()[0]
        if same >= c["max_configs"]:
            raise BudgetExceeded(f"this hypothesis has already used its {c['max_configs']} configurations")
        before = self.db.execute("SELECT id FROM experiment WHERE fingerprint=? AND decision IN ('rejected', 'declined')", (fingerprint,)).fetchone()
        if before and not new_evidence:
            raise DuplicateExperiment(f"this exact strategy already failed as experiment {before[0]}; say what new evidence justifies trying again")

    def record_experiment(self, cycle, agent, hypothesis, change, fingerprint, champion_version, challenger_version, params, dataset,
                          dev_results, decision, reason, motivation=None, validation_results=None, failure_analysis=None,
                          new_evidence=None, strategy=None) -> int:
        self.check_allowed(cycle, hypothesis, fingerprint, new_evidence)
        with self.db:
            exp = self.db.execute(
                "INSERT INTO experiment (cycle_id, agent, hypothesis, motivation, change, fingerprint, dataset, champion_version, challenger_version,"
                " params, strategy, dev_results, validation_results, failure_analysis, new_evidence, decision, reason, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cycle, agent, hypothesis, motivation, change, fingerprint, dataset, champion_version, challenger_version,
                 json.dumps(params, default=str), json.dumps(strategy, default=str) if strategy is not None else None,
                 json.dumps(dev_results, default=str), json.dumps(validation_results, default=str) if validation_results else None,
                 failure_analysis, new_evidence, decision, reason, int(time.time()))).lastrowid
            if decision == "candidate":
                self.db.execute("INSERT INTO approval (experiment_id, agent, challenger_version, status) VALUES (?,?,?, 'pending')",
                                (exp, agent, challenger_version))
        return exp

    def experiment(self, exp: int) -> dict:
        return self._rows("SELECT * FROM experiment WHERE id=?", (exp,))[0]

    def experiments(self, cycle: int | None = None, agent: str | None = None) -> list:
        sql, args = "SELECT * FROM experiment WHERE 1=1", []
        for clause, value in (("cycle_id=?", cycle), ("agent=?", agent)):
            if value is not None:
                sql, args = f"{sql} AND {clause}", args + [value]
        return self._rows(sql + " ORDER BY id", args)

    def count_tested(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM experiment").fetchone()[0]

    def search(self, text: str) -> list:
        """Earlier experiments that mention this, to read before proposing something new."""
        like = f"%{text}%"
        return self._rows("SELECT * FROM experiment WHERE hypothesis LIKE ? OR change LIKE ? OR reason LIKE ? OR motivation LIKE ? ORDER BY id",
                          (like, like, like, like))

    # ---- champions and approval

    def seed_champions(self, agents: dict) -> None:
        """Version one of each agent, as a provisional baseline nobody has approved."""
        from ..sim.agents import fingerprint
        with self.db:
            for a in agents.values():
                if not self.db.execute("SELECT 1 FROM champion WHERE agent=?", (a.id,)).fetchone():
                    self.db.execute("INSERT INTO champion (agent, version, strategy, status, since) VALUES (?,?,?, 'provisional', ?)",
                                    (a.id, a.version, json.dumps({"fingerprint": fingerprint(a)}), int(time.time())))

    def champion(self, agent: str) -> dict | None:
        rows = self._rows("SELECT * FROM champion WHERE agent=? AND current=1", (agent,))
        return rows[0] if rows else None

    def champion_history(self, agent: str) -> list:
        return self._rows("SELECT * FROM champion WHERE agent=? ORDER BY id", (agent,))

    def approval_queue(self) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM approval WHERE status='pending' ORDER BY experiment_id")]

    def _candidate(self, exp: int) -> dict:
        e = self.experiment(exp)
        if e["decision"] != "candidate":
            raise ValueError(f"experiment {exp} is {e['decision']!r}; only a candidate that passed validation can be decided on")
        return e

    def promote(self, exp: int, phrase: str) -> None:
        _owner(phrase, "promote a challenger to champion")
        e = self._candidate(exp)
        now = int(time.time())
        with self.db:
            self.db.execute("UPDATE champion SET current=0 WHERE agent=?", (e["agent"],))
            self.db.execute("INSERT INTO champion (agent, version, strategy, status, approved_by, since) VALUES (?,?,?, 'approved', 'owner', ?)",
                            (e["agent"], e["challenger_version"], json.dumps(e["strategy"]), now))
            self.db.execute("UPDATE experiment SET decision='promoted' WHERE id=?", (exp,))
            self.db.execute("UPDATE approval SET status='approved', decided_by='owner', decided_at=? WHERE experiment_id=?", (now, exp))
            self._event("promoted", agent=e["agent"], version=e["challenger_version"], experiment=exp)

    def decline(self, exp: int, phrase: str, note: str = "") -> None:
        _owner(phrase, "decline a challenger")
        self._candidate(exp)
        with self.db:
            self.db.execute("UPDATE experiment SET decision='declined', reason=COALESCE(reason, '') || ' | owner: ' || ? WHERE id=?", (note, exp))
            self.db.execute("UPDATE approval SET status='declined', note=?, decided_by='owner', decided_at=? WHERE experiment_id=?",
                            (note, int(time.time()), exp))

    # ---- the locked holdout

    def log_holdout(self, agent: str, version: str, phrase: str, result: dict) -> None:
        _owner(phrase, "open the locked holdout")
        with self.db:
            self.db.execute("INSERT INTO holdout_look (agent, version, result, at) VALUES (?,?,?,?)", (agent, version, json.dumps(result, default=str), int(time.time())))
            self._event("holdout opened", agent=agent, version=version)

    def holdout_looks(self, agent: str | None = None) -> list:
        return self._rows("SELECT * FROM holdout_look" + (" WHERE agent=?" if agent else "") + " ORDER BY id", (agent,) if agent else ())

    def holdout_contaminated(self, agent: str) -> bool:
        """Once its results have been seen, the holdout is no longer an independent test for that agent."""
        return bool(self.holdout_looks(agent))

    # ---- lessons

    def add_lesson(self, agent: str, cause: str, text: str, trade_ref: str | None = None) -> None:
        """A lesson is a research hypothesis for later. It never restricts live trading."""
        with self.db:
            self.db.execute("INSERT INTO lesson (agent, cause, text, trade_ref, at) VALUES (?,?,?,?,?)", (agent, cause, text, trade_ref, int(time.time())))

    def lessons(self, agent: str | None = None) -> list:
        return [dict(r) for r in self.db.execute("SELECT * FROM lesson" + (" WHERE agent=?" if agent else "") + " ORDER BY id", (agent,) if agent else ())]
