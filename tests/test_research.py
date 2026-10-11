"""Milestone 5: research memory, bounded research cycles, and champion against challenger.
The limits are enforced by code, the grading is done by code, and only the owner promotes."""

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from whynot.researchlab import failure, screens
from whynot.researchlab.memory import (BudgetExceeded, DuplicateExperiment, Limits, OWNER_PHRASE, ResearchDB, ReviewRequired)
from whynot.sim import stats
from whynot.sim.agents import AGENTS, RULES, complexity, fingerprint, variant


@pytest.fixture
def db(tmp_path):
    return ResearchDB(tmp_path / "w.db")


PLAN = dict(hypothesis="Trend following on QQQ does not need the CHOP size cut", motivation="the no-regime version made more",
            datasets="DEVELOPMENT 2006-2019, then VALIDATION 2020-2024 once", metrics="expectancy, Sharpe, drawdown, t against the adjusted bar",
            stopping="stop at the first config that fails a screen", max_seconds=600)


def open_cycle(db, direction="kip trend", agent="Kip", **kw):
    return db.open_cycle(direction=direction, agent=agent, **{**PLAN, **kw})


def record(db, cycle, n=0, decision="rejected", hypothesis=None, **kw):
    spec = dict(agent="Kip", hypothesis=hypothesis or PLAN["hypothesis"], change=f"config {n}", fingerprint=f"fp{n}", champion_version="kip-v1",
                challenger_version=f"kip-c{n}", params={"n": n}, dataset="DEVELOPMENT", dev_results={"sharpe": 0.1}, decision=decision, reason="test")
    spec.update(kw)
    return db.record_experiment(cycle, **spec)


# ---------------------------------------------------------------- bounded research

def test_a_cycle_cannot_open_without_a_complete_plan(db):
    for missing in ("hypothesis", "datasets", "metrics", "stopping", "max_seconds"):
        with pytest.raises(ValueError, match=missing):
            open_cycle(db, **{missing: None})
    cycle = open_cycle(db)
    row = db.cycle(cycle)
    assert row["status"] == "open" and row["max_experiments"] == 20 and row["max_configs"] == 5


def test_the_experiment_and_configuration_budgets_are_hard_stops(db):
    cycle = open_cycle(db)
    for n in range(5):
        record(db, cycle, n)
    with pytest.raises(BudgetExceeded, match="5 configurations"):
        record(db, cycle, 5)                                                       # a sixth setting of the same hypothesis
    for n in range(5, 20):
        record(db, cycle, n, hypothesis=f"other idea {n // 5}")
    with pytest.raises(BudgetExceeded, match="20 experiments"):
        record(db, cycle, 99, hypothesis="one more idea")
    assert len(db.experiments(cycle=cycle)) == 20                                  # every attempt is on record, none beyond the budget


def test_the_time_budget_is_a_hard_stop(db):
    cycle = open_cycle(db, max_seconds=60)
    db.db.execute("UPDATE cycle SET opened_at = opened_at - 61 WHERE id=?", (cycle,))
    with pytest.raises(BudgetExceeded, match="time"):
        record(db, cycle, 1)


def test_limits_cannot_be_raised_quietly(db):
    with pytest.raises(FrozenInstanceError):
        db.limits.max_experiments = 500
    with pytest.raises(PermissionError):
        db.set_limits(Limits(max_experiments=500), phrase="go on then")
    db.set_limits(Limits(max_experiments=30), phrase=OWNER_PHRASE)
    assert db.limits.max_experiments == 30 and db.events()[-1]["kind"] == "limits changed"
    assert ResearchDB(db.path).limits.max_experiments == 30                        # and it is the stored value, not a default


def test_three_failed_cycles_in_a_row_need_the_owner_before_a_fourth(db):
    for _ in range(3):
        cycle = open_cycle(db)
        record(db, cycle, _)
        db.close_cycle(cycle)                                                      # nothing passed
        assert db.cycle(cycle)["outcome"] == "no challenger passed"
    with pytest.raises(ReviewRequired):
        open_cycle(db)
    open_cycle(db, direction="nemo reversion", agent="Nemo")                       # another direction is unaffected
    with pytest.raises(PermissionError):
        db.owner_review("kip trend", phrase="looks fine", note="x")
    db.owner_review("kip trend", phrase=OWNER_PHRASE, note="carry on, but only with new data")
    open_cycle(db)


