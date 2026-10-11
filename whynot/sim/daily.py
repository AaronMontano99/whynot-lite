"""The daily engine. One closed candle in, one decision out; the decision is carried
out at the next candle's open.

Order of events on a trading day: yesterday's decision fills at the open; the stop is
checked against the day's range; the account is marked at the close; then the agent
decides, seeing this candle and earlier ones and the regime record for this candle."""

from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

from ..partitions import VALIDATION_END, HOLDOUT_END
from ..regime import POLICY
from .agents import RULES

STOP_SLIP = 2.0                 # a stop pays this many times the normal cost


class HoldoutLocked(Exception):
    """The locked holdout period was asked for. It is opened once, on purpose, and recorded."""


@dataclass
class Decision:
    day: date
    action: str                 # ENTER, EXIT, RESIZE, HOLD, REJECT or NONE
    reason: str
    state: str | None = None
    direction: str | None = None
    fraction: float = 0.0
    time: str | None = None     # for intraday decisions


@dataclass
class Trade:
    direction: str
    rule: str
    state: str | None           # the active state when the trade was decided
    entry_day: date
    entry_price: float
    shares: float
    fraction: float
    stop: float | None
    entry_reason: str
    probs: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    exit_day: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float = 0.0            # dollars, after costs
    ret: float = 0.0            # on the money put into the trade, after costs
    costs: float = 0.0
    mfe: float = 0.0            # best and worst the trade stood, as a return, while it was open
    mae: float = 0.0
    days_held: int = 0
    entry_time: str | None = None
    exit_time: str | None = None
    max_days: int | None = None
    params: dict = field(default_factory=dict)      # the rule settings the trade was opened under


@dataclass
class Result:
    decisions: list
    trades: list
    equity: list                # (day, account value at the close)
    open_position: Trade | None = None


def check_period(start: date, end: date) -> None:
    if end > VALIDATION_END and start <= HOLDOUT_END:
        raise HoldoutLocked(f"{start} to {end} reaches into the locked holdout, which starts after {VALIDATION_END}")


def sign(direction: str) -> int:
    return 1 if direction == "LONG" else -1


def close_trade(pos: Trade, day, price: float, reason: str, rate: float, shares: float | None = None) -> Trade:
    """Book the exit of all (or part) of a position. Entry and exit prices already carry
    their costs, so the profit is simply the difference; `costs` records what was paid."""
    n = pos.shares if shares is None else shares
    done = Trade(**{**vars(pos), "shares": n, "exit_day": day, "exit_price": price, "exit_reason": reason})
    done.pnl = sign(pos.direction) * (price - pos.entry_price) * n
    done.costs = (pos.entry_price + price) * n * rate
    done.ret = done.pnl / (pos.entry_price * n) if n else 0.0
    return done


def run_daily(agent, candles: list, records: dict, start: date, end: date, cost_bps: float, gated: bool = True,
              equity: float = 20000.0) -> Result:
    check_period(start, end)
    rate = cost_bps / 1e4
    cash, pos, pending = equity, None, None
    decisions, trades, curve = [], [], []
    closes, highs, lows = [], [], []
    prev_rec = None
    for c in candles:
        closes.append(c.close); highs.append(c.high); lows.append(c.low)
        if c.day < start or c.day > end:
            prev_rec = records.get(c.day, prev_rec) if c.day < start else prev_rec
            continue

        # 1. yesterday's decision fills at this open
        if pending:
            kind, info = pending
            if kind == "ENTER" and pos is None:
                price = c.open * (1 + sign(info["direction"]) * rate)
                shares = int(cash * info["fraction"] / price)
                if shares > 0:
                    stop = price * (1 - sign(info["direction"]) * info["stop_pct"]) if info["stop_pct"] else None
                    pos = Trade(info["direction"], info["rule"], info["state"], c.day, price, shares, info["fraction"], stop,
                                info["reason"], info["probs"], info["features"])
                    pos.max_days, pos.params = info["max_days"], info["params"]
            elif kind == "EXIT" and pos:
                done = close_trade(pos, c.day, c.open * (1 - sign(pos.direction) * rate), info["reason"], rate)
                cash += done.pnl
                trades.append(done)
                pos = None
            elif kind == "RESIZE" and pos:
                cut = pos.shares - int(pos.shares * info["fraction"] / pos.fraction)
                if cut > 0:
                    done = close_trade(pos, c.day, c.open * (1 - sign(pos.direction) * rate), "size cut: " + info["reason"], rate, shares=cut)
                    cash += done.pnl
                    trades.append(done)
                    pos.shares -= cut
                pos.fraction = info["fraction"]
            pending = None

        # 2. the stop, against this candle's range
        if pos:
            s = sign(pos.direction)
            worst, best = (c.low, c.high) if s > 0 else (c.high, c.low)
            if pos.stop is not None and s * (worst - pos.stop) <= 0:
                gapped = pos.entry_day != c.day and s * (c.open - pos.stop) <= 0
                raw = c.open if gapped else pos.stop
                pos.mae = min(pos.mae, s * (raw / pos.entry_price - 1))
                done = close_trade(pos, c.day, raw * (1 - s * rate * STOP_SLIP), "stop", rate)
                cash += done.pnl
                trades.append(done)
                pos = None
            else:
                pos.mfe = max(pos.mfe, s * (best / pos.entry_price - 1))
                pos.mae = min(pos.mae, s * (worst / pos.entry_price - 1))
                pos.days_held += 1

        # 3. mark the account at the close
        curve.append((c.day, cash + (sign(pos.direction) * (c.close - pos.entry_price) * pos.shares if pos else 0.0)))

        # 4. decide, knowing this candle and nothing later
        rec = records.get(c.day)
        ctx = SimpleNamespace(closes=closes, highs=highs, lows=lows, rec=rec, prev_rec=prev_rec, pos=pos)
        decision, pending = decide(agent, ctx, c.day, gated)
        decisions.append(decision)
        prev_rec = rec if rec is not None else prev_rec
    return Result(decisions, trades, curve, pos)


