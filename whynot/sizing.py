"""Layer 3: how big. Position size scales with confidence and is zero whenever the
code says stand aside. The model supplies probabilities; it never sets a size.

  fraction of equity = quarter Kelly (at most)
                       x filtered probability of the active state
                       x (1 - posterior entropy)
                       x the switching decision's multiplier (0, 0.5 or 1)
capped by the state's leverage cap in risk.py.

Kelly needs a win probability. Until that probability has been checked against the
bot's own fills in that state (Brier score), only a small probe size is allowed."""

PROBE = 0.02                 # of equity, while calibration is unverified
KELLY_SHARE = 0.25           # quarter Kelly
MIN_FILLS = 30               # fills in a state before its calibration can be judged
MAX_BRIER = 0.25             # 0.25 is what always saying "50%" scores; must beat it


def kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Full-Kelly fraction of equity for a bet that returns +avg_win or -avg_loss
    per dollar staked. Zero when there is no measured edge."""
    if not (0 < win_rate < 1) or avg_win <= 0 or avg_loss <= 0:
        return 0.0
    return max(0.0, win_rate / avg_loss - (1 - win_rate) / avg_win)


def edge(trades: list) -> dict:
    """Win rate and average win/loss as a fraction of the premium paid."""
    rets = [t["pnl"] / (t["entry_price"] * 100 * t["contracts"]) for t in trades
            if t["entry_price"] * t["contracts"] > 0]
    wins, losses = [r for r in rets if r > 0], [-r for r in rets if r <= 0]
    n = len(rets)
    out = {"trades": n, "win_rate": len(wins) / n if n else 0.0,
           "avg_win": sum(wins) / len(wins) if wins else 0.0,
           "avg_loss": sum(losses) / len(losses) if losses else 0.0}
    out["kelly"] = kelly(out["win_rate"], out["avg_win"], out["avg_loss"])
    return out


def brier(pairs: list) -> float | None:
    """Mean squared error of predicted win probabilities against real fills.
    pairs: [(predicted win probability, won)]."""
    if not pairs:
        return None
    return sum((p - (1.0 if won else 0.0)) ** 2 for p, won in pairs) / len(pairs)


def calibrated(pairs: list) -> bool:
    score = brier(pairs)
    return len(pairs) >= MIN_FILLS and score is not None and score < MAX_BRIER


def reliability(pairs: list, bins: int = 5) -> list:
    """Reliability curve from (predicted win probability, won) pairs."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        hit = [won for p, won in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if hit:
            out.append({"predicted": f"{lo:.1f}-{hi:.1f}", "fills": len(hit),
                        "won": round(sum(hit) / len(hit), 3)})
    return out


def fraction(edge_stats: dict, state_prob: float, entropy: float, decision_size: float,
             cap: float, verified: bool) -> tuple:
    """(fraction of equity to spend on premium, why)."""
    if decision_size <= 0:
        return 0.0, "switching rules say flat"
    k = edge_stats.get("kelly", 0.0)
    if k <= 0:
        return 0.0, "playbook has no measured edge"
    f = KELLY_SHARE * k * state_prob * max(0.0, 1.0 - entropy) * decision_size
    why = "quarter Kelly x state probability x certainty"
    if not verified:
        f, why = min(f, PROBE * decision_size), "probe size until calibration is verified on fills"
    f = min(f, cap)
    return (f, why) if f > 0 else (0.0, "size rounds to zero")
