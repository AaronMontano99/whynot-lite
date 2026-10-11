"""Black-Scholes, used only to DERIVE implied volatility and Greeks from a real traded
price. It never produces a price that is then treated as a fill."""

import math


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d(spot, strike, t, vol, r):
    sd = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / sd
    return d1, d1 - sd


def price(kind: str, spot: float, strike: float, t: float, vol: float, r: float = 0.0) -> float:
    d1, d2 = _d(spot, strike, t, vol, r)
    if kind == "call":
        return spot * _cdf(d1) - strike * math.exp(-r * t) * _cdf(d2)
    return strike * math.exp(-r * t) * _cdf(-d2) - spot * _cdf(-d1)


def delta(kind: str, spot: float, strike: float, t: float, vol: float, r: float = 0.0) -> float:
    d1, _ = _d(spot, strike, t, vol, r)
    return _cdf(d1) if kind == "call" else _cdf(d1) - 1.0


def greeks(kind: str, spot: float, strike: float, t: float, vol: float, r: float = 0.0) -> dict:
    d1, d2 = _d(spot, strike, t, vol, r)
    carry = r * strike * math.exp(-r * t) * (_cdf(d2) if kind == "call" else -_cdf(-d2))
    return {"delta": delta(kind, spot, strike, t, vol, r), "gamma": _pdf(d1) / (spot * vol * math.sqrt(t)),
            "vega": spot * _pdf(d1) * math.sqrt(t) / 100.0,                               # per 1 point of volatility
            "theta": (-spot * _pdf(d1) * vol / (2.0 * math.sqrt(t)) - carry) / 365.0}     # per calendar day


def implied_vol(kind: str, premium: float, spot: float, strike: float, t: float, r: float = 0.0) -> float | None:
    """The volatility at which the model gives this premium, or None when no volatility
    does (a price at or below exercise value, or no time left)."""
    if t <= 0 or premium <= 0:
        return None
    lo, hi = 0.01, 5.0
    if not price(kind, spot, strike, t, lo, r) < premium < price(kind, spot, strike, t, hi, r):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if price(kind, spot, strike, t, mid, r) < premium:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
