"""The regime detector walked forward through history, one closed candle at a time.

At each candle the only model in use was fitted on candles strictly before it, the
features use that candle and earlier ones, and the probabilities are forward-filtered.
The model is refitted every 30 days on an expanding window (or a rolling one, for
comparison), with states matched to the old ones so a label keeps its meaning. A refit
that drifts past tolerance, or a drop in live log-likelihood, freezes new entries.

A day's record is usable from that day's close onward, never during the day."""

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from . import regime
from .ingest.timeutil import et_day
from .regime import Candle, Model
from .switching import Switcher

MIN_ROWS = 756                  # three years of daily candles behind the first fit
REFIT_DAYS = 30
ROLLING_WINDOW = 2520           # ten years of candles, for the rolling comparison


@dataclass
class Record:
    day: date
    version: int                # the model that judged this day
    probs: dict                 # filtered probability of each state, from candles up to this day
    next_probs: dict            # probability of each state on the next candle
    top: str
    entropy: float
    loglik: float               # how well the model expected this candle
    active: str | None          # the state after the switching rules
    size: float                 # 0, 0.5 or 1
    reason: str
    switched: bool
    frozen: bool
    expected_days: float | None


@dataclass
class Result:
    models: list
    records: list


def candles_from_catalog(cat, symbol: str, as_of: int | None = None, source: str = "yahoo") -> list:
    """Daily candles of the ETF itself (never a stand-in), as they were known at as_of."""
    ds = cat.find(symbol, "1Day", source)
    if ds is None:
        raise LookupError(f"no daily {source} history for {symbol} in the catalog")
    return [Candle(et_day(b["ts"]), b["open"], b["high"], b["low"], b["close"], b["volume"])
            for b in cat.bars(ds, as_of=as_of) if b["volume"] and b["volume"] > 0]


def walk(candles: list, first_fit_through: date, refit_days: int = REFIT_DAYS, window: int | None = None, log=None,
         known: dict | None = None) -> Result:
    """known: {version: Model} already fitted and stored. A stored model is reused instead of
    being fitted again, so extending the history by a day costs seconds and cannot change the past."""
    known = known or {}
    days, Z = regime.observations(candles)
    start = sum(d <= first_fit_through for d in days)            # rows behind the first fit
    if start < MIN_ROWS:
        raise ValueError(f"the first fit needs 3 years of candles ({MIN_ROWS}); {start} are available by {first_fit_through}")
    if 1 in known:
        model = known[1]
        if model.through != days[start - 1]:
            raise ValueError(f"the stored first model was fitted through {model.through}, not {days[start - 1]}")
    else:
        model = regime.select_and_fit(days[:start], Z[:start])
        model.version, model.report["drift"] = 1, []
    models, records, switcher = [model], [], Switcher()
    alpha, drift, recent = regime.forward(model, Z[:start])[0][-1], [], []
    for t in range(start, len(Z)):
        if days[t - 1] >= model.through + timedelta(days=refit_days):          # refit on what was known before today
            lo = max(0, t - window) if window else 0
            stored = known.get(model.version + 1)
            if stored is not None and stored.through == days[t - 1]:
                fresh, drift = stored, list(stored.report.get("drift", []))
            else:
                fresh, drift = regime.refit(model, days[lo:t], Z[lo:t])
                fresh.version, fresh.report["drift"] = model.version + 1, list(drift)
            model = fresh
            models.append(model)
            alpha, recent = regime.forward(model, Z[:t])[0][-1], []
            if any("re-ranked" in reason for reason in drift):
                switcher = Switcher()                                           # labels moved: a state must earn the lead again
            if log:
                log(f"  refit {model.version} through {model.through}" + (f" DRIFT: {'; '.join(drift)}" if drift else ""))
        try:
            alpha, ll = regime.step(model, alpha, Z[t])
        except FloatingPointError:
            d = switcher.flat("model error: the filter collapsed")              # rule 6
            records.append(Record(days[t], model.version, {}, {}, "", 1.0, float("nan"), d.state, 0.0, d.reason, False, True, None))
            alpha = None
            continue
        nxt = regime.next_state(model, alpha)
        d = switcher.step(model.labels, alpha, nxt)
        recent = (recent + [ll])[-regime.LOOKBACK:]
        reasons = list(drift)
        if len(recent) == regime.LOOKBACK and float(np.mean(recent)) < model.ll_floor:
            reasons.append(f"live log-likelihood {np.mean(recent):.2f} fell below the floor {model.ll_floor:.2f}")
        frozen = bool(reasons)
        records.append(Record(
            day=days[t], version=model.version,
            probs={s: float(p) for s, p in zip(model.labels, alpha)}, next_probs={s: float(p) for s, p in zip(model.labels, nxt)},
            top=model.labels[int(np.argmax(alpha))], entropy=regime.entropy(alpha), loglik=float(ll),
            active=d.state, size=0.0 if frozen else d.size, reason="frozen, no new entries: " + "; ".join(reasons) if frozen else d.reason,
            switched=d.switched, frozen=frozen,
            expected_days=model.durations()[model.labels.index(d.state)] if d.state else None))
    return Result(models, records)