def test_a_failed_experiment_is_not_repeated_without_new_evidence(db):
    cycle = open_cycle(db)
    record(db, cycle, 1, fingerprint="same")
    with pytest.raises(DuplicateExperiment):
        record(db, cycle, 2, fingerprint="same")
    record(db, cycle, 3, fingerprint="same", new_evidence="two more years of data since the first try")
    assert [e["challenger_version"] for e in db.search("CHOP size cut")] == ["kip-c1", "kip-c3"]
    assert db.search("nothing like this") == []


# ---------------------------------------------------------------- champion and challenger

def test_champions_start_provisional_and_only_the_owner_promotes(db):
    db.seed_champions(AGENTS)
    champ = db.champion("Kip")
    assert champ["version"] == "kip-v1" and champ["status"] == "provisional" and champ["approved_by"] is None
    cycle = open_cycle(db)
    failed = record(db, cycle, 1)
    passed = record(db, cycle, 2, decision="candidate", strategy={"x": 1})
    with pytest.raises(ValueError):
        db.promote(failed, phrase=OWNER_PHRASE)                                    # only a candidate can be promoted
    with pytest.raises(PermissionError):
        db.promote(passed, phrase="approved by Claude")
    assert db.champion("Kip")["version"] == "kip-v1" and [q["status"] for q in db.approval_queue()] == ["pending"]
    db.promote(passed, phrase=OWNER_PHRASE)
    champ = db.champion("Kip")
    assert champ["version"] == "kip-c2" and champ["status"] == "approved" and champ["approved_by"] == "owner"
    assert [h["version"] for h in db.champion_history("Kip")] == ["kip-v1", "kip-c2"]
    assert db.experiment(passed)["decision"] == "promoted" and db.approval_queue() == []


def test_the_owner_can_decline_and_the_champion_stays(db):
    db.seed_champions(AGENTS)
    cycle = open_cycle(db)
    passed = record(db, cycle, 2, decision="candidate", strategy={"x": 1})
    db.decline(passed, phrase=OWNER_PHRASE, note="not convinced")
    assert db.champion("Kip")["version"] == "kip-v1" and db.experiment(passed)["decision"] == "declined"


def test_the_holdout_opens_only_for_the_owner_and_every_look_is_logged(db):
    with pytest.raises(PermissionError):
        db.log_holdout("Kip", "kip-c2", phrase="just a peek", result={})
    assert not db.holdout_contaminated("Kip")
    db.log_holdout("Kip", "kip-c2", phrase=OWNER_PHRASE, result={"sharpe": 0.3})
    assert len(db.holdout_looks("Kip")) == 1 and db.holdout_contaminated("Kip")     # it can never again be an independent test for Kip
    assert not db.holdout_contaminated("Nemo")


def test_a_variant_is_a_new_strategy_and_the_original_is_untouched():
    kip = AGENTS["Kip"]
    v2 = variant(kip, "kip-c1", {"CHOP": {"max_size": 1.0}, "*": {"params": {"avg": 100}}})
    assert v2.version == "kip-c1" and v2.playbooks["CHOP"].max_size == 1.0 and v2.playbooks["CALM_UP"].params == {"avg": 100}
    assert kip.playbooks["CHOP"].max_size == 0.5 and kip.playbooks["CALM_UP"].params == {} and AGENTS["Kip"] is kip
    assert v2.playbooks["CRASH"] is None                                           # crash stays flat whatever the variant says
    assert fingerprint(v2) != fingerprint(kip) and fingerprint(variant(kip, "other-name", {})) == fingerprint(kip)
    assert complexity(v2) > complexity(kip)
    with pytest.raises(ValueError):
        variant(kip, "bad", {"CRASH": {"rule": "trend_long"}})


def test_rule_settings_change_behaviour_and_the_defaults_are_version_one():
    entry, _ = RULES["trend_long"]
    closes = [100.0] * 40 + [100 + 0.1 * i for i in range(40)] + [103.4]            # a steady rise, then a small dip
    ctx = lambda **params: SimpleNamespace(closes=closes, highs=closes, lows=closes, rec=None, prev_rec=None, pos=None, params=params)
    assert entry(ctx())[0] == "LONG"
    assert entry(ctx(lookback=5)) is None                                          # lower than five days ago


