"""Run the five agents over the DEVELOPMENT years and write the report.

One run per agent with the settings written down in agents.py, plus three comparisons
that change nothing about the strategy: the same rules with the regime layer removed,
and costs doubled and multiplied by five. Nothing here searches for better settings."""

from datetime import date
from types import SimpleNamespace

from ..ingest.timeutil import et_time
from ..partitions import DEVELOPMENT_END
from ..walkforward import candles_from_catalog
from . import stats
from .agents import AGENTS, write_playbooks
from .daily import run_daily
from .intraday import run_intraday

COST_BPS = {"SPY": 1.0, "QQQ": 1.0, "IWM": 2.0}      # per side, of the traded value: half a spread plus slippage
DAILY_START = date(2006, 1, 3)                        # the first day the regime detector has a record
INTRADAY_START = date(2016, 1, 4)                     # where the 1-minute history begins


class CatalogSessions:
    """day -> that session's regular-hours 1-minute bars, read from the catalog on demand."""

    def __init__(self, cat, symbol, start: date, end: date):
        self.cat, self.ds = cat, cat.find(symbol, "1Min", "alpaca")
        self.hours = {date.fromisoformat(s["day"]): (s["open"], s["close"]) for s in cat.sessions(start.isoformat(), end.isoformat())}

    def keys(self):
        return self.hours.keys()

    def __getitem__(self, day):
        open_, close = self.hours[day]
        return self.cat.db.execute("SELECT ts, open, high, low, close, volume FROM bar WHERE dataset_id=? AND ts>=? AND ts<? ORDER BY ts",
                                   (self.ds, et_time(day, open_), et_time(day, close))).fetchall()


def regime_records(store, symbol, end: date) -> dict:
    return {d["day"]: SimpleNamespace(**d) for d in store.days(symbol, "expanding", end=end)}


def backtest(agent, cat, store, start, end, cost, gated=True):
    """One agent over one period. Nothing after `end` is loaded."""
    records = regime_records(store, agent.symbol, end)
    if agent.timeframe == "5Min":
        start = max(start, INTRADAY_START)
        return run_intraday(agent, CatalogSessions(cat, agent.symbol, start, end), records, start, end, cost, gated=gated)
    candles = [c for c in candles_from_catalog(cat, agent.symbol) if c.day <= end]
    return run_daily(agent, candles, records, max(start, DAILY_START), end, cost, gated=gated)


def _one(agent, cat, store, end, cost, gated=True):
    return backtest(agent, cat, store, DAILY_START, end, cost, gated)


