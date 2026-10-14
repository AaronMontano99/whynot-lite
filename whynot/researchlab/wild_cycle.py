"""Research cycle 3: Era as a wild card on single stocks in the news. Written before it was run.

The owner asked for this on 2026-10-12: Era as a wild card trading whatever is trending in the news,
which authorized single stocks for Era only.
One configuration, two nearby settings, and one control without the news. Development years only."""

from dataclasses import replace

from ..partitions import DEVELOPMENT_END
from ..sim import stats
from ..sim.wild_run import EQUITY, WILD_START
from ..sim.wildcard import WildRule, run_wild
from .screens import Evidence, screen

RULE = WildRule()
HYPOTHESIS = ("A stock that is suddenly in the news (at least 5 headlines since the last close and at least 3 times its own usual number) and that "
              "moved at least 2% that day keeps moving the same way over the next 3 sessions, by more than trading costs.")
MOTIVATION = "The owner's direction for Era: trade whatever is trending in the news. Era version one lost 18.7% on development and does not trade."
CHANGE = ("Era leaves QQQ's opening range for single stocks picked by news heat: enter at the next open in the direction of the day's move, hold 3 "
          "sessions, stop 6%, at most 2 positions. Tested on shares; options on these stocks have prices only from 2024.")
PLAN = dict(datasets="DEVELOPMENT only: headlines from October 2015, split-adjusted daily prices from 2016, trades March 2016 to December 2019",
            metrics="the eleven screens, measured against the same rule choosing the day's biggest mover with no news requirement; "
                    "doubled costs; holding 2 and 5 sessions as nearby settings; long and short separately; each year",
            stopping="one configuration, two nearby settings and the control; stop there whatever the result", max_seconds=3600)
NEAR = [("era-wild-1-hold2", replace(RULE, hold_days=2)), ("era-wild-1-hold5", replace(RULE, hold_days=5))]
CONTROL = replace(RULE, min_headlines=0, min_heat=0.0, rank_by="move")


def _evidence(rule, run, neighbours=()) -> Evidence:
    main = run(rule)
    return Evidence(curve=stats.curve(main.equity), trades=main.trades, stats=stats.trades(main.trades),
                    cost2_sharpe=stats.curve(run(replace(rule, cost_bps=rule.cost_bps * 2)).equity)["sharpe"],
                    neighbour_sharpes=[stats.curve(run(n).equity)["sharpe"] for _, n in neighbours], complexity=1)


def _row(name, e: Evidence) -> str:
    t, c = e.stats, e.curve
    return (f"| {name} | {t['count']} | {t['hit_rate']:.0%} | {t['expectancy']:+.2%} | {t['t_stat']:.2f} | {c['sharpe']:.2f} | "
            f"{c['total_return']:+.1%} | {c['max_drawdown']:.1%} |")


