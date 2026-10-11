"""Agent Era's intraday engine: the opening-range breakout on 5-minute candles.

The regime it trades under is the one known at the previous session's close. A decision
made on a 5-minute close is carried out at the next candle's open. It is always flat by
the end of the session."""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime

from ..ingest.timeutil import ET, et_time
from ..regime import POLICY
from .agents import DEFAULTS
from .daily import STOP_SLIP, Decision, Result, Trade, check_period, close_trade, sign

# Version one: the first 30 minutes make the range, it must be 0.1% to 1.5% of price, no new trade after 15:00.


@dataclass
class Bar5:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def five_minute(minute_bars: list) -> list:
    """Regular-session 1-minute bars (ts, open, high, low, close, volume) -> 5-minute bars."""
    if not minute_bars:
        return []
    day = datetime.fromtimestamp(minute_bars[0][0], ET).date()
    start, end = et_time(day, "09:30"), et_time(day, "16:00")
    out = {}
    for ts, o, h, l, c, v in minute_bars:
        if not start <= ts < end:
            continue
        k = (ts - start) // 300
        b = out.get(k)
        if b is None:
            out[k] = Bar5(start + k * 300, o, h, l, c, v or 0)
        else:
            b.high, b.low, b.close, b.volume = max(b.high, h), min(b.low, l), c, b.volume + (v or 0)
    return [out[k] for k in sorted(out)]


def clock(ts: int) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%H:%M")


def run_intraday(agent, sessions, records: dict, start: date, end: date, cost_bps: float, gated: bool = True,
                 equity: float = 20000.0) -> Result:
    check_period(start, end)
    rate, cash = cost_bps / 1e4, equity
    known = sorted(records)
    decisions, trades, curve = [], [], []
    for d in sorted(k for k in sessions.keys() if start <= k <= end):
        bars = five_minute(sessions[d])
        i = bisect_left(known, d)
        rec = records[known[i - 1]] if i else None              # yesterday's regime, never today's
        if gated:
            state = rec.active if rec else None
            allowed, frozen = (rec.size if rec else 0.0), bool(rec and rec.frozen)
            book = agent.playbooks.get(state) if state else None
        else:
            state, allowed, frozen, book = agent.static_state, 1.0, False, agent.playbooks[agent.static_state]
        said = len(decisions)
        p = {**DEFAULTS["opening_range"], **(book.params if book else {})}
        RANGE_BARS, MIN_WIDTH, MAX_WIDTH, LAST_ENTRY_MINUTE = p["range_bars"], p["min_width"], p["max_width"], p["last_entry_minute"]
        if len(bars) < RANGE_BARS * 3:
            decisions.append(Decision(d, "NONE", f"only {len(bars)} five-minute bars: not enough to trade", state))
            curve.append((d, cash))
            continue
        hi, lo = max(b.high for b in bars[:RANGE_BARS]), min(b.low for b in bars[:RANGE_BARS])
        width, open_ts = (hi - lo) / bars[0].open, bars[0].ts
        pos, pending, done_today, last = None, None, False, len(bars) - 1
        for n in range(RANGE_BARS, len(bars)):
            b = bars[n]
            if pending:
                kind, info = pending
                if kind == "ENTER":
                    price = b.open * (1 + sign(info["direction"]) * rate)
                    shares = int(cash * info["fraction"] / price)
                    if shares > 0:
                        pos = Trade(info["direction"], "opening_range", state, d, price, shares, info["fraction"],
                                    lo if info["direction"] == "LONG" else hi, info["reason"], dict(rec.probs) if rec else {},
                                    {"range_high": hi, "range_low": lo, "range_width": round(width, 5)}, entry_time=clock(b.ts))
                        entered_at = n
                elif pos:
                    t = close_trade(pos, d, b.open * (1 - sign(pos.direction) * rate), info["reason"], rate)
                    t.exit_time = clock(b.ts)
                    cash += t.pnl
                    trades.append(t)
                    pos = None
                pending = None
            if pos:
                s = sign(pos.direction)
                worst, best = (b.low, b.high) if s > 0 else (b.high, b.low)
                if s * (worst - pos.stop) <= 0:
                    raw = b.open if n != entered_at and s * (b.open - pos.stop) <= 0 else pos.stop
                    pos.mae = min(pos.mae, s * (raw / pos.entry_price - 1))
                    t = close_trade(pos, d, raw * (1 - s * rate * STOP_SLIP), "stop", rate)
                    t.exit_time = clock(b.ts)
                    cash += t.pnl
                    trades.append(t)
                    decisions.append(Decision(d, "EXIT", "stop: price crossed back through the opening range", state, pos.direction, time=clock(b.ts)))
                    pos = None
                else:
                    pos.mfe, pos.mae = max(pos.mfe, s * (best / pos.entry_price - 1)), min(pos.mae, s * (worst / pos.entry_price - 1))
            if pos and n == last - 1:
                pending = ("EXIT", {"reason": "session end"})
                decisions.append(Decision(d, "EXIT", "session end", state, pos.direction, time=clock(b.ts)))
            elif pos is None and not done_today and n < last - 1 and (b.ts - open_ts) // 60 < LAST_ENTRY_MINUTE:
                direction = "LONG" if b.close > hi else "SHORT" if b.close < lo else None
                if direction is None:
                    continue
                done_today = True                                # one signal a day, taken or refused
                why = f"5-minute close {'above' if direction == 'LONG' else 'below'} the opening range ({lo:.2f} to {hi:.2f})"
                refuse = (f"the opening range is {width:.2%} of price, outside {MIN_WIDTH:.1%} to {MAX_WIDTH:.1%}" if not MIN_WIDTH <= width <= MAX_WIDTH
                          else "no regime record before this session" if gated and rec is None
                          else f"frozen, no new entries ({rec.reason})" if frozen
                          else f"state {state} at zero size ({rec.reason if rec else 'no record'})" if book is None or allowed <= 0 or POLICY.get(state, 0) <= 0
                          else None)
                if refuse:
                    decisions.append(Decision(d, "REJECT", f"{direction} signal ({why}) refused: {refuse}", state, direction, time=clock(b.ts)))
                else:
                    fraction = allowed * book.max_size
                    pending = ("ENTER", {"direction": direction, "fraction": fraction, "reason": why})
                    decisions.append(Decision(d, "ENTER", why, state, direction, fraction, time=clock(b.ts)))
        if pos:                                                  # should not happen; never carry a position overnight
            t = close_trade(pos, d, bars[-1].close * (1 - sign(pos.direction) * rate), "session end", rate)
            t.exit_time = clock(bars[-1].ts)
            cash += t.pnl
            trades.append(t)
        if len(decisions) == said:
            decisions.append(Decision(d, "NONE", "no breakout from the opening range", state))
        curve.append((d, cash))
    return Result(decisions, trades, curve, None)
