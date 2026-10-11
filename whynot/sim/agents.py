"""The five agents, their rules, and one playbook per market state.

These are starting hypotheses written down before any of them was tested. Every number
here was chosen in advance from common practice, not fitted to results. A change to any
of it is a new strategy version and has to go through the challenger process."""

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..regime import DANGER
from . import indicators as ind

STATES = ("CALM_UP", "CALM", "CHOP", "STRESS", "CRASH")


@dataclass(frozen=True)
class Playbook:
    rule: str
    stop_pct: float | None          # distance from entry at which the trade is abandoned
    max_days: int | None            # time stop, in candles
    max_size: float = 1.0           # share of the agent's allocation, before the state's own cut
    take_profit: str = "None. The exit rule takes the profit."
    params: dict = field(default_factory=dict)      # settings for the rule; empty means its defaults


@dataclass(frozen=True)
class Agent:
    id: str
    bot: str
    symbol: str
    timeframe: str
    version: str
    focus: str
    playbooks: dict                 # state -> Playbook, or None for "flat"
    static_state: str | None        # the playbook the no-regime baseline uses all the time


# ---- rules: entry(ctx) -> (direction, why) or None; exit(ctx) -> why or None ----
# ctx has closes, highs, lows (through the candle just closed), rec and prev_rec (the
# regime records for this candle and the one before) and pos (the open position or None).

# The numbers each rule uses unless a playbook's params say otherwise. These are version one.
DEFAULTS = {"trend_long": {"avg": 50, "lookback": 20}, "trend_short": {"avg": 50, "lookback": 20},
            "revert": {"rsi_n": 2, "low": 10, "high": 90, "trend_avg": 200, "exit_avg": 5, "shorts": True},
            "breakout": {"entry_n": 20, "exit_n": 10}, "transition": {"danger_in": 0.25, "danger_out": 0.15},
            "opening_range": {"range_bars": 6, "min_width": 0.001, "max_width": 0.015, "last_entry_minute": 330}}


def setting(ctx, rule: str, name: str):
    return (getattr(ctx, "params", None) or {}).get(name, DEFAULTS[rule][name])


def _trend_long(ctx):
    n, back = setting(ctx, "trend_long", "avg"), setting(ctx, "trend_long", "lookback")
    avg = ind.sma(ctx.closes, n)
    if avg and len(ctx.closes) > back and ctx.closes[-1] > avg and ctx.closes[-1] > ctx.closes[-back - 1]:
        return "LONG", f"close above its {n}-day average and above its level {back} days ago"


def _trend_long_exit(ctx):
    n = setting(ctx, "trend_long", "avg")
    avg = ind.sma(ctx.closes, n)
    if avg and ctx.closes[-1] < avg:
        return f"close fell below its {n}-day average"


def _trend_short(ctx):
    n, back = setting(ctx, "trend_short", "avg"), setting(ctx, "trend_short", "lookback")
    avg = ind.sma(ctx.closes, n)
    if avg and len(ctx.closes) > back and ctx.closes[-1] < avg and ctx.closes[-1] < ctx.closes[-back - 1]:
        return "SHORT", f"close below its {n}-day average and below its level {back} days ago"


def _trend_short_exit(ctx):
    n = setting(ctx, "trend_short", "avg")
    avg = ind.sma(ctx.closes, n)
    if avg and ctx.closes[-1] > avg:
        return f"close rose above its {n}-day average"


def _revert(ctx):
    get = lambda name: setting(ctx, "revert", name)
    r, long_avg = ind.rsi(ctx.closes, get("rsi_n")), ind.sma(ctx.closes, get("trend_avg"))
    if r is None or long_avg is None:
        return None
    if r < get("low") and ctx.closes[-1] > long_avg:
        return "LONG", f"{get('rsi_n')}-day RSI {r:.0f} (oversold) while above the {get('trend_avg')}-day average"
    if get("shorts") and r > get("high") and ctx.closes[-1] < long_avg:
        return "SHORT", f"{get('rsi_n')}-day RSI {r:.0f} (overbought) while below the {get('trend_avg')}-day average"


def _revert_exit(ctx):
    avg = ind.sma(ctx.closes, setting(ctx, "revert", "exit_avg"))
    if avg and ((ctx.pos.direction == "LONG" and ctx.closes[-1] > avg) or (ctx.pos.direction == "SHORT" and ctx.closes[-1] < avg)):
        return "price came back through its 5-day average"


def _breakout(ctx):
    top = ind.highest(ctx.highs, setting(ctx, "breakout", "entry_n"))
    if top and ctx.closes[-1] > top:
        return "LONG", "close above the highest high of the previous 20 days"