def decide(agent, ctx, day, gated: bool = True) -> tuple:
    """(Decision, pending order or None). The same function serves backtest, replay and live."""
    rec, pos = ctx.rec, ctx.pos
    if gated:
        state = rec.active if rec else None
        allowed = rec.size if rec else 0.0
        frozen = bool(rec and rec.frozen)
        book = agent.playbooks.get(state) if state else None
    else:
        state, allowed, frozen, book = agent.static_state, 1.0, False, agent.playbooks[agent.static_state]

    def out(action, reason, pend=None, direction=None, fraction=0.0):
        return Decision(day, action, reason, state, direction, fraction), pend

    if pos:
        if gated and rec is None:
            return out("EXIT", "no regime record for this day: flat", ("EXIT", {"reason": "no regime record: flat"}), pos.direction)
        if not frozen:
            if book is None or POLICY.get(state, 0.0) <= 0:
                why = f"state {state}: flat"
                return out("EXIT", why, ("EXIT", {"reason": why}), pos.direction)
            if allowed <= 0:
                why = f"uncertain: flat ({rec.reason})"
                return out("EXIT", why, ("EXIT", {"reason": why}), pos.direction)
            if book.rule != pos.rule:
                why = f"playbook switched: {state} runs {book.rule}"
                return out("EXIT", why, ("EXIT", {"reason": why}), pos.direction)
        ctx.params = pos.params
        why = RULES[pos.rule][1](ctx)
        if why is None and pos.max_days and pos.days_held >= pos.max_days:
            why = f"time stop after {pos.max_days} days"
        if why:
            return out("EXIT", why, ("EXIT", {"reason": why}), pos.direction)
        if not frozen and book and allowed * book.max_size < pos.fraction - 1e-9:
            target = allowed * book.max_size
            return out("RESIZE", f"size cut to {target:.0%}: {rec.reason}", ("RESIZE", {"fraction": target, "reason": rec.reason}), pos.direction, target)
        return out("HOLD", "frozen: holding, no new entries" if frozen else "holding", None, pos.direction, pos.fraction)

    want = None
    seen = set()
    for b in ([book] if book else [x for x in agent.playbooks.values() if x]):
        if b.rule in seen:
            continue
        seen.add(b.rule)
        ctx.params = b.params
        want = RULES[b.rule][0](ctx)
        if want:
            used = b
            break
    if not want:
        return out("NONE", "no signal")
    direction, why = want
    if gated and rec is None:
        return out("REJECT", f"{direction} signal ({why}) refused: no regime record for this day", None, direction)
    if frozen:
        return out("REJECT", f"{direction} signal ({why}) refused: frozen, no new entries ({rec.reason})", None, direction)
    if book is None or allowed <= 0:
        return out("REJECT", f"{direction} signal ({why}) refused: state {state} at zero size ({rec.reason if rec else 'no record'})", None, direction)
    fraction = allowed * book.max_size
    order = {"direction": direction, "fraction": fraction, "rule": book.rule, "state": state, "reason": why, "stop_pct": book.stop_pct,
             "max_days": book.max_days, "params": dict(book.params), "probs": dict(rec.probs) if rec else {},
             "features": {"close": ctx.closes[-1]}}
    return out("ENTER", why, ("ENTER", order), direction, fraction)
