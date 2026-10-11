"""The options portfolio simulator: all agents, one account, one risk engine.

It takes the agents' signals (already decided, day by day, by the signal engine) and
walks the calendar. At each close it picks a contract for tomorrow's entries from what
traded today, asks the risk engine, and fills at tomorrow's first real trade. Exits fill
the same way. The signal's own result on the fund is kept beside the option's result,
so contract selection can be judged separately from the signal."""

from dataclasses import dataclass, field
from datetime import date

from .. import regime, sizing
from ..opt.engine import entry_fill, exit_fill, pick, settle
from ..riskengine import Order, RiskEngine

PROBE = 0.01                    # of the account, while an agent's probabilities are unproven on its own fills


@dataclass
class OptionTrade:
    agent: str
    underlying: str
    contract: str
    kind: str
    strike: float
    expiration: date
    qty: int
    direction: str
    state: str | None
    entry_day: date
    entry_price: float              # real opening trade plus assumed cost
    iv: float                       # derived
    delta: float                    # derived
    selection_reason: str
    size_reason: str
    underlying_ret: float | None    # what the signal made on the fund itself
    exit_day: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float = 0.0
    option_ret: float = 0.0         # on the premium paid
    flags: list = field(default_factory=list)
    simulated: bool = True          # never a broker execution


@dataclass
class OptionsResult:
    trades: list
    rejections: list
    equity: list
    risk_events: list
    open_positions: list = field(default_factory=list)


def budget_for(agent: str, sig, equity: float, fills: dict, cap: float) -> tuple:
    """(dollars of premium, why). Probe size until this agent's fills in this state show
    its probabilities are calibrated; then quarter Kelly x probability x certainty."""
    mine = fills.get((agent, sig.state), [])
    pairs = [(f["p"], f["pnl"] > 0) for f in mine]
    if not sizing.calibrated(pairs):
        return PROBE * sig.fraction * equity, "probe size: fewer than 30 calibrated fills for this agent in this state"
    p = sig.probs.get(sig.state, 0.0)
    f, why = sizing.fraction(sizing.edge(mine), p, regime.entropy(list(sig.probs.values())), sig.fraction, cap, True)
    return f * equity, why


def run_options(signals: dict, symbols: dict, data, under: dict, days: list, rule, settings, equity: float = 100_000.0,
                rate: float = 0.0) -> OptionsResult:
    risk, cash = RiskEngine(settings, equity), equity
    entries, rejections, trades, curve, fills = {}, [], [], [], {}
    first = days[0] if days else None

    def refuse(agent, day, stage, reason, sig):
        rejections.append({"agent": agent, "day": day, "stage": stage, "reason": reason, "direction": sig.direction, "state": sig.state})

    for agent, sigs in signals.items():
        for sig in sigs:
            if sig.entry_day in days and sig.entry_day != first:
                entries.setdefault(sig.entry_day, []).append((agent, sig))
            elif first and days[0] <= sig.entry_day <= days[-1]:
                refuse(agent, sig.entry_day, "selection", "no earlier day of option data to choose a contract from", sig)

    book, pending, prev_equity = [], [], equity     # book: open positions as dicts
    for i, day in enumerate(days):
        risk.start_day(day, prev_equity)
        for p in book:
            if p["sig"].exit_day == day and not p["why"]:
                stopped = p["sig"].exit_reason == "stop"                # a stop happens during the day: sell at the close, not the open
                p["why"], p["at"] = ("signal stop" if stopped else f"signal exit: {p['sig'].exit_reason}"), ("close" if stopped else "open")

        # 1. yesterday's decisions fill at today's first real trade
        for agent, sig, choice, order, why_size, why_pick in pending:
            price, note = entry_fill(data, choice.contract, day, rule)
            if price is None:
                refuse(agent, day, "fill", note, sig)
                continue
            cash -= price * 100 * choice.qty
            risk.record_fill(order, price)
            t = OptionTrade(agent, symbols[agent], choice.contract, choice.kind, choice.strike, choice.expiration, choice.qty, sig.direction,
                            sig.state, day, price, choice.iv, choice.delta, why_pick, why_size, None)
            book.append({"trade": t, "sig": sig, "why": None, "at": "open", "mark": price, "stale": 0})
        pending, keep = [], []
        for p in book:
            t = p["trade"]
            if day >= t.expiration and not p["why"]:
                p["why"], p["at"] = "expiry", "open"
            if not p["why"]:
                keep.append(p)
                continue
            price, _ = exit_fill(data, t.contract, day, rule, at=p["at"])
            reason = p["why"]
            if price is None and day >= t.expiration:
                spot = under[t.underlying].get(t.expiration, under[t.underlying].get(day))[1]
                price, reason, day_out = settle(t.kind, t.strike, spot), "settled at expiry", t.expiration
            elif price is None:
                if "exit delayed: no trade printed" not in t.flags:
                    t.flags.append("exit delayed: no trade printed")
                keep.append(p)
                continue
            else:
                day_out = day
            cash += price * 100 * t.qty
            t.exit_day, t.exit_price, t.exit_reason = day_out, price, reason
            t.pnl = (price - t.entry_price) * 100 * t.qty
            t.option_ret = t.pnl / (t.entry_price * 100 * t.qty)
            t.underlying_ret = getattr(p["sig"], "ret", None)
            if p["stale"]:
                t.flags.append(f"stale marks: {p['stale']} days without a trade")
            risk.record_fill(Order(t.agent, t.underlying, t.contract, t.direction, t.qty, price, False, day, day), price)
            fills.setdefault((t.agent, t.state), []).append({"p": p["sig"].probs.get(t.state, 0.0), "pnl": t.pnl, "entry_price": t.entry_price, "contracts": t.qty})
            trades.append(t)
        book = keep

        # 2. mark the book at today's last real trades
        for p in book:
            bar = data.bar(p["trade"].contract, day)
            if bar is None:
                p["stale"] += 1
            else:
                p["mark"] = bar.close
        value = cash + sum(p["mark"] * 100 * p["trade"].qty for p in book)
        curve.append((day, value))
        for bot, contract in risk.mark(day, value):
            for p in book:
                if (p["trade"].agent, p["trade"].contract) == (bot, contract):
                    p["why"], p["at"] = "kill switch", "open"

        # 3. decide at the close for tomorrow
        if i + 1 < len(days):
            nxt = days[i + 1]
            for p in book:
                if not p["why"] and (p["trade"].expiration - nxt).days <= rule.exit_dte:
                    p["why"], p["at"] = "expiry near", "open"
            cap = settings.max_position_risk * value
            for agent, sig in entries.get(nxt, []):
                symbol = symbols[agent]
                bar = under[symbol].get(day)
                if bar is None:
                    refuse(agent, day, "selection", "no price for the fund that day", sig)
                    continue
                want, why_size = budget_for(agent, sig, value, fills, settings.max_position_risk)
                choice, why, note = pick(data, symbol, sig.direction, day, bar[1], want, cap, rule, rate)
                why_size += note
                if choice is None:
                    refuse(agent, day, "selection", why, sig)
                    continue
                order = Order(agent, symbol, choice.contract, sig.direction, choice.qty, choice.ref_price, True, day, day, choice.volume, choice.trades)
                verdict = risk.check(order)
                if not verdict.approved:
                    refuse(agent, day, "risk", verdict.reasons[0], sig)
                    continue
                pending.append((agent, sig, choice, order, why_size, why))
        prev_equity = value
    return OptionsResult(trades, rejections, curve, risk.events, [p["trade"] for p in book])