# ---------------------------------------------------------------- the screens (code grades; the model never does)

def trade(ret, year=2010, state="CALM_UP"):
    return SimpleNamespace(ret=ret, pnl=ret * 10_000, state=state, entry_day=date(year, 6, 1))


def evidence(trades, sharpe=1.0, dd=0.10, cost2=0.8, neighbours=(0.9, 0.9), comp=3):
    return screens.Evidence(curve={"sharpe": sharpe, "max_drawdown": dd, "total_return": sum(t.ret for t in trades)}, trades=trades,
                            stats=stats.trades(trades), cost2_sharpe=cost2, neighbour_sharpes=list(neighbours), complexity=comp)


def good_trades():
    out = []
    for year in range(2008, 2018):
        for state in ("CALM_UP", "CHOP", "STRESS"):
            out += [trade(0.012, year, state), trade(0.02, year, state), trade(-0.008, year, state), trade(0.015, year, state)]
    return out


CHAMP = evidence([trade(0.002, y) for y in range(2008, 2018)] * 4, sharpe=0.4, dd=0.12)


def failed(checks):
    return [c.name for c in checks if not c.passed]


def test_a_robust_challenger_passes_every_screen():
    assert failed(screens.screen(evidence(good_trades()), CHAMP, n_tested=5)) == []


def test_each_way_of_fooling_yourself_is_caught():
    one_trade = evidence([trade(0.0, 2008 + i % 10, ("CALM_UP", "CHOP")[i % 2]) for i in range(60)] + [trade(0.9)])
    assert "survives without its best trade" in failed(screens.screen(one_trade, CHAMP, 5))
    one_year = evidence([trade(-0.001, y) for y in range(2008, 2018) for _ in range(6)] + [trade(0.05, 2013)] * 6)
    assert "survives without its best year" in failed(screens.screen(one_year, CHAMP, 5))
    one_state = evidence([trade(0.03, y, "CHOP") for y in range(2008, 2018)] * 3 + [trade(-0.004, y, "CALM_UP") for y in range(2008, 2018)] * 3)
    assert "works in more than one regime" in failed(screens.screen(one_state, CHAMP, 5))
    assert "survives doubled costs" in failed(screens.screen(evidence(good_trades(), cost2=-0.1), CHAMP, 5))
    assert "not a narrow optimum" in failed(screens.screen(evidence(good_trades(), neighbours=(0.9, 0.1)), CHAMP, 5))
    assert "no added tail risk" in failed(screens.screen(evidence(good_trades(), dd=0.30), CHAMP, 5))
    assert "enough independent trades" in failed(screens.screen(evidence(good_trades()[:12]), CHAMP, 5))
    assert "beats the champion" in failed(screens.screen(evidence(good_trades(), sharpe=0.3), CHAMP, 5))


def test_the_bar_rises_with_every_strategy_tried_and_ties_go_to_the_simpler_one():
    assert screens.t_needed(1) == pytest.approx(1.96, abs=0.01) and screens.t_needed(5) == pytest.approx(2.58, abs=0.01)
    assert screens.t_needed(50) > screens.t_needed(5)
    weak = evidence([trade(0.02 if i % 2 else -0.012, 2008 + i % 10, ("CALM_UP", "CHOP")[i % 2]) for i in range(60)])
    assert "t-statistic clears the adjusted bar" in failed(screens.screen(weak, CHAMP, n_tested=40))
    similar = evidence(good_trades(), sharpe=0.45, comp=9)
    simple_champ = evidence(CHAMP.trades, sharpe=0.4, dd=0.12, comp=3)
    assert "simpler or clearly better" in failed(screens.screen(similar, simple_champ, 5))


# ---------------------------------------------------------------- failure analysis

def loss(**kw):
    row = dict(ret=-0.02, pnl=-400.0, mfe=0.001, mae=-0.02, exit_reason="stop", days_held=3, costs=4.0, state="CALM_UP", flags="[]",
               state_at_exit="CALM_UP", account=20_000.0, gapped=False)
    row.update(kw)
    return row