def _breakout_exit(ctx):
    floor = ind.lowest(ctx.lows, setting(ctx, "breakout", "exit_n"))
    if floor and ctx.closes[-1] < floor:
        return "close below the lowest low of the previous 10 days"


def _danger(rec):
    return sum(p for s, p in (rec.next_probs or {}).items() if s in DANGER) if rec else 0.0


def _transition(ctx):
    rec, prev = ctx.rec, ctx.prev_rec
    if rec is None or prev is None:
        return None
    if rec.switched and prev.active in DANGER and rec.active not in DANGER:
        return "LONG", f"the regime just left {prev.active} for {rec.active}"
    if rec.switched and rec.active == "STRESS" and prev.active not in DANGER:
        return "SHORT", f"the regime just moved from {prev.active} into STRESS"
    line = setting(ctx, "transition", "danger_in")
    if rec.active not in DANGER and _danger(rec) > line >= _danger(prev):
        return "SHORT", f"chance of stress or crash tomorrow rose to {_danger(rec):.2f}"


def _transition_exit(ctx):
    rec = ctx.rec
    if rec is None:
        return None
    if ctx.pos.direction == "LONG" and rec.active in DANGER:
        return f"the regime went back to {rec.active}"
    if ctx.pos.direction == "SHORT" and rec.active not in DANGER and _danger(rec) < setting(ctx, "transition", "danger_out"):
        return "the chance of stress faded below 0.15"


def _never(ctx):
    return None


RULES = {
    "trend_long": (_trend_long, _trend_long_exit),
    "trend_short": (_trend_short, _trend_short_exit),
    "revert": (_revert, _revert_exit),
    "breakout": (_breakout, _breakout_exit),
    "transition": (_transition, _transition_exit),
    "opening_range": (_never, _never),          # intraday; its logic lives in intraday.py
}

# What each rule says in a playbook: entry, exit, invalidation.
WORDS = {
    "trend_long": ("Buy (calls) when the close is above its 50-day average and above where it was 20 days ago.",
                   "Sell when the close falls below the 50-day average.",
                   "A close below the 50-day average: the trend this trade relied on is gone."),
    "trend_short": ("Sell short (puts) when the close is below its 50-day average and below where it was 20 days ago.",
                    "Cover when the close rises above the 50-day average.",
                    "A close above the 50-day average."),
    "revert": ("Buy (calls) when the 2-day RSI is under 10 and price is above its 200-day average. "
               "Sell short (puts) when the 2-day RSI is over 90 and price is below its 200-day average.",
               "Exit when price closes back through its 5-day average.",
               "No snap-back within the time stop, or the stop is hit: the dip was not a dip."),
    "breakout": ("Buy (calls) when the close is above the highest high of the previous 20 days.",
                 "Sell when the close is below the lowest low of the previous 10 days.",
                 "A close below the 10-day low: the breakout failed."),
    "transition": ("Buy (calls) on the day the regime leaves STRESS or CRASH for a calmer state. Sell short (puts) on the day the "
                   "regime moves from a calm state into STRESS, or when the chance of stress or crash tomorrow first rises above 0.25.",
                   "Longs exit if the regime returns to STRESS or CRASH. Shorts exit when the regime is calm and that chance is under 0.15.",
                   "The regime reverses the move that opened the trade."),
    "opening_range": ("After the first 30 minutes, buy (calls) on a 5-minute close above the opening range's high, or sell short (puts) on a "
                      "5-minute close below its low. One trade a day, none after 15:00, none if the range is under 0.1% or over 1.5% of price.",
                      "Flat by the end of the session, always.",
                      "Price crosses back through the other side of the opening range."),
}


def _books(**by_state):
    return {s: by_state.get(s) for s in STATES}


TREND = Playbook("trend_long", stop_pct=0.05, max_days=None)
REVERT = Playbook("revert", stop_pct=0.04, max_days=5)
BREAKOUT = Playbook("breakout", stop_pct=0.05, max_days=None)
TRANSITION = Playbook("transition", stop_pct=0.04, max_days=10)
RANGE = Playbook("opening_range", stop_pct=None, max_days=0)

