"""Deterministic contract selection and fills.

Selection looks only at contracts that actually traded on the decision day. A fill is
the real opening trade of the next day, moved against us by an assumed cost. A contract
that did not trade cannot be bought or sold: there is no fill, and no price is invented."""

from dataclasses import dataclass, field, replace
from datetime import date

from . import pricing

LABELS = {"price": "real: traded prices, volume and trade counts from the provider",
          "iv": "derived from the day's last traded price and the fund's close (Black-Scholes, no dividends)",
          "greeks": "derived from that implied volatility",
          "slippage": "assumed: no historical bid/ask exists, so each fill is moved by the larger of 2 cents and 1.5% of the premium",
          "open_interest": "not available historically"}


@dataclass(frozen=True)
class SelectionRule:
    target_delta: float = 0.50
    min_delta: float = 0.25
    max_delta: float = 0.75
    min_dte: int = 21               # days to expiration at the decision
    min_volume: int = 50            # contracts traded that day
    min_trades: int = 5
    max_volume_share: float = 0.10  # never more than a tenth of the day's volume
    slip_floor: float = 0.02        # dollars
    slip_pct: float = 0.015         # of the premium
    exit_dte: int = 7               # out of a position this many days before it expires
    standard_only: bool = True      # skip strikes that are not multiples of $0.50: they are contracts adjusted after a
                                    # corporate action, and what they deliver may not be 100 ordinary shares


@dataclass(frozen=True)
class Quote:
    contract: str
    kind: str
    strike: float
    expiration: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: float


@dataclass(frozen=True)
class Choice:
    contract: str
    kind: str
    strike: float
    expiration: date
    dte: int
    ref_price: float                # the day's last traded price
    qty: int
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    volume: float
    trades: float
    est_slippage: float
    max_premium: float              # the most this order can lose
    labels: dict = field(default_factory=lambda: dict(LABELS), compare=False)


def slip(price: float, rule: SelectionRule) -> float:
    return max(rule.slip_floor, price * rule.slip_pct)


def select_contract(data, underlying: str, direction: str, day: date, spot: float, budget: float, rule: SelectionRule,
                    rate: float = 0.0, min_qty: int = 0) -> tuple:
    """(Choice, why) or (None, why not). direction LONG buys a call, SHORT buys a put.
    With min_qty=1 a single contract is allowed if it fits the budget."""
    kind = "call" if direction == "LONG" else "put"
    expiries = [e for e in data.expirations(underlying) if (e - day).days >= rule.min_dte]
    seen_any = thin = 0
    for exp in expiries[:3]:                                    # the nearest that qualifies wins
        chain = data.chain(underlying, kind, exp, day)
        if not chain:
            continue
        t, best = (exp - day).days / 365.0, None
        for q in chain:
            seen_any += 1
            if rule.standard_only and abs(q.strike * 2 - round(q.strike * 2)) > 1e-9:
                continue
            if q.volume < rule.min_volume or q.trades < rule.min_trades:
                thin += 1
                continue
            iv = pricing.implied_vol(kind, q.close, spot, q.strike, t, rate)
            if iv is None:
                continue
            d = pricing.delta(kind, spot, q.strike, t, iv, rate)
            if not rule.min_delta <= abs(d) <= rule.max_delta:
                continue
            gap = abs(abs(d) - rule.target_delta)
            if best is None or gap < best[0]:
                best = (gap, q, iv)
        if best:                                                # the contract is chosen on its merits, then checked against the money
            _, q, iv = best
            qty = min(int(budget // (q.close * 100)), int(q.volume * rule.max_volume_share))
            if qty < 1:
                return None, f"one contract costs more than the ${budget:,.0f} this trade is allowed (${q.close * 100:,.0f})"
            g = pricing.greeks(kind, spot, q.strike, t, iv, rate)
            choice = Choice(q.contract, kind, q.strike, exp, (exp - day).days, q.close, qty, iv, g["delta"], g["gamma"], g["theta"], g["vega"],
                            q.volume, q.trades, slip(q.close, rule), q.close * 100 * qty)
            return choice, f"{kind} {q.strike:g} expiring {exp}, delta {g['delta']:+.2f}, nearest the {rule.target_delta:.2f} target among liquid contracts"
    if not seen_any:
        return None, f"no {kind} at least {rule.min_dte} days from expiry traded that day"
    if thin:
        return None, "no liquid contract: every candidate traded too little that day"
    return None, "no contract in the allowed delta range"


def entry_fill(data, contract: str, day: date, rule: SelectionRule) -> tuple:
    """Buy at the day's first real trade plus the assumed cost."""
    bar = data.bar(contract, day)
    if bar is None:
        return None, "no trade printed that day"
    return bar.open + slip(bar.open, rule), "real opening trade plus assumed cost"


def exit_fill(data, contract: str, day: date, rule: SelectionRule, at: str = "open") -> tuple:
    """Sell at the day's first (or last) real trade less the assumed cost."""
    bar = data.bar(contract, day)
    if bar is None:
        return None, "no trade printed that day"
    raw = bar.open if at == "open" else bar.close
    return max(raw - slip(raw, rule), 0.0), f"real {'opening' if at == 'open' else 'closing'} trade less assumed cost"


def settle(kind: str, strike: float, spot: float) -> float:
    """What the contract is worth at expiry. Arithmetic, not a market price."""
    return max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)


def pick(data, underlying: str, direction: str, day: date, spot: float, want: float, cap: float, rule: SelectionRule, rate: float = 0.0) -> tuple:
    """(Choice or None, why, size note). `want` is what sizing asked for and `cap` the hard
    position limit. Contracts come whole: if the chosen one costs more than `want` but
    fits inside `cap`, one contract is taken. Used by the backtest and by live trading."""
    choice, why = select_contract(data, underlying, direction, day, spot, min(want, cap), rule, rate)
    note = ""
    if choice is None and "costs more" in why and want < cap:
        choice, why = select_contract(data, underlying, direction, day, spot, cap, rule, rate)
        if choice:
            choice = replace(choice, qty=1, max_premium=choice.ref_price * 100)
            note = "; one contract is the smallest order and it fits inside the position limit"
    return choice, why, note
