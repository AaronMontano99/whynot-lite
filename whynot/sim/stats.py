"""Performance arithmetic. Plain definitions, no smoothing, nothing tuned."""

import math
from datetime import date


def _mean(x):
    return sum(x) / len(x) if x else 0.0


def _sd(x):
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1)) if len(x) > 1 else 0.0


def curve(equity: list) -> dict:
    """From (day, account value) pairs: return, risk-adjusted return and drawdown."""
    values = [v for _, v in equity]
    if len(values) < 2:
        return {"days": len(values), "total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0, "drawdown_days": 0}
    rets = [b / a - 1 for a, b in zip(values, values[1:])]
    sd, down = _sd(rets), math.sqrt(_mean([min(r, 0.0) ** 2 for r in rets]))
    peak, worst, under, longest = values[0], 0.0, 0, 0
    for v in values:
        if v >= peak:
            peak, under = v, 0
        else:
            under += 1
            worst, longest = max(worst, 1 - v / peak), max(longest, under)
    years = max((equity[-1][0] - equity[0][0]).days / 365.25, 1e-9)
    total = values[-1] / values[0] - 1
    return {"days": len(values), "total_return": total, "cagr": (1 + total) ** (1 / years) - 1 if total > -1 else -1.0,
            "sharpe": _mean(rets) / sd * math.sqrt(252) if sd else 0.0, "sortino": _mean(rets) / down * math.sqrt(252) if down else 0.0,
            "max_drawdown": worst, "drawdown_days": longest}


def trades(items: list) -> dict:
    rets, pnls = [t.ret for t in items], [t.pnl for t in items]
    wins, losses = [r for r in rets if r > 0], [r for r in rets if r <= 0]
    gain, loss = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p <= 0)
    sd = _sd(rets)
    out = {"count": len(rets), "hit_rate": len(wins) / len(rets) if rets else 0.0, "expectancy": _mean(rets),
           "profit_factor": gain / loss if loss else (math.inf if gain else 0.0),
           "avg_win": _mean(wins), "avg_loss": _mean(losses), "payoff": _mean(wins) / -_mean(losses) if wins and _mean(losses) else 0.0,
           "t_stat": _mean(rets) / (sd / math.sqrt(len(rets))) if sd and rets else 0.0, "worst": min(rets) if rets else 0.0}
    for key, of in (("by_state", lambda t: t.state or "none"), ("by_year", lambda t: t.entry_day.year if isinstance(t.entry_day, date) else t.entry_day)):
        groups = {}
        for t in items:
            groups.setdefault(of(t), []).append(t.ret)
        out[key] = {k: {"count": len(v), "hit_rate": sum(r > 0 for r in v) / len(v), "expectancy": _mean(v)} for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}
    return out


def gates(c: dict, t: dict, beats_hold: bool, beats_static: bool) -> dict:
    """The owner's acceptance targets. Research targets, not proof of anything."""
    g = {"sharpe": c["sharpe"] > 1.5, "max_drawdown": c["max_drawdown"] < 0.15, "hit_rate": t["hit_rate"] > 0.55,
         "t_stat": t["t_stat"] > 2.0, "beats_buy_and_hold": bool(beats_hold), "beats_static": bool(beats_static)}
    g["all"] = all(g.values())
    return g


def buy_and_hold(candles: list, start: date, end: date, equity: float = 20000.0) -> list:
    span = [c for c in candles if start <= c.day <= end]
    if not span:
        return []
    shares = equity / span[0].open
    return [(c.day, shares * c.close) for c in span]
