"""Failure analysis. One losing trade gets one likely cause from a fixed list, by simple
stated rules of thumb. A cause is a note for research; it never changes how anything
trades, and a pattern only counts once there is a lot of it."""

import json

from ..regime import DANGER

CAUSES = ["Normal statistical loss", "Regime misclassification", "Regime transition", "Poor entry timing", "Poor exit timing",
          "Volatility expansion", "Volatility contraction", "Liquidity and slippage", "Excessive position size", "Correlated exposure",
          "News or event shock", "Model calibration failure", "Stale or incorrect data", "Execution failure"]
MIN_COUNT, MIN_SHARE = 30, 0.40          # before a cause is worth a research hypothesis


def classify(t: dict) -> str:
    """t: a losing trade with ret, pnl, mfe, mae, exit_reason, days_held, costs, state, state_at_exit, flags, account, gapped."""
    flags = json.loads(t.get("flags") or "[]") if isinstance(t.get("flags"), str) else (t.get("flags") or [])
    loss = abs(t["pnl"]) or 1e-9
    if flags:
        return "Stale or incorrect data"
    if t.get("gapped"):
        return "News or event shock"                    # the market opened through the stop
    if t.get("state_at_exit") and t["state_at_exit"] != t.get("state"):
        return "Regime transition"
    if t.get("account") and loss / t["account"] > 0.04:
        return "Excessive position size"
    if t.get("costs", 0.0) / loss > 0.5:
        return "Liquidity and slippage"
    if t.get("mfe", 0.0) >= max(2 * abs(t["ret"]), 0.02):
        return "Poor exit timing"                       # it was well in profit before it lost
    if t.get("mfe", 0.0) < 0.002 and t.get("days_held", 99) <= 1:
        return "Poor entry timing"                      # wrong from the first day
    if t.get("state") in DANGER and t.get("exit_reason") == "stop":
        return "Volatility expansion"
    return "Normal statistical loss"


def enough_evidence(rows: list) -> list:
    """Causes (other than ordinary losses) common enough to justify a research hypothesis."""
    counts = {}
    for r in rows:
        counts[r["cause"]] = counts.get(r["cause"], 0) + 1
    total = sum(counts.values())
    return [c for c, n in counts.items() if c != "Normal statistical loss" and n >= MIN_COUNT and n / total >= MIN_SHARE]
