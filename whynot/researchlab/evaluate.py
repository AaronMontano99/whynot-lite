"""Champion against challenger, on identical periods and identical assumptions.

Development first. Only a challenger that passes every screen there gets its one look
at validation. Passing validation makes it a candidate for the owner; it changes nothing."""

from ..sim import stats
from ..sim.agents import complexity, fingerprint
from .screens import Evidence, screen


def _evidence(agent, runner, start, end, neighbours=()) -> Evidence:
    main = runner(agent, start, end, 1.0)
    return Evidence(curve=stats.curve(main.equity), trades=main.trades, stats=stats.trades(main.trades),
                    cost2_sharpe=stats.curve(runner(agent, start, end, 2.0).equity)["sharpe"],
                    neighbour_sharpes=[stats.curve(runner(n, start, end, 1.0).equity)["sharpe"] for n in neighbours],
                    complexity=complexity(agent))


def _brief(e: Evidence) -> dict:
    t = e.stats
    return {"sharpe": round(e.curve["sharpe"], 3), "total_return": round(e.curve["total_return"], 4), "max_drawdown": round(e.curve["max_drawdown"], 4),
            "trades": t["count"], "hit_rate": round(t["hit_rate"], 3), "expectancy": round(t["expectancy"], 5), "t_stat": round(t["t_stat"], 2),
            "cost2_sharpe": round(e.cost2_sharpe, 3), "neighbour_sharpes": [round(s, 3) for s in e.neighbour_sharpes]}


def evaluate_challenger(db, cycle, champion, challenger, neighbours, runner, periods, hypothesis, change, motivation, new_evidence=None) -> dict:
    """runner(agent, start, end, cost_multiplier) -> a result with .equity and .trades."""
    fp = fingerprint(challenger)
    db.check_allowed(cycle, hypothesis, fp, new_evidence)               # refuse before any compute is spent
    left = db.budget_left(cycle)["experiments"]
    if 1 + len(neighbours) > left:
        raise RuntimeError(f"this experiment and its {len(neighbours)} nearby settings need more than the {left} experiments left in the cycle")
    if 1 + len(neighbours) > db.limits.max_configs:
        raise RuntimeError(f"one configuration and {len(neighbours)} nearby settings is more than the {db.limits.max_configs} a hypothesis may use")
    n_tested = db.count_tested() + 5 + 1 + len(neighbours)              # the five version-one strategies were tests too
    dev = periods["dev"]
    new, old = _evidence(challenger, runner, *dev, neighbours), _evidence(champion, runner, *dev)
    checks = screen(new, old, n_tested)
    failed = [c for c in checks if not c.passed]
    results = {"challenger": _brief(new), "champion": _brief(old), "period": [str(d) for d in dev],
               "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks]}
    validation, decision = None, "rejected"
    if failed:
        reason = "failed on development: " + "; ".join(f"{c.name} ({c.detail})" for c in failed)
    else:
        val = periods["val"]
        vn, vo = _evidence(challenger, runner, *val), _evidence(champion, runner, *val)
        validation = {"challenger": _brief(vn), "champion": _brief(vo), "period": [str(d) for d in val]}
        limit = max(vo.curve["max_drawdown"] * 1.25, vo.curve["max_drawdown"] + 0.02)
        problems = [text for ok, text in (
            (vn.stats["expectancy"] > 0, "it lost money on validation"),
            (vn.curve["sharpe"] > vo.curve["sharpe"], "it did not beat the champion on validation"),
            (vn.curve["max_drawdown"] <= limit, "its validation drawdown was too deep"),
            (vn.cost2_sharpe > 0, "it did not survive doubled costs on validation")) if not ok]
        if problems:
            reason = "passed development, failed validation: " + "; ".join(problems)
        else:
            decision, reason = "candidate", "passed every development screen and its one look at validation; waiting for the owner"
    for n in neighbours:                                                # nearby settings are experiments too, and count against the budget
        db.record_experiment(cycle, agent=challenger.id, hypothesis=hypothesis, change=f"nearby setting for {challenger.version}", fingerprint=fingerprint(n),
                             champion_version=champion.version, challenger_version=n.version, params={"neighbour_of": challenger.version},
                             dataset="DEVELOPMENT", dev_results={}, decision="sensitivity check", reason="run only to test how fragile the setting is",
                             motivation=motivation, new_evidence=new_evidence or "sensitivity check")
    exp = db.record_experiment(cycle, agent=challenger.id, hypothesis=hypothesis, change=change, fingerprint=fp, champion_version=champion.version,
                               challenger_version=challenger.version, params={"complexity": new.complexity}, dataset="DEVELOPMENT" + (" + VALIDATION" if validation else ""),
                               dev_results=results, decision=decision, reason=reason, motivation=motivation, validation_results=validation,
                               failure_analysis=None if decision == "candidate" else reason, new_evidence=new_evidence,
                               strategy={"fingerprint": fp, "version": challenger.version})
    return {"id": exp, "decision": decision, "reason": reason, "checks": checks, "development": results, "validation": validation}
