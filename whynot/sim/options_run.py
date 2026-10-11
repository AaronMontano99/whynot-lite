"""The option-level run on the only period, outside the locked holdout, that has real
option prices: 18 January to 31 December 2024. That year is VALIDATION data, so every
run here is a recorded look at validation and nothing in it may be tuned afterwards."""

from dataclasses import replace
from datetime import date

from .. import riskengine
from ..opt.data import CatalogOptionData
from ..opt.engine import LABELS, SelectionRule
from ..partitions import VALIDATION_END
from ..walkforward import candles_from_catalog
from . import stats
from .agents import AGENTS
from .daily import run_daily
from .portfolio import run_options
from .run import COST_BPS, regime_records

START = date(2024, 1, 18)               # the first day with stored option bars
SIGNALS_FROM = date(2024, 1, 2)


def whole_signals(trades: list) -> list:
    """The signal engine books a size cut as a separate partial trade. For options a
    signal is one entry and its final exit."""
    by_entry = {}
    for t in trades:
        cur = by_entry.get(t.entry_day)
        if cur is None or t.exit_day > cur.exit_day:
            by_entry[t.entry_day] = t
    return [by_entry[d] for d in sorted(by_entry)]


def validation_2024(cat, store, ledger, log=print) -> str:
    end = VALIDATION_END
    daily = {a.id: a for a in AGENTS.values() if a.timeframe == "1Day"}
    signals, under = {}, {}
    for a in daily.values():
        candles = [c for c in candles_from_catalog(cat, a.symbol) if c.day <= end]
        under[a.symbol] = {c.day: (c.open, c.close) for c in candles if c.day >= SIGNALS_FROM}
        result = run_daily(a, candles, regime_records(store, a.symbol, end), SIGNALS_FROM, end, COST_BPS[a.symbol])
        signals[a.id] = [t for t in whole_signals(result.trades) if t.entry_day >= START]
        ledger.save(a, result, "VALIDATION", "v1 signals for the 2024 option run (validation look)", {"cost_bps": COST_BPS[a.symbol]},
                    {"curve": stats.curve(result.equity), "trades": stats.trades(result.trades)})
        log(f"{a.id}: {len(signals[a.id])} signals in the option window")
    days = [date.fromisoformat(s["day"]) for s in cat.sessions(START.isoformat(), end.isoformat())]
    symbols = {a.id: a.symbol for a in daily.values()}
    rate = (cat.bars(cat.find("DFF", "1Day", "fred"), end=CatalogOptionData._ts(START)) or [{"close": 0.0}])[-1]["close"] / 100.0
    data, settings, base = CatalogOptionData(cat), riskengine.RiskSettings(), SelectionRule()

    def go(rule):
        return run_options(signals, symbols, data, under, days, rule, settings, rate=rate)

    main = go(base)
    c = stats.curve(main.equity)
    out = ["# Option-level run on real 2024 option prices", "",
           f"Period: {START} to {end}. This is the only stretch outside the locked holdout with real option prices, and it belongs to the "
           "VALIDATION years. It was run once as a demonstration of the options engine and the risk engine; it is recorded as a look at "
           "validation, and nothing was tuned on it. Agent Era is not included: its trades are intraday and only daily option bars are stored.", "",
           "**What is real, derived and assumed**", ""]
    out += [f"- {k}: {v}" for k, v in LABELS.items()]
    out += ["", "## The portfolio", "",
            f"One $100,000 account shared by the four daily agents, every order checked by the central risk engine. Ended at ${main.equity[-1][1]:,.0f} "
            f"({c['total_return']:+.2%}). Worst drawdown {c['max_drawdown']:.2%}. Kill switch fired: {'YES' if any(e['kind'] == 'kill' for e in main.risk_events) else 'no'}. "
            f"Orders refused by the risk engine: {sum(r['stage'] == 'risk' for r in main.rejections)}.", "",
            "| Agent | Signals | Bought | Refused | Won | Average option return | Same signals on the fund | Profit or loss |", "|---|---|---|---|---|---|---|---|"]
    for a in daily.values():
        mine = [t for t in main.trades if t.agent == a.id]
        refused = [r for r in main.rejections if r["agent"] == a.id]
        n = len(mine)
        won = sum(t.pnl > 0 for t in mine) / n if n else 0.0
        opt = sum(t.option_ret for t in mine) / n if n else 0.0
        fund = [t.underlying_ret for t in mine if t.underlying_ret is not None]
        out.append(f"| {a.id} ({a.symbol}) | {len(signals[a.id])} | {n} | {len(refused)} | {won:.0%} | {opt:+.1%} | "
                   f"{(sum(fund) / len(fund) if fund else 0):+.2%} | ${sum(t.pnl for t in mine):+,.0f} |")
    reasons = {}
    for r in main.rejections:
        key = (r["stage"], r["reason"].split(" (")[0].split(":")[0][:70])
        reasons[key] = reasons.get(key, 0) + 1
    out += ["", "**Why signals were refused**", ""] + [f"- {stage}: {why}: {n}" for (stage, why), n in sorted(reasons.items(), key=lambda kv: -kv[1])] or ["- none"]
    flags = {}
    for t in main.trades:
        for f in t.flags:
            flags[f.split(":")[0]] = flags.get(f.split(":")[0], 0) + 1
    exits = {}
    for t in main.trades:
        exits[t.exit_reason.split(":")[0]] = exits.get(t.exit_reason.split(":")[0], 0) + 1
    out += ["", "**How trades ended:** " + ", ".join(f"{n} {k}" for k, n in sorted(exits.items(), key=lambda kv: -kv[1])) + ".",
            "**Data warnings on trades:** " + (", ".join(f"{n} with {k}" for k, n in flags.items()) or "none") + ".",
            f"**Typical trade:** {sum(t.qty for t in main.trades) / max(len(main.trades), 1):.1f} contracts, "
            f"${sum(t.entry_price * 100 * t.qty for t in main.trades) / max(len(main.trades), 1):,.0f} of premium, implied volatility "
            f"{sum(t.iv for t in main.trades) / max(len(main.trades), 1):.0%}, delta {sum(abs(t.delta) for t in main.trades) / max(len(main.trades), 1):.2f}.", "",
            "## Contract choice and cost, with the signals held fixed", "",
            "Same signals, same risk limits; only the contract rule or the assumed cost changes. Each row is another recorded look at validation.", "",
            "| Variant | Trades | Won | Average option return | Profit or loss | Worst drawdown |", "|---|---|---|---|---|---|"]
    variants = [("Delta 0.50 (the default)", main), ("Delta 0.30 (cheaper, further out)", go(replace(base, target_delta=0.30))),
                ("Delta 0.70 (dearer, in the money)", go(replace(base, target_delta=0.70))),
                ("Delta 0.50, assumed cost doubled", go(replace(base, slip_floor=0.04, slip_pct=0.03)))]
    for name, r in variants:
        ledger.save_options(f"2024 option run: {name}", "VALIDATION", r, {"variant": name}, {"curve": stats.curve(r.equity)})
        n = max(len(r.trades), 1)
        out.append(f"| {name} | {len(r.trades)} | {sum(t.pnl > 0 for t in r.trades) / n:.0%} | {sum(t.option_ret for t in r.trades) / n:+.1%} | "
                   f"${sum(t.pnl for t in r.trades):+,.0f} | {stats.curve(r.equity)['max_drawdown']:.2%} |")
    out += ["", "## Risk settings in force", "", "These are the conservative defaults. They need your approval before any paper trading.", ""]
    out += [f"- {k.replace('_', ' ')}: {v:.1%}" if isinstance(v, float) else f"- {k.replace('_', ' ')}: {v}" for k, v in vars(settings).items()]
    return "\n".join(out) + "\n", main