def run_wild_cycle(days, counts, bars, states, db, spy_hold: list, log=print) -> str:
    """days, counts, bars: see run_wild. spy_hold: the account curve of holding SPY over the same dates."""
    run = lambda rule: run_wild(days, counts, bars, WILD_START, DEVELOPMENT_END, rule, EQUITY, states)
    everything = {d: {s: 0 for s, series in bars.items() if d in series} for d in days}        # the control looks at every stock with a price
    control_run = lambda rule: run_wild(days, everything, bars, WILD_START, DEVELOPMENT_END, rule, EQUITY, states)
    cycle = db.open_cycle(direction="era: news heat on single stocks", agent="Era", hypothesis=HYPOTHESIS, motivation=MOTIVATION, **PLAN)
    log("era-wild-1 and its nearby settings")
    new = _evidence(RULE, run, NEAR)
    log("the control without news")
    control = _evidence(CONTROL, control_run)
    checks = screen(new, control, db.count_tested() + 1 + len(NEAR))
    passed = all(c.passed for c in checks)
    failed = [c.name for c in checks if not c.passed]
    reason = ("Passed every screen on development. Nothing further happens without the owner." if passed
              else "Failed on development: " + "; ".join(failed) + ".")
    brief = lambda e: {"sharpe": round(e.curve["sharpe"], 3), "total_return": round(e.curve["total_return"], 4),
                       "max_drawdown": round(e.curve["max_drawdown"], 4), "trades": e.stats["count"], "hit_rate": round(e.stats["hit_rate"], 3),
                       "expectancy": round(e.stats["expectancy"], 5), "t_stat": round(e.stats["t_stat"], 2)}
    main = run(RULE)
    for (name, rule), sharpe in zip(NEAR, new.neighbour_sharpes):
        db.record_experiment(cycle, "Era", HYPOTHESIS, f"nearby setting of era-wild-1: hold {rule.hold_days} sessions", f"wild:{rule}", "era-v1", name,
                             vars(rule), "DEVELOPMENT", {"sharpe": round(sharpe, 3)}, "sensitivity", "A nearby setting, run only to test fragility.")
    db.record_experiment(cycle, "Era", HYPOTHESIS, CHANGE, f"wild:{RULE}", "era-v1", "era-wild-1", vars(RULE), "DEVELOPMENT",
                         {"challenger": brief(new), "control": brief(control), "checks": [vars(c) for c in checks]},
                         "passed_development" if passed else "rejected", reason, motivation=MOTIVATION)
    outcome = db.close_cycle(cycle, report=reason)

    hold = stats.curve(spy_hold)
    longs, shorts = [t for t in main.trades if t.direction == "LONG"], [t for t in main.trades if t.direction == "SHORT"]
    side = lambda items: (f"{len(items)} trades, {stats.trades(items)['hit_rate']:.0%} won, average {stats.trades(items)['expectancy']:+.2%}"
                          if items else "none")
    out = ["# Era as a wild card: news heat on single stocks (development)", "",
           f"**Hypothesis:** {HYPOTHESIS}", f"**Why:** {MOTIVATION}", f"**The change:** {CHANGE}", "",
           f"Period: {WILD_START} to {DEVELOPMENT_END}. ${EQUITY:,.0f}, shares, {RULE.cost_bps:.0f} basis points a side. Validation and the holdout were not touched.", "",
           "| On development | Trades | Won | Average trade | t | Sharpe | Total return | Worst drawdown |", "|---|---|---|---|---|---|---|---|",
           _row("Wild card (news heat)", new), _row("Control (biggest mover, no news)", control),
           f"| Holding SPY | | | | | {hold['sharpe']:.2f} | {hold['total_return']:+.1%} | {hold['max_drawdown']:.1%} |", "",
           f"- Bought after up days: {side(longs)}.", f"- Sold short after down days: {side(shorts)}.",
           "- By year: " + "; ".join(f"{y} {v['count']} trades, average {v['expectancy']:+.2%}" for y, v in new.stats["by_year"].items()) + ".",
           f"- With costs doubled: Sharpe {new.cost2_sharpe:.2f}. Holding 2 sessions: {new.neighbour_sharpes[0]:.2f}. Holding 5: {new.neighbour_sharpes[1]:.2f}.",
           "", "| Screen | Result | Detail |", "|---|---|---|"]
    out += [f"| {c.name} | {'pass' if c.passed else 'FAIL'} | {c.detail} |" for c in checks]
    g = stats.gates(new.curve, new.stats, new.curve["total_return"] > hold["total_return"], new.curve["total_return"] > control.curve["total_return"])
    out += ["", "Against the owner's targets: " + ", ".join(f"{k.replace('_', ' ')} {'yes' if v else 'no'}" for k, v in g.items() if k != "all") + ".",
            "", f"**Decision:** {'PASSED DEVELOPMENT' if passed else 'REJECTED'}. {reason}", f"**Cycle outcome:** {outcome}.", "",
            "## What this does and does not show", "",
            "- Shares, not options. Options on these stocks would add time decay and wider spreads; their prices exist only from 2024.",
            "- Prices are adjusted for splits, which is knowledge from later; it changes price levels, not daily moves.",
            "- The stocks are the ones the news named at the time, including ones since delisted, so today's survivors are not favoured.",
            "- Daily closes come from one provider, whose closes were found wrong on some stress days for the three funds.",
            "- A headline is counted, never read. Its time is the provider's publication time.",
            "- Short trades assume shares could be borrowed at no cost.", ""]
    return "\n".join(out)