SCHEMA = """
CREATE TABLE IF NOT EXISTS regime_model (
  symbol TEXT NOT NULL, variant TEXT NOT NULL, version INTEGER NOT NULL, fitted_through TEXT NOT NULL,
  labels TEXT NOT NULL, params TEXT NOT NULL, report TEXT NOT NULL, created_at INTEGER NOT NULL,
  PRIMARY KEY (symbol, variant, version));
CREATE TABLE IF NOT EXISTS regime_day (
  symbol TEXT NOT NULL, variant TEXT NOT NULL, day TEXT NOT NULL, version INTEGER NOT NULL, probs TEXT NOT NULL,
  next_probs TEXT NOT NULL, top TEXT, entropy REAL, loglik REAL, active TEXT, size REAL, reason TEXT,
  switched INTEGER, frozen INTEGER, expected_days REAL,
  PRIMARY KEY (symbol, variant, day));
"""


class RegimeStore:
    """Model versions and the day-by-day regime history. What is stored is never rewritten:
    a later run can add days, not change them."""

    def __init__(self, path="data/whynot.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def save(self, symbol: str, variant: str, result: Result) -> None:
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO regime_model VALUES (?,?,?,?,?,?,?,?)", [
                (symbol, variant, m.version, m.through.isoformat(), json.dumps(m.labels),
                 json.dumps({"startprob": m.startprob.tolist(), "transmat": m.transmat.tolist(), "means": m.means.tolist(),
                             "covars": m.covars.tolist(), "ll_floor": m.ll_floor}),
                 json.dumps(m.report), int(time.time())) for m in result.models])
            self.db.executemany("INSERT OR IGNORE INTO regime_day VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                (symbol, variant, r.day.isoformat(), r.version, json.dumps(r.probs), json.dumps(r.next_probs), r.top, r.entropy,
                 r.loglik, r.active, r.size, r.reason, int(r.switched), int(r.frozen), r.expected_days) for r in result.records])

    def days(self, symbol: str, variant: str, start: date | None = None, end: date | None = None) -> list:
        rows = self.db.execute("SELECT * FROM regime_day WHERE symbol=? AND variant=? AND day BETWEEN ? AND ? ORDER BY day",
                               (symbol, variant, (start or date.min).isoformat(), (end or date.max).isoformat()))
        return [{**dict(r), "day": date.fromisoformat(r["day"]), "probs": json.loads(r["probs"]),
                 "next_probs": json.loads(r["next_probs"])} for r in rows]

    def model(self, symbol: str, variant: str, version: int) -> Model:
        r = self.db.execute("SELECT * FROM regime_model WHERE symbol=? AND variant=? AND version=?", (symbol, variant, version)).fetchone()
        p = json.loads(r["params"])
        return Model(np.array(p["startprob"]), np.array(p["transmat"]), np.array(p["means"]), np.array(p["covars"]),
                     json.loads(r["labels"]), through=date.fromisoformat(r["fitted_through"]), ll_floor=p["ll_floor"],
                     report=json.loads(r["report"]), version=version)

    def models(self, symbol: str, variant: str) -> list:
        return [self.model(symbol, variant, r[0]) for r in
                self.db.execute("SELECT version FROM regime_model WHERE symbol=? AND variant=? ORDER BY version", (symbol, variant))]


# ---------------------------------------------------------------- running and reporting

SYMBOLS = ("SPY", "QQQ", "IWM")
FIRST_FIT = date(2005, 12, 31)          # every fund has at least five years of candles by then
VARIANTS = {"expanding": None, "rolling": ROLLING_WINDOW}


def run(cat, store, symbols=SYMBOLS, first_fit=FIRST_FIT, log=print) -> None:
    for symbol in symbols:
        candles = candles_from_catalog(cat, symbol)
        for variant, window in VARIANTS.items():
            log(f"{symbol} {variant}: {len(candles):,} candles, first fit through {first_fit}")
            store.save(symbol, variant, walk(candles, first_fit, window=window, log=log))


