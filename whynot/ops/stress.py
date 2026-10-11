"""Stress replays: the engine's mechanics walked through real crisis price paths.

The trader here is a dummy that is always long with a 5% stop and ignores the regime.
It is not a strategy and says nothing about any agent; it exists to check that stops,
gaps, accounting and daily decisions behave when prices do their worst. Instruments are
used only where they existed; 1987 uses the S&P 500 index as a labeled stand-in."""

from datetime import date

from ..sim import stats
from ..sim.agents import RULES, Agent, Playbook
from ..sim.daily import run_daily

PERIODS = [
    ("1987 crash", "^GSPC", date(1987, 8, 3), date(1987, 12, 31), "S&P 500 index, a stand-in: no fund of ours existed"),
    ("Dot-com collapse", "QQQ", date(2000, 3, 1), date(2002, 10, 31), "QQQ itself"),
    ("2008 financial crisis", "SPY", date(2008, 6, 2), date(2009, 3, 31), "SPY itself"),
    ("2020 COVID crash", "SPY", date(2020, 2, 3), date(2020, 4, 30), "SPY itself"),
    ("2022 inflation and rates", "QQQ", date(2022, 1, 3), date(2022, 12, 30), "QQQ itself"),
]
STOP = 0.05


def dummy() -> Agent:
    RULES.setdefault("stress_always_long", ((lambda ctx: ("LONG", "stress dummy: always long")), (lambda ctx: None)))
    book = Playbook("stress_always_long", stop_pct=STOP, max_days=None)
    return Agent("StressDummy", "none", "n/a", "1Day", "dummy", "mechanics check only", {"CALM_UP": book, "CRASH": None}, static_state="CALM_UP")


def candles_for(cat, symbol: str) -> list:
    from ..regime import Candle
    from ..ingest.timeutil import et_day
    ds = cat.find(symbol, "1Day", "yahoo")
    return [Candle(et_day(b["ts"]), b["open"], b["high"], b["low"], b["close"], b["volume"] or 0.0) for b in cat.bars(ds, allow_proxy=True)]


def replay(candles: list, start: date, end: date, cost_bps: float = 1.0):
    return run_daily(dummy(), [c for c in candles if c.day <= end], {}, start, end, cost_bps, gated=False)


def violations(result, candles: list, cost_bps: float = 1.0) -> list:
    """Everything that would mean the mechanics are wrong. An empty list is a pass."""
    by_day, out, slack = {c.day: c for c in candles}, [], 4 * cost_bps / 1e4 + 1e-9
    for t in result.trades:
        if t.exit_reason != "stop":
            continue
        opened = by_day[t.exit_day].open
        gapped = t.exit_day != t.entry_day and opened <= t.stop
        if gapped and abs(t.exit_price / opened - 1) > slack:
            out.append(f"{t.exit_day}: gapped through the stop but filled at {t.exit_price:.2f}, not near the open {opened:.2f}")
        if not gapped and t.ret < -(STOP + slack):
            out.append(f"{t.exit_day}: lost {t.ret:.2%} on a stop that was never gapped")
    for a, b in zip(result.trades, result.trades[1:]):
        if b.entry_day < a.exit_day:
            out.append(f"trades overlap: {a.exit_day} and {b.entry_day}")
    booked = 20_000.0 + sum(t.pnl for t in result.trades)
    open_ = result.open_position
    if open_ is not None and result.equity:
        booked += (by_day[result.equity[-1][0]].close - open_.entry_price) * open_.shares
    if result.equity and abs(result.equity[-1][1] - booked) > 0.01:
        out.append(f"account value {result.equity[-1][1]:.2f} does not equal the trades booked {booked:.2f}")
    if any(v <= 0 for _, v in result.equity):
        out.append("the account value went to zero or below")
    sessions = [c.day for c in candles if result.equity and result.equity[0][0] <= c.day <= result.equity[-1][0]]
    if [d.day for d in result.decisions] != sessions:
        out.append("a session went by without a decision")
    return out


def report(cat) -> str:
    out = ["# Stress replays", "",
           "A dummy that is always long with a 5% stop, walked through real crisis prices to check the engine's mechanics: stops, gaps through a stop, "
           "accounting and a decision every session. It is not a strategy and says nothing about the agents.", "",
           "| Period | Prices | Sessions | Stops hit | Of them gapped | Worst single trade | Worst drawdown | Mechanics |", "|---|---|---|---|---|---|---|---|"]
    for name, symbol, start, end, note in PERIODS:
        cs = candles_for(cat, symbol)
        r = replay(cs, start, end)
        by_day = {c.day: c for c in cs}
        stops = [t for t in r.trades if t.exit_reason == "stop"]
        gapped = sum(t.exit_day != t.entry_day and by_day[t.exit_day].open <= t.stop for t in stops)
        bad = violations(r, cs)
        out.append(f"| {name} | {note} | {len(r.equity)} | {len(stops)} | {gapped} | {min((t.ret for t in r.trades), default=0):+.1%} | "
                   f"{stats.curve(r.equity)['max_drawdown']:.1%} | {'pass' if not bad else 'FAIL: ' + '; '.join(bad)} |")
    return "\n".join(out) + "\n"
