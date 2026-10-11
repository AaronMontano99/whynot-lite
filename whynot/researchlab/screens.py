"""The screens a challenger must pass. Plain arithmetic on its trades and account curve;
the model that proposed the strategy has no say in the result."""

from dataclasses import dataclass
from statistics import NormalDist

MIN_TRADES = 30
CLEARLY_BETTER = 0.10           # a Sharpe improvement smaller than this is "similar": then the simpler strategy wins
NEIGHBOUR_KEEPS = 0.5           # a nearby setting must keep at least half the Sharpe


@dataclass
class Evidence:
    curve: dict                 # from stats.curve
    trades: list
    stats: dict                 # from stats.trades
    cost2_sharpe: float         # Sharpe with costs doubled
    neighbour_sharpes: list     # Sharpe at nearby settings
    complexity: int


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def t_needed(n_tested: int) -> float:
    """The t-statistic needed for 5% confidence after n strategies have been tried."""
    return NormalDist().inv_cdf(1 - 0.025 / max(n_tested, 1))


def _without_best(trades: list, key) -> float:
    groups = {}
    for t in trades:
        groups[key(t)] = groups.get(key(t), 0.0) + t.ret
    return sum(groups.values()) - max(groups.values()) if len(groups) > 1 else -1.0


def screen(new: Evidence, champion: Evidence, n_tested: int) -> list:
    rets, t, c = [x.ret for x in new.trades], new.stats, new.curve
    bar = t_needed(n_tested)
    edge = c["sharpe"] - champion.curve["sharpe"]
    limit = max(champion.curve["max_drawdown"] * 1.25, champion.curve["max_drawdown"] + 0.02)
    near = new.neighbour_sharpes
    return [
        Check("enough independent trades", t["count"] >= MIN_TRADES, f"{t['count']} trades (need {MIN_TRADES})"),
        Check("positive expectancy after costs", t["expectancy"] > 0, f"average trade {t['expectancy']:+.3%}"),
        Check("t-statistic clears the adjusted bar", t["t_stat"] >= bar, f"t {t['t_stat']:.2f} against {bar:.2f} after {n_tested} strategies tried"),
        Check("beats the champion", edge > 0, f"Sharpe {c['sharpe']:.2f} against the champion's {champion.curve['sharpe']:.2f}"),
        Check("simpler or clearly better", edge >= CLEARLY_BETTER or new.complexity <= champion.complexity,
              f"Sharpe gain {edge:+.2f}, complexity {new.complexity} against {champion.complexity}"),
        Check("no added tail risk", c["max_drawdown"] <= limit, f"worst drawdown {c['max_drawdown']:.1%} against a limit of {limit:.1%}"),
        Check("survives without its best trade", bool(rets) and sum(rets) - max(rets) > 0, "total of all trades but the best"),
        Check("survives without its best year", _without_best(new.trades, lambda x: x.entry_day.year) > 0, "total of all years but the best"),
        Check("works in more than one regime", _without_best(new.trades, lambda x: x.state) > 0, "total of all states but the best"),
        Check("survives doubled costs", new.cost2_sharpe > 0, f"Sharpe {new.cost2_sharpe:.2f} at double the costs"),
        Check("not a narrow optimum", bool(near) and all(s > 0 and s >= NEIGHBOUR_KEEPS * c["sharpe"] for s in near),
              "nearby settings: " + (", ".join(f"{s:.2f}" for s in near) or "none tested")),
    ]