def report(cat, store, symbols=SYMBOLS) -> str:
    """What the detector did, on the DEVELOPMENT years only. Later years are stored for
    the engine but are not summarised here, so that nothing learned from them can leak
    into strategy design."""
    from .partitions import DEVELOPMENT_END

    out = ["# Regime detector report", "",
           f"Walk-forward results through {DEVELOPMENT_END} (the development period). Validation and holdout years are "
           "stored but deliberately not shown. Every probability is forward-filtered from past candles only.", ""]
    for symbol in symbols:
        closes = {c.day: c.close for c in candles_from_catalog(cat, symbol)}
        order = sorted(closes)
        nxt = {a: closes[b] / closes[a] - 1 for a, b in zip(order, order[1:])}       # the return of the day after
        out += [f"## {symbol}", ""]
        scores = {}
        for variant in VARIANTS:
            days = store.days(symbol, variant, end=DEVELOPMENT_END)
            models = [m for m in store.models(symbol, variant) if m.through <= DEVELOPMENT_END]
            if not days or not models:
                out += [f"No {variant} run stored.", ""]
                continue
            scores[variant] = float(np.mean([d["loglik"] for d in days if d["loglik"] == d["loglik"]]))
            if variant != "expanding":
                continue
            first, last = models[0], models[-1]
            sel = first.report.get("selection", {})
            reranked = sum(any("re-ranked" in r for r in m.report.get("drift", [])) for m in models)
            out += [f"**Model:** {first.n} states, chosen on candles through {first.through} "
                    f"({', '.join(f'{n} states: BIC {v['bic']:,.0f}, out-of-sample {v['oos_ll_per_candle']:.3f}' for n, v in sel.items())}). "
                    f"Refitted {len(models) - 1} times by {last.through}; {sum(bool(m.report.get('drift')) for m in models)} refits drifted past tolerance, "
                    f"{reranked} of them because a label no longer matched its volatility rank and labels were reassigned.", "",
                    f"**States as of the fit through {last.through}** (z-scores are in units of that feature's own history):", "",
                    "| State | Mean return (z) | Volatility (z) | Expected stay | " + " | ".join(f"to {s}" for s in last.labels) + " |",
                    "|---|---|---|---|" + "---|" * last.n]
            for row in last.describe():
                out.append(f"| {row['state']} | {row['mean_return_z']:+.2f} | {row['volatility_z']:+.2f} | {row['expected_days']:.0f} days | "
                           + " | ".join(f"{p:.2f}" for p in row["transitions"]) + " |")
            out += ["", f"**What happened the day after each state** ({days[0]['day']} to {days[-1]['day']}, {len(days):,} days):", "",
                    "| Active state | Days | Share | Mean next-day return | Annualised volatility | Up days |", "|---|---|---|---|---|---|"]
            groups = {}
            for d in days:
                key = "frozen" if d["frozen"] else (d["active"] or "none yet") + ("" if d["size"] > 0 or d["frozen"] else " (flat: uncertain)" if d["active"] and regime.POLICY.get(d["active"], 0) > 0 else "")
                if d["day"] in nxt:
                    groups.setdefault(key, []).append(nxt[d["day"]])
            for key, r in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                r = np.array(r)
                out.append(f"| {key} | {len(r):,} | {len(r) / len(days):.0%} | {r.mean() * 1e4:+.1f} bp | {r.std() * np.sqrt(252):.0%} | {(r > 0).mean():.0%} |")
            switches = sum(d["switched"] for d in days)
            flat = sum(d["size"] <= 0 for d in days)
            out += ["", f"Switches of active state: {switches} ({switches / (len(days) / 252):.1f} a year). Days at zero size: {flat:,} ({flat / len(days):.0%}), "
                    f"of which frozen by drift or a likelihood drop: {sum(d['frozen'] for d in days):,}.", ""]
            out += ["**Known stress periods inside development** (share of days the top state was STRESS or CRASH):", ""]
            for name, a, b in (("Lehman to the 2009 low", date(2008, 9, 15), date(2009, 3, 9)), ("August 2011 downgrade", date(2011, 8, 1), date(2011, 10, 3)),
                               ("August 2015 devaluation", date(2015, 8, 18), date(2015, 9, 30)), ("Q4 2018 selloff", date(2018, 10, 3), date(2018, 12, 24)),
                               ("Calm 2017 (should be low)", date(2017, 1, 3), date(2017, 12, 29))):
                span = [d for d in days if a <= d["day"] <= b]
                if span:
                    out.append(f"- {name}: {sum(d['top'] in regime.DANGER for d in span) / len(span):.0%} of {len(span)} days")
            out.append("")
        if len(scores) == 2:
            better = max(scores, key=scores.get)
            out += [f"**Expanding or rolling refits:** average log-likelihood per unseen candle was {scores['expanding']:.3f} expanding and "
                    f"{scores['rolling']:.3f} rolling (10 years). Higher is better; {better} fitted unseen candles better over this period.", ""]
    return "\n".join(out) + "\n"