def test_a_loss_gets_one_cause_from_the_list_and_ordinary_losses_are_ordinary():
    assert failure.classify(loss()) == "Normal statistical loss"
    assert failure.classify(loss(mfe=0.05)) == "Poor exit timing"                  # it was well up before it lost
    assert failure.classify(loss(state_at_exit="STRESS")) == "Regime transition"
    assert failure.classify(loss(gapped=True)) == "News or event shock"
    assert failure.classify(loss(costs=300.0)) == "Liquidity and slippage"
    assert failure.classify(loss(pnl=-900.0, ret=-0.045)) == "Excessive position size"
    assert failure.classify(loss(flags='["stale marks: 3 days"]')) == "Stale or incorrect data"
    assert all(failure.classify(loss(**kw)) in failure.CAUSES for kw in ({}, {"mfe": 0.05}, {"gapped": True}))


def test_a_loss_becomes_a_lesson_never_a_trading_rule(db):
    db.seed_champions(AGENTS)
    db.add_lesson("Kip", cause="Poor exit timing", text="Gave back a 5% gain", trade_ref="run 1 trade 7")
    assert db.lessons("Kip")[0]["kind"] == "hypothesis"
    assert db.champion("Kip")["version"] == "kip-v1"                               # the champion is not touched by a loss
    assert failure.enough_evidence([{"cause": "Poor exit timing"}] * 5) == []       # five losses are not a pattern
    many = [{"cause": "Poor exit timing"}] * 40 + [{"cause": "Normal statistical loss"}] * 20
    assert failure.enough_evidence(many) == ["Poor exit timing"]


# ---------------------------------------------------------------- the evaluation harness

def fake_result(trades):
    value, curve = 20_000.0, []
    for i, t in enumerate(trades):
        value *= 1 + t.ret
        curve.append((date(2008, 1, 1) + timedelta(days=i * 7), value))
    return SimpleNamespace(equity=curve, trades=trades)


def test_both_sides_run_on_identical_terms_and_validation_opens_only_after_a_pass(db):
    from whynot.researchlab.evaluate import evaluate_challenger
    db.seed_champions(AGENTS)
    kip = AGENTS["Kip"]
    calls = []

    def runner(agent, start, end, cost_mult):
        calls.append((agent.version, start, end, cost_mult))
        weak = agent.version in ("kip-v1", "kip-bad")
        return fake_result([trade(0.002, y) for y in range(2008, 2018)] * 4 if weak else good_trades())

    periods = {"dev": (date(2006, 1, 3), date(2019, 12, 31)), "val": (date(2020, 1, 1), date(2024, 12, 31))}
    cycle = open_cycle(db)
    bad = variant(kip, "kip-bad", {"CHOP": {"max_size": 0.9}})
    out = evaluate_challenger(db, cycle, kip, bad, [], runner, periods, hypothesis="h1", change="c", motivation="m")
    assert out["decision"] == "rejected" and "beats the champion" in out["reason"]
    assert not [c for c in calls if c[1] == periods["val"][0]]                      # validation was never opened for a loser
    calls.clear()
    good = variant(kip, "kip-c1", {"CHOP": {"max_size": 1.0}})
    near = [variant(kip, "kip-c1a", {"CHOP": {"max_size": 1.0}, "*": {"params": {"avg": 40}}}),
            variant(kip, "kip-c1b", {"CHOP": {"max_size": 1.0}, "*": {"params": {"avg": 60}}})]
    out = evaluate_challenger(db, cycle, kip, good, near, runner, periods, hypothesis="h2", change="c", motivation="m")
    dev = [c for c in calls if c[1] == periods["dev"][0]]
    assert ("kip-c1", *periods["dev"], 1.0) in dev and ("kip-v1", *periods["dev"], 1.0) in dev      # same dates, same costs
    assert ("kip-c1", *periods["dev"], 2.0) in dev and {"kip-c1a", "kip-c1b"} <= {c[0] for c in dev}
    assert ("kip-c1", *periods["val"], 1.0) in calls and ("kip-v1", *periods["val"], 1.0) in calls
    assert out["decision"] == "candidate" and db.champion("Kip")["version"] == "kip-v1"             # passing is not promotion
    assert len(db.approval_queue()) == 1 and len(db.experiments(cycle=cycle)) == 4                   # the bad one, the good one, two neighbours
    assert db.experiment(out["id"])["validation_results"] and AGENTS["Kip"] is kip