AGENTS = {a.id: a for a in [
    Agent("Kip", "BOT_1", "QQQ", "1Day", "kip-v1", "Momentum and trend following",
          _books(CALM_UP=TREND, CALM=TREND, CHOP=Playbook("trend_long", 0.05, None, max_size=0.5),
                 STRESS=Playbook("trend_short", 0.05, None)), static_state="CALM_UP"),
    Agent("Nemo", "BOT_2", "SPY", "1Day", "nemo-v1", "Mean reversion",
          _books(CALM_UP=REVERT, CALM=REVERT, CHOP=REVERT, STRESS=REVERT), static_state="CHOP"),
    Agent("Chud", "BOT_3", "IWM", "1Day", "chud-v1", "Volatility and regime transitions",
          _books(CALM_UP=TRANSITION, CALM=TRANSITION, CHOP=TRANSITION, STRESS=TRANSITION), static_state=None),
    Agent("Era", "BOT_4", "QQQ", "5Min", "era-v1", "Intraday momentum and opening range",
          _books(CALM_UP=RANGE, CALM=RANGE, CHOP=RANGE, STRESS=RANGE), static_state="CALM_UP"),
    Agent("Aura", "BOT_5", "QQQ", "1Day", "aura-v1", "Adaptive quantitative strategy research",
          _books(CALM_UP=BREAKOUT, CALM=BREAKOUT, CHOP=REVERT, STRESS=REVERT), static_state="CALM_UP"),
]}


def variant(agent: Agent, version: str, changes: dict) -> Agent:
    """A new strategy made from an existing one. `changes` maps a state (or "*" for every
    trading state) to Playbook fields; "params" is merged into the rule's settings.
    The original agent is not touched, and CRASH stays flat."""
    if "CRASH" in changes:
        raise ValueError("CRASH is flat for every agent; a variant cannot trade it")
    books = {}
    for state, book in agent.playbooks.items():
        if book is None:
            books[state] = None
            continue
        fields = {**changes.get("*", {}), **changes.get(state, {})}
        params = {**book.params, **changes.get("*", {}).get("params", {}), **changes.get(state, {}).get("params", {})}
        fields = {k: v for k, v in fields.items() if k != "params"}
        if "rule" in fields and fields["rule"] not in RULES:
            raise ValueError(f"unknown rule {fields['rule']!r}")
        books[state] = replace(book, **fields, params=params)
    return replace(agent, version=version, playbooks=books)


def _shape(agent: Agent) -> dict:
    return {s: None if b is None else [b.rule, b.stop_pct, b.max_days, b.max_size, sorted(b.params.items())] for s, b in agent.playbooks.items()}


def fingerprint(agent: Agent) -> str:
    """The same strategy under any name has the same fingerprint."""
    text = json.dumps([agent.symbol, agent.timeframe, _shape(agent)], sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def complexity(agent: Agent) -> int:
    """Distinct playbooks plus the number of settings moved off their defaults."""
    shapes = [json.dumps(v, default=str) for v in _shape(agent).values() if v]
    return len(set(shapes)) + sum(len(b.params) for b in agent.playbooks.values() if b)


def write_playbooks(folder="playbooks") -> None:
    """playbooks/<Agent>/<STATE>.md, generated from the definitions above so the words
    and the code cannot disagree."""
    from ..regime import POLICY

    for a in AGENTS.values():
        out = Path(folder) / a.id
        out.mkdir(parents=True, exist_ok=True)
        for state, book in a.playbooks.items():
            head = f"# Agent {a.id} in {state}\n\n{a.bot}, {a.symbol}, strategy version {a.version}. Focus: {a.focus}.\n\n"
            if book is None:
                body = ("**Flat.** No new trades, and any open trade is closed at the next open.\n\n"
                        "- **Entry:** none\n- **Exit:** close everything\n- **Stop:** not applicable\n- **Take profit:** not applicable\n"
                        "- **Max size:** zero\n- **Invalidation:** not applicable\n")
            else:
                entry, exit_, invalid = WORDS[book.rule]
                stop = ("The far side of the opening range." if book.rule == "opening_range" else f"{book.stop_pct:.0%} from the entry price.")
                held = ("Same day only." if book.max_days == 0 else f"Time stop after {book.max_days} trading days." if book.max_days else "No time stop.")
                size = POLICY[state] * book.max_size
                body = (f"- **Entry:** {entry}\n- **Exit:** {exit_} {held} Also closed if the state changes to one with a different playbook, "
                        f"or to a flat or uncertain state.\n- **Stop:** {stop}\n- **Take profit:** {book.take_profit}\n"
                        f"- **Max size:** {size:.0%} of this agent's allocation (the state allows {POLICY[state]:.0%}, this playbook {book.max_size:.0%}), "
                        f"cut to half early if stress looks likely, and never above the central risk limits.\n- **Invalidation:** {invalid}\n")
            (out / f"{state}.md").write_text(head + body + "\nA starting hypothesis, not a proven strategy. The code decides; this page describes it.\n")
