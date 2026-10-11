"""Research cycle 1: two hypotheses, written here before either was run.

Both come from the development results of version one. Each is one configuration plus
two nearby settings to test fragility: six experiments of the twenty a cycle may use."""

import json
from datetime import date
from types import SimpleNamespace

from ..partitions import DEVELOPMENT_END, VALIDATION_END
from ..sim.agents import AGENTS, variant
from ..sim.run import COST_BPS, DAILY_START, backtest
from . import failure
from .evaluate import evaluate_challenger

PERIODS = {"dev": (DAILY_START, DEVELOPMENT_END), "val": (date(2020, 1, 1), VALIDATION_END)}
PLAN = dict(datasets="DEVELOPMENT (2006 to 2019) for every screen; VALIDATION (2020 to 2024) once, only for a configuration that passes all of them",
            metrics="trades, expectancy, t-statistic against the bar adjusted for every strategy tried, Sharpe and drawdown against the champion, "
                    "results without the best trade, year and state, doubled costs, two nearby settings",
            stopping="one configuration per hypothesis plus two nearby settings; stop there whatever the result", max_seconds=900)

CYCLE_1 = [
    dict(agent="Kip", direction="kip: trend sizing across states",
         hypothesis="Trend-following on QQQ needs no size cut in CHOP and no shorting in STRESS: the long-trend rule in every non-crash state "
                    "gives a better risk-adjusted result than version one.",
         motivation="On development, version one made +46.7% against +138.8% for the same rule with no regime layer; its CHOP trades were its best "
                    "(at half size) and its STRESS shorts were few.",
         change="CHOP at full size instead of half; STRESS runs the long-trend rule instead of the short one. Nothing else.",
         version="kip-c1", changes={"CHOP": {"max_size": 1.0}, "STRESS": {"rule": "trend_long"}},
         near=[("kip-c1-avg40", {"*": {"params": {"avg": 40}}}), ("kip-c1-avg60", {"*": {"params": {"avg": 60}}})]),
    dict(agent="Nemo", direction="nemo: long-only dip buying",
         hypothesis="On SPY, buying sharp dips in an uptrend carries the whole result; shorting overbought days in a downtrend adds nothing.",
         motivation="On development, version one's 299 long trades made about $10,600 and its 82 short trades about $150.",
         change="The mean-reversion rule stops taking short trades. Nothing else.",
         version="nemo-c1", changes={"*": {"params": {"shorts": False}}},
         near=[("nemo-c1-rsi8", {"*": {"params": {"low": 8}}}), ("nemo-c1-rsi12", {"*": {"params": {"low": 12}}})]),
]


