"""Era's wild card: each day after the close, find the stock the news is suddenly about,
and if it also moved hard, follow the move for a few days.

"Hot" is a count, not a reading: how many headlines named the stock since the last close,
against its own usual number. No headline is interpreted. Everything used at a close was
published or traded by that close; the order is carried out at the next open.

Order of events on a day: yesterday's exits and entry fill at the open; stops are checked
against the day's range; the account is marked at the close; then the day's decision."""

from dataclasses import dataclass, replace
from datetime import date

from .daily import STOP_SLIP, Decision, Trade, check_period, sign


@dataclass(frozen=True)
class WildRule:
    min_headlines: int = 5              # named by at least this many headlines since the last close
    baseline_days: int = 60             # ... compared with its own average over this many earlier sessions
    min_heat: float = 3.0               # headlines today / (usual number + 1)
    min_move: float = 0.02              # and the stock itself moved at least this much on the day
    min_price: float = 10.0
    min_dollar_volume: float = 50e6     # average over liquidity_days, so its options can be expected to trade
    liquidity_days: int = 20
    hold_days: int = 3
    stop: float = 0.06
    max_positions: int = 2
    cost_bps: float = 5.0
    rank_by: str = "heat"               # "move" ranks by the size of the day's move instead (the control without news)


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class WildTrade(Trade):
    symbol: str = ""


@dataclass
class WildResult:
    decisions: list
    trades: list
    equity: list
    open_positions: list


def heat(count: int, earlier: list) -> float:
    """How unusual today's number of headlines is for this stock. 1 means ordinary."""
    return count / (sum(earlier) / len(earlier) + 1) if earlier else 0.0


def candidates(i: int, days: list, counts: dict, bars: dict, rule: WildRule) -> list:
    """Stocks that qualify at the close of days[i], hottest first. Looks only at days[:i + 1]."""
    out = []
    if i < max(rule.baseline_days, rule.liquidity_days):
        return out
    for symbol, n in counts.get(days[i], {}).items():
        if n < rule.min_headlines:
            continue
        h = heat(n, [counts.get(days[j], {}).get(symbol, 0) for j in range(i - rule.baseline_days, i)])
        series = bars.get(symbol, {})
        recent = [series.get(days[j]) for j in range(i - rule.liquidity_days, i + 1)]
        if h < rule.min_heat or any(b is None for b in recent):
            continue
        today, before = recent[-1], recent[-2]
        traded = sum(b.close * b.volume for b in recent[1:]) / rule.liquidity_days
        move = today.close / before.close - 1
        if today.close < rule.min_price or traded < rule.min_dollar_volume or abs(move) < rule.min_move:
            continue
        out.append({"symbol": symbol, "heat": h, "headlines": n, "move": move, "direction": "LONG" if move > 0 else "SHORT"})
    return sorted(out, key=lambda c: (-abs(c["move"]) if rule.rank_by == "move" else -c["heat"], c["symbol"]))


def run_wild(days: list, counts: dict, bars: dict, start: date, end: date, rule: WildRule = WildRule(), equity: float = 20000.0,
             states: dict | None = None) -> WildResult:
    """days: every session in order. counts: day -> {symbol: headlines between the previous close and that day's close}.
    bars: symbol -> {day: Bar}. states: day -> the market regime that day, recorded on each trade and used for nothing else."""
    check_period(start, end)
    rate = rule.cost_bps / 1e4
    cash, value, positions, entry, exits = equity, equity, [], None, []
    decisions, trades, curve, last = [], [], [], {}

    def sell(pos, day, price, reason):
        nonlocal cash
        pnl = sign(pos.direction) * (price - pos.entry_price) * pos.shares          # both prices already carry their costs
        done = replace(pos, exit_day=day, exit_price=price, exit_reason=reason, pnl=pnl, ret=pnl / (pos.entry_price * pos.shares),
                       costs=(pos.entry_price + price) * pos.shares * rate)
        cash += pos.entry_price * pos.shares + done.pnl
        positions.remove(pos)
        trades.append(done)

    for i, day in enumerate(days):
        if day < start or day > end:
            continue
        price_of = lambda s: bars.get(s, {}).get(day)

        # 1. what was decided at the last close is carried out at this open
        for pos, reason in exits:
            bar = price_of(pos.symbol)
            if pos in positions and bar:
                sell(pos, day, bar.open * (1 - sign(pos.direction) * rate), reason)
        exits = [(p, r) for p, r in exits if p in positions]          # no price today: try again tomorrow
        if entry:
            bar, s = price_of(entry["symbol"]), sign(entry["direction"])
            price = bar.open * (1 + s * rate) if bar else 0.0
            shares = int(min(value / rule.max_positions, cash) / price) if bar else 0
            if shares > 0:
                positions.append(WildTrade(entry["direction"], "news_heat", entry["state"], day, price, shares, 1 / rule.max_positions,
                                           price * (1 - s * rule.stop), entry["reason"], features=entry["features"],
                                           max_days=rule.hold_days, symbol=entry["symbol"]))
                cash -= price * shares
            else:
                decisions.append(Decision(day, "REJECT", f"{entry['symbol']}: " + ("no price at the open" if not bar else "not enough cash for one share")))
            entry = None

        # 2. stops, against the day's range; a gap through the stop fills at the open
        for pos in list(positions):
            bar, s = price_of(pos.symbol), sign(pos.direction)
            if not bar:
                continue
            hit = bar.low <= pos.stop if s > 0 else bar.high >= pos.stop
            if hit:
                gapped = bar.open <= pos.stop if s > 0 else bar.open >= pos.stop
                sell(pos, day, (bar.open if gapped else pos.stop) * (1 - s * rate * STOP_SLIP), "stop")
                continue
            pos.days_held += 1
            best, worst = (bar.high, bar.low) if s > 0 else (bar.low, bar.high)
            pos.mfe = max(pos.mfe, s * (best / pos.entry_price - 1))
            pos.mae = min(pos.mae, s * (worst / pos.entry_price - 1))

        # 3. the account at the close
        for pos in positions:
            if price_of(pos.symbol):
                last[pos.symbol] = price_of(pos.symbol).close
        value = cash + sum(p.shares * (p.entry_price + sign(p.direction) * (last.get(p.symbol, p.entry_price) - p.entry_price)) for p in positions)
        curve.append((day, value))

        # 4. the decision, with nothing later than this close
        for pos in positions:
            if pos.days_held >= rule.hold_days and not any(p is pos for p, _ in exits):
                exits.append((pos, f"time: held {pos.days_held} sessions"))
                decisions.append(Decision(day, "EXIT", f"{pos.symbol}: held {pos.days_held} sessions", direction=pos.direction))
        found = [c for c in candidates(i, days, counts, bars, rule) if all(p.symbol != c["symbol"] for p in positions)]
        if found and len(positions) < rule.max_positions:
            c = found[0]
            reason = (f"{c['symbol']}: {c['headlines']} headlines since the last close, {c['heat']:.1f} times its usual, and the stock moved "
                      f"{c['move']:+.1%} on the day")
            entry = {**c, "reason": reason, "state": (states or {}).get(day), "features": {"heat": round(c["heat"], 2), "headlines": c["headlines"], "move": round(c["move"], 4)}}
            decisions.append(Decision(day, "ENTER", reason, direction=c["direction"], fraction=1 / rule.max_positions))
        elif found:
            decisions.append(Decision(day, "REJECT", f"{found[0]['symbol']} is hot but {rule.max_positions} positions are already open"))
        else:
            decisions.append(Decision(day, "NONE", "nothing hot enough"))
    return WildResult(decisions, trades, curve, positions)
