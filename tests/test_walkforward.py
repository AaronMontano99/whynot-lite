"""Milestone 2: the regime detector walked forward through time, point in time."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from whynot import regime, walkforward
from whynot.ingest.catalog import Catalog
from whynot.ingest.timeutil import epoch
from whynot.partitions import partition_of
from whynot.regime import Candle, Model

ET = ZoneInfo("America/New_York")
START = date(2010, 1, 1)


def candles(n=1100, seed=3):
    rng = np.random.default_rng(seed)
    out, price = [], 100.0
    for i in range(n):
        vol = 0.004 if (i // 90) % 2 == 0 else 0.02          # calm and wild stretches
        o = price
        price = o * float(np.exp(rng.normal(0.0003, vol)))
        hi, lo = max(o, price) * (1 + vol / 2), min(o, price) * (1 - vol / 2)
        out.append(Candle(START + timedelta(days=i), o, hi, lo, price, float(rng.uniform(1e6, 2e6))))
    return out


FIRST_FIT = START + timedelta(days=909)                      # 800 observation rows behind it


@pytest.fixture(scope="module")
def quick():
    mp = pytest.MonkeyPatch()
    mp.setattr(regime, "RESTARTS", 2)                        # fewer restarts: the tests check plumbing, not fit quality
    yield
    mp.undo()


@pytest.fixture(scope="module")
def full(quick):
    return walkforward.walk(candles(), FIRST_FIT)


def test_step_by_step_filter_equals_the_whole_filter():
    _, z = regime.observations(candles(400))
    model = Model(startprob=np.array([0.5, 0.5]), transmat=np.array([[0.95, 0.05], [0.10, 0.90]]),
                  means=np.array([[0.0] * 5, [0.0, 2.0, 2.0, 0.0, 0.0]]), covars=np.array([np.eye(5), np.eye(5)]),
                  labels=["CALM_UP", "STRESS"])
    whole, ll = regime.forward(model, z)
    alpha = None
    for t in range(len(z)):
        alpha, l = regime.step(model, alpha, z[t])
        assert np.allclose(alpha, whole[t], atol=1e-12) and abs(l - ll[t]) < 1e-9
    assert model.durations() == pytest.approx([1 / (1 - 0.95), 1 / (1 - 0.90)])     # expected stay = 1 / (1 - self-transition)


def test_trend_feature_is_distance_from_the_50_candle_average():
    cs = candles(120)
    days, x = regime.raw_features(cs)
    closes = [c.close for c in cs]
    assert days[0] == cs[50].day
    assert x[0][4] == pytest.approx(closes[50] / np.mean(closes[1:51]) - 1)


def test_the_first_fit_needs_three_years(quick):
    with pytest.raises(ValueError, match="3 years"):
        walkforward.walk(candles(700), START + timedelta(days=600))


def test_every_day_is_judged_by_a_model_fitted_strictly_before_it(full):
    result = full
    assert len(result.records) == 1100 - 910
    through = {m.version: m.through for m in result.models}
    assert all(through[r.version] < r.day for r in result.records)
    assert result.records[0].day == FIRST_FIT + timedelta(days=1)


def test_the_past_does_not_change_when_the_future_does(full, quick):
    cs = candles()
    cut = 1000
    truncated = walkforward.walk(cs[:cut], FIRST_FIT)
    scrambled = walkforward.walk(cs[:cut] + [Candle(c.day, c.open * 3, c.high * 9, c.low / 2, c.close * 5, c.volume * 7)
                                             for c in cs[cut:]], FIRST_FIT)
    known = [r for r in full.records if r.day < cs[cut].day]
    assert len(known) == len(truncated.records) > 80
    for a, b, c in zip(known, truncated.records, scrambled.records):
        assert (a.day, a.version, a.probs, a.next_probs, a.active, a.size) == (b.day, b.version, b.probs, b.next_probs, b.active, b.size)
        assert (a.probs, a.active, a.size, a.reason) == (c.probs, c.active, c.size, c.reason)
    assert any(a.probs != c.probs for a, c in zip(full.records[len(known):], scrambled.records[len(known):]))


def test_refits_come_every_30_days_and_labels_keep_their_meaning(full):
    fitted = [m.through for m in full.models]
    assert fitted[0] == FIRST_FIT and len(fitted) == 1 + (1100 - 911) // 30
    assert all((b - a).days == 30 for a, b in zip(fitted, fitted[1:]))
    assert all(m.labels == full.models[0].labels for m in full.models)
    assert [m.version for m in full.models] == list(range(1, len(fitted) + 1))


def test_probabilities_are_proper_and_carry_next_state_and_duration(full):
    for r in full.records:
        assert sum(r.probs.values()) == pytest.approx(1.0) and sum(r.next_probs.values()) == pytest.approx(1.0)
        assert 0.0 <= r.entropy <= 1.0 and r.size in (0.0, 0.5, 1.0)
        assert r.expected_days is None or r.expected_days >= 1.0


def test_a_rolling_window_never_refits_on_more_than_its_window(quick, monkeypatch):
    seen, real = [], regime.refit
    monkeypatch.setattr(regime, "refit", lambda old, days, z: (seen.append(len(z)), real(old, days, z))[1])
    walkforward.walk(candles(), FIRST_FIT, window=500)
    assert seen and max(seen) == 500


def test_drift_freezes_new_entries_until_a_clean_refit(quick, monkeypatch):
    real, calls = regime.refit, []

    def drifting(old, days, z):
        model, _ = real(old, days, z)
        calls.append(1)
        return model, (["state means moved 0.90 (tolerance 0.5)"] if len(calls) == 1 else [])

    monkeypatch.setattr(regime, "refit", drifting)
    result = walkforward.walk(candles(), FIRST_FIT)
    second, third = result.models[1], result.models[2]
    frozen = [r for r in result.records if r.version == second.version]
    assert frozen and all(r.frozen and r.size == 0.0 and "state means moved" in r.reason for r in frozen)
    assert not any("state means moved" in r.reason for r in result.records if r.version == third.version)
    assert second.report["drift"] and not third.report["drift"]


def test_candles_come_from_the_catalog_as_of_a_moment(tmp_path):
    cat = Catalog(tmp_path / "w.db")
    ds = cat.dataset(category="ETF_HISTORY", symbol="SPY", timeframe="1Day", source="yahoo", authentic=True, adjustment="split_adjusted")
    rows = []
    for i in range(5):
        d = date(2024, 3, 4) + timedelta(days=i)
        ts, close = epoch(datetime(d.year, d.month, d.day, tzinfo=ET)), epoch(datetime(d.year, d.month, d.day, 16, tzinfo=ET))
        rows.append((ts, 100 + i, 101 + i, 99 + i, 100.5 + i, 1e6, None, None, close))
    cat.add_bars(ds, rows)
    midweek = epoch(datetime(2024, 3, 6, 15, 59, tzinfo=ET))                     # a minute before Wednesday's close
    got = walkforward.candles_from_catalog(cat, "SPY", as_of=midweek)
    assert [c.day for c in got] == [date(2024, 3, 4), date(2024, 3, 5)]
    assert len(walkforward.candles_from_catalog(cat, "SPY")) == 5
    with pytest.raises(LookupError):
        walkforward.candles_from_catalog(cat, "QQQ")


def test_stored_history_is_never_rewritten(full, tmp_path):
    store = walkforward.RegimeStore(tmp_path / "w.db")
    store.save("SPY", "expanding", full)
    first = store.days("SPY", "expanding")
    assert len(first) == len(full.records) and first[0]["probs"] == full.records[0].probs
    changed = walkforward.Result(full.models, [walkforward.Record(**{**vars(full.records[0]), "active": "CRASH", "size": 0.0})])
    store.save("SPY", "expanding", changed)
    again = store.days("SPY", "expanding")
    assert again[0]["active"] == full.records[0].active and len(again) == len(first)
    model = store.model("SPY", "expanding", full.records[-1].version)
    assert np.allclose(model.transmat, full.models[-1].transmat) and model.labels == full.models[-1].labels


def test_partitions_are_chronological_and_complete():
    assert [partition_of(d) for d in (date(1993, 2, 1), date(2019, 12, 31), date(2020, 1, 1), date(2024, 12, 31),
                                      date(2025, 1, 1), date(2026, 10, 7), date(2026, 10, 8))] == [
        "DEVELOPMENT", "DEVELOPMENT", "VALIDATION", "VALIDATION", "LOCKED_HOLDOUT", "LOCKED_HOLDOUT", "LIVE_PAPER"]


def _three(vols):
    return Model(startprob=np.array([0.2, 0.3, 0.5]),
                 transmat=np.array([[0.90, 0.07, 0.03], [0.10, 0.80, 0.10], [0.05, 0.15, 0.80]]),
                 means=np.array([[0.1, vols[0], 0, 0, 0], [0.2, vols[1], 0, 0, 0], [0.3, vols[2], 0, 0, 0]]),
                 covars=np.array([np.eye(5) * k for k in (1, 2, 3)]), labels=["CALM_UP", "CHOP", "STRESS"])


def test_a_label_always_means_its_volatility_rank():
    """IWM showed why: after years of refits the state still called CALM had become the
    second most volatile. A label must follow the statistics, and the change must be loud."""
    same, changed = regime.rank_by_volatility(_three([-1.0, 0.0, 1.5]))
    assert not changed and same.means[:, 1].tolist() == [-1.0, 0.0, 1.5]
    tie, changed = regime.rank_by_volatility(_three([-1.0, 0.1, 0.0]))                 # out of order by a hair: leave it
    assert not changed and tie.means[:, 1].tolist() == [-1.0, 0.1, 0.0]
    fixed, changed = regime.rank_by_volatility(_three([1.5, -1.0, 0.0]))               # the "calm" state is now the wildest
    assert changed and fixed.labels == ["CALM_UP", "CHOP", "STRESS"]
    assert fixed.means[:, 1].tolist() == [-1.0, 0.0, 1.5] and fixed.means[:, 0].tolist() == [0.2, 0.3, 0.1]
    assert fixed.startprob.tolist() == [0.3, 0.5, 0.2]
    assert fixed.transmat[2].tolist() == [0.07, 0.03, 0.90]                            # old state 0 is now state 2
    assert [c[0, 0] for c in fixed.covars] == [2, 3, 1]
    assert np.allclose(fixed.transmat.sum(axis=1), 1.0)


def test_a_re_ranking_is_drift_and_makes_states_earn_their_place_again(quick, monkeypatch):
    real, calls = regime.rank_by_volatility, []

    def once(model):
        calls.append(1)
        return (model, True) if len(calls) == 1 else real(model)

    monkeypatch.setattr(regime, "rank_by_volatility", once)
    result = walkforward.walk(candles(), FIRST_FIT)
    second = [r for r in result.records if r.version == 2]
    assert all(r.frozen and "re-ranked" in r.reason for r in second)
    assert second[0].active is None                                                    # the old active label is not trusted
    assert any("re-ranked" in d for d in result.models[1].report["drift"])