def run_cycle_1(cat, store, db, log=print) -> str:
    db.seed_champions(AGENTS)
    runner = lambda agent, start, end, mult: backtest(agent, cat, store, start, end, COST_BPS[agent.symbol] * mult)
    out = ["# Research cycle 1", "",
           "Two hypotheses, each written down before it was run. Limits enforced by the code: 20 experiments a cycle, 5 configurations a hypothesis, "
           "a time budget, and no repeat of a failed experiment without new evidence. The code graded every result.", ""]
    for h in CYCLE_1:
        champion = AGENTS[h["agent"]]
        prior = db.search(h["direction"].split(":")[1].strip())
        cycle = db.open_cycle(direction=h["direction"], agent=h["agent"], hypothesis=h["hypothesis"], motivation=h["motivation"], **PLAN)
        challenger = variant(champion, h["version"], h["changes"])
        near = [variant(challenger, name, changes) for name, changes in h["near"]]
        log(f"{h['agent']}: {h['version']} against {champion.version}")
        r = evaluate_challenger(db, cycle, champion, challenger, near, runner, PERIODS, h["hypothesis"], h["change"], h["motivation"])
        left = db.budget_left(cycle)["experiments"]
        outcome = db.close_cycle(cycle, report=r["reason"])
        d = r["development"]
        out += [f"## Agent {h['agent']}: {h['version']} against {champion.version}", "",
                f"**Hypothesis:** {h['hypothesis']}", f"**Why:** {h['motivation']}", f"**The change:** {h['change']}",
                f"**Earlier experiments on this found in memory:** {len(prior)}.", "",
                "| On development | Trades | Won | Average trade | t | Sharpe | Total return | Worst drawdown |", "|---|---|---|---|---|---|---|---|"]
        for name, s in (("Challenger", d["challenger"]), ("Champion (version one)", d["champion"])):
            out.append(f"| {name} | {s['trades']} | {s['hit_rate']:.0%} | {s['expectancy']:+.2%} | {s['t_stat']:.2f} | {s['sharpe']:.2f} | {s['total_return']:+.1%} | {s['max_drawdown']:.1%} |")
        out += ["", "| Screen | Result | Detail |", "|---|---|---|"] + [f"| {c.name} | {'pass' if c.passed else 'FAIL'} | {c.detail} |" for c in r["checks"]]
        if r["validation"]:
            v = r["validation"]
            out += ["", "| On validation (one look) | Trades | Average trade | Sharpe | Total return | Worst drawdown |", "|---|---|---|---|---|---|"]
            for name, s in (("Challenger", v["challenger"]), ("Champion (version one)", v["champion"])):
                out.append(f"| {name} | {s['trades']} | {s['expectancy']:+.2%} | {s['sharpe']:.2f} | {s['total_return']:+.1%} | {s['max_drawdown']:.1%} |")
        else:
            out += ["", "Validation was not opened, because the challenger did not pass development."]
        out += ["", f"**Decision:** {r['decision'].upper()}. {r['reason']}", f"**Cycle outcome:** {outcome}. Experiments left unused in this cycle: {left - 0}.", ""]
    queue = db.approval_queue()
    out += ["## Where things stand", "",
            f"- Experiments on record: {db.count_tested()}. Waiting for your approval: {len(queue)}"
            + (" (" + ", ".join(q["challenger_version"] for q in queue) + ")" if queue else "") + ".",
            "- Champions: " + ", ".join(f"{a} {db.champion(a)['version']} ({db.champion(a)['status']})" for a in AGENTS) + ".",
            f"- Looks at the locked holdout: {len(db.holdout_looks())}.", ""]
    out += failure_report(store, db)
    return "\n".join(out) + "\n"


def failure_report(store, db) -> list:
    """Every losing trade of version one on development, given one likely cause."""
    out = ["## Why version one's losing trades lost (development)", "",
           "One likely cause per losing trade, by fixed rules of thumb. These are notes for research, not trading rules.", "",
           "| Agent | Losing trades | Most common causes | Common enough to research |", "|---|---|---|---|"]
    for a in AGENTS.values():
        run = db.db.execute("SELECT id FROM bt_run WHERE agent=? AND partition='DEVELOPMENT' AND label='v1 signal test' ORDER BY id LIMIT 1", (a.id,)).fetchone()
        if not run:
            continue
        states = {d["day"].isoformat(): d["active"] for d in store.days(a.symbol, "expanding", end=DEVELOPMENT_END)}
        rows = []
        for t in db.db.execute("SELECT * FROM bt_trade WHERE run_id=? AND pnl < 0", (run[0],)):
            t = dict(t)
            s = 1 if t["direction"] == "LONG" else -1
            gapped = t["exit_reason"] == "stop" and t["stop"] is not None and s * (t["exit_price"] - t["stop"]) < -0.002 * t["stop"]
            rows.append({"cause": failure.classify({**t, "flags": "[]", "account": 20_000.0, "gapped": gapped,
                                                     "state_at_exit": states.get(t["exit_day"], t["state"]) if a.timeframe == "1Day" else t["state"]})})
        counts = {}
        for r in rows:
            counts[r["cause"]] = counts.get(r["cause"], 0) + 1
        worth = failure.enough_evidence(rows)
        for cause in worth:
            if not any(l["cause"] == cause for l in db.lessons(a.id)):
                db.add_lesson(a.id, cause, f"{counts[cause]} of {len(rows)} losing trades of {a.version} on development: {cause.lower()}", f"bt_run {run[0]}")
        top = ", ".join(f"{c} {n}" for c, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3])
        out.append(f"| {a.id} | {len(rows)} | {top} | {', '.join(worth) or 'none'} |")
    return out + [""]