def development(cat, store, ledger, log=print) -> str:
    end = DEVELOPMENT_END
    write_playbooks()
    out = ["# Development backtest: the five agents' signals on the underlying funds", "",
           f"Period: {DAILY_START} to {end} for the daily agents, {INTRADAY_START} to {end} for Agent Era (1-minute data starts in 2016). "
           "These are the DEVELOPMENT years only; validation and the locked holdout have not been touched.", "",
           "What this measures: each agent trading the fund itself (shares, long or short) with $20,000, whole shares, entries and exits at the "
           "next open, costs on every fill. It is the signal test. Options, portfolio risk limits and position sizing by confidence come in Milestone 4.", "",
           "Each strategy was written down before it was run and was run once. Nothing was tuned.", ""]
    summary = []
    for agent in AGENTS.values():
        log(f"{agent.id} ({agent.symbol})")
        cost = COST_BPS[agent.symbol]
        main = _one(agent, cat, store, end, cost)
        c, t = stats.curve(main.equity), stats.trades(main.trades)
        start = main.equity[0][0]
        candles = candles_from_catalog(cat, agent.symbol)
        hold = stats.curve(stats.buy_and_hold(candles, start, end))
        static = _one(agent, cat, store, end, cost, gated=False) if agent.static_state else None
        sc = stats.curve(static.equity) if static else None
        g = stats.gates(c, t, c["total_return"] > hold["total_return"], sc is None or c["total_return"] > sc["total_return"])
        ledger.save(agent, main, "DEVELOPMENT", "v1 signal test", {"cost_bps": cost, "gated": True}, {"curve": c, "trades": t, "gates": g})
        if static:
            ledger.save(agent, static, "DEVELOPMENT", "v1 static baseline (no regime)", {"cost_bps": cost, "gated": False}, {"curve": sc})
        stress = {m: stats.curve(_one(agent, cat, store, end, cost * m).equity) for m in (2, 5)}
        passed = sum(v for k, v in g.items() if k != "all")
        summary.append((agent, c, t, hold, g, passed))

        out += [f"## Agent {agent.id} ({agent.bot}, {agent.symbol}): {agent.focus}", "",
                "| | Total return | A year | Sharpe | Sortino | Worst drawdown | Longest drawdown |", "|---|---|---|---|---|---|---|"]
        for name, s in (("Agent", c), ("Same rules, no regime layer", sc), (f"Buy and hold {agent.symbol}", hold)):
            if s:
                out.append(f"| {name} | {s['total_return']:+.1%} | {s['cagr']:+.1%} | {s['sharpe']:.2f} | {s['sortino']:.2f} | {s['max_drawdown']:.1%} | {s['drawdown_days']:,} days |")
        acts = {}
        for d in main.decisions:
            acts[d.action] = acts.get(d.action, 0) + 1
        out += ["", f"**Trades:** {t['count']:,}. Won {t['hit_rate']:.1%}. Average trade {t['expectancy']:+.2%} (winners {t['avg_win']:+.2%}, losers {t['avg_loss']:+.2%}, "
                f"payoff {t['payoff']:.2f}). Profit factor {t['profit_factor']:.2f}. Worst trade {t['worst']:+.1%}. t-statistic {t['t_stat']:.2f}.",
                f"**Decisions:** " + ", ".join(f"{n:,} {a}" for a, n in sorted(acts.items(), key=lambda kv: -kv[1])) + ".", "",
                "| State at entry | Trades | Won | Average trade |", "|---|---|---|---|"]
        out += [f"| {k} | {v['count']} | {v['hit_rate']:.0%} | {v['expectancy']:+.2%} |" for k, v in t["by_state"].items()]
        out += ["", "| Year | Trades | Won | Average trade |", "|---|---|---|---|"]
        out += [f"| {k} | {v['count']} | {v['hit_rate']:.0%} | {v['expectancy']:+.2%} |" for k, v in t["by_year"].items()]
        out += ["", f"**If costs were higher:** Sharpe {c['sharpe']:.2f} at {cost:g} bp a side, {stress[2]['sharpe']:.2f} at double, {stress[5]['sharpe']:.2f} at five times.",
                "", f"**Your gates ({passed} of 6 passed):** " + ", ".join(
                    f"{label} {'PASS' if g[key] else 'fail'}" for key, label in (("sharpe", "Sharpe above 1.5"), ("max_drawdown", "drawdown under 15%"),
                    ("hit_rate", "hit rate above 55%"), ("t_stat", "t above 2.0"), ("beats_buy_and_hold", "beats buy and hold"), ("beats_static", "beats its no-regime version"))) + ".", ""]
    out[8:8] = ["## Summary", "", "| Agent | Fund | Trades | Total return | Buy and hold | Sharpe | Worst drawdown | Won | Average trade | t | Gates passed |", "|---|---|---|---|---|---|---|---|---|---|---|"] + [
        f"| {a.id} | {a.symbol} | {t['count']:,} | {c['total_return']:+.1%} | {h['total_return']:+.1%} | {c['sharpe']:.2f} | {c['max_drawdown']:.1%} | {t['hit_rate']:.0%} | {t['expectancy']:+.2%} | {t['t_stat']:.2f} | {p} of 6 |"
        for a, c, t, h, g, p in summary] + ["",
        "Five strategies were tested, so a t-statistic of 2.0 is a weaker bar than it looks: allowing for five tries, about 2.6 is needed for the same confidence.",
        f"Strategies clearing every gate: {sum(g['all'] for *_, g, _ in summary)} of 5.", ""]
    return "\n".join(out) + "\n"
