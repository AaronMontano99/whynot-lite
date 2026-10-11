import numpy as np
import pytest
from datetime import date, timedelta

from whynot import regime, sizing
from whynot.regime import Candle, Model
from whynot.switching import Switcher

LABELS = ["CALM_UP", "CHOP", "STRESS", "CRASH"]


def candles(n=400, seed=3):
    rng = np.random.default_rng(seed)
    out, price, day = [], 100.0, date(2020, 1, 1)
    for i in range(n):
        vol = 0.004 if (i // 80) % 2 == 0 else 0.02          # calm and wild stretches
        o = price
        price = o * float(np.exp(rng.normal(0.0003, vol)))
        hi, lo = max(o, price) * (1 + vol / 2), min(o, price) * (1 - vol / 2)
        out.append(Candle(day + timedelta(days=i), o, hi, lo, price, float(rng.uniform(1e6, 2e6))))
    return out


def toy_model():
    return Model(startprob=np.array([0.5, 0.5]),
                 transmat=np.array([[0.95, 0.05], [0.10, 0.90]]),
                 means=np.array([[0.0] * 5, [0.0, 2.0, 2.0, 0.0, 0.0]]),
                 covars=np.array([np.eye(5), np.eye(5)]), labels=["CALM_UP", "STRESS"])


# ---- no lookahead ----------------------------------------------------------

def test_features_never_use_a_later_candle():
    cs = candles()
    days, z = regime.observations(cs)
    cut = len(cs) - 50
    changed = cs[:cut] + [Candle(c.day, c.open * 3, c.high * 9, c.low / 2, c.close * 5, c.volume * 7)
                          for c in cs[cut:]]
    days2, z2 = regime.observations(changed)
    known = sum(d < cs[cut].day for d in days)
    assert known > 100 and days[:known] == days2[:known]
    assert np.array_equal(z[:known], z2[:known])             # the past is untouched
    assert not np.allclose(z[known:], z2[known:])


def test_filtered_probability_ignores_the_future_and_smoothing_does_not():
    _, z = regime.observations(candles())
    model = toy_model()
    full, _ = regime.forward(model, z)
    for k in (1, 57, 200):
        part, _ = regime.forward(model, z[:k])
        assert np.array_equal(part, full[:k])                # no decision touches a future candle
    garbage = z.copy()
    garbage[200:] = 50.0
    assert np.array_equal(regime.forward(model, garbage)[0][:200], full[:200])

    from hmmlearn.hmm import GaussianHMM
    hmm = GaussianHMM(2, covariance_type="full")
    hmm.startprob_, hmm.transmat_ = model.startprob, model.transmat
    hmm.means_, hmm.covars_ = model.means, model.covars
    assert np.allclose(hmm.predict_proba(z)[-1], full[-1], atol=1e-8)   # same maths at the last row
    assert not np.allclose(hmm.predict_proba(z)[:200], hmm.predict_proba(garbage)[:200])  # smoothing peeks


def test_model_fits_labels_by_volatility_and_refits_stably():
    days, z = regime.observations(candles(700))
    model = regime.select_and_fit(days[:500], z[:500])
    assert 2 <= model.n <= 5 and model.labels == regime.LADDERS[model.n]
    assert list(np.argsort(model.means[:, 1])) == list(range(model.n))
    assert np.allclose(model.transmat.sum(axis=1), 1.0)
    assert all(d >= 1.0 for d in model.durations())
    again = regime.select_and_fit(days[:500], z[:500])
    assert np.allclose(again.means, model.means)             # fixed seed: same model every time
    newer, drift = regime.refit(model, days[:560], z[:560])
    assert newer.labels == model.labels and isinstance(drift, list)
    assert np.abs(newer.means[:, 1] - model.means[:, 1]).max() < 1.0


# ---- the six switching rules ----------------------------------------------

def step(sw, probs, nxt=None):
    return sw.step(LABELS, probs, nxt or probs)


CALM, CHOP = [0.90, 0.06, 0.03, 0.01], [0.05, 0.90, 0.04, 0.01]


def settled():
    sw = Switcher()
    for _ in range(3):
        d = step(sw, CALM)
    assert d.state == "CALM_UP" and d.size == 1.0
    sw.cooldown = 0
    return sw


def test_rule_1_a_new_state_needs_more_than_070():
    sw = settled()
    for _ in range(10):
        d = step(sw, [0.20, 0.65, 0.10, 0.05])
    assert d.state == "CALM_UP" and not d.switched


def test_rule_2_it_must_hold_for_three_consecutive_candles():
    sw = settled()
    assert step(sw, CHOP).state == "CALM_UP"
    assert step(sw, CHOP).state == "CALM_UP"
    step(sw, CALM)                                           # the lead is broken
    assert step(sw, CHOP).state == "CALM_UP"
    assert step(sw, CHOP).state == "CALM_UP"
    d = step(sw, CHOP)
    assert d.state == "CHOP" and d.switched and "switch CALM_UP -> CHOP" in d.reason


def test_rule_3_cooldown_after_a_switch():
    sw = Switcher()
    for _ in range(3):
        d = step(sw, CHOP)
    assert d.switched and sw.cooldown == 3
    states = [step(sw, CALM).state for _ in range(3)]
    assert states == ["CHOP", "CHOP", "CHOP"]                # qualified, but still cooling down
    assert step(sw, CALM).state == "CALM_UP"


def test_rule_4_early_cut_to_half_when_stress_or_crash_is_coming():
    sw = settled()
    d = step(sw, CALM, [0.70, 0.04, 0.20, 0.06])
    assert d.state == "CALM_UP" and d.size == 0.5 and "cut to half" in d.reason
    assert step(sw, CALM, [0.70, 0.06, 0.20, 0.04]).size == 1.0     # 0.24 is under the line


def test_rule_5_uncertain_means_size_zero():
    sw = settled()
    d = step(sw, [0.48, 0.40, 0.08, 0.04])
    assert d.state == "CALM_UP" and d.size == 0.0 and "uncertain" in d.reason


def test_rule_6_errors_and_stale_data_go_flat():
    sw = settled()
    d = sw.flat("daily data is stale")
    assert d.flat and d.reason == "daily data is stale" and sw.active == "CALM_UP"


def test_crash_is_flat_and_stress_is_reduced():
    sw = Switcher()
    for _ in range(3):
        d = step(sw, [0.02, 0.03, 0.90, 0.05])
    assert d.state == "STRESS" and d.size == 0.5
    sw.cooldown = 0
    for _ in range(3):
        d = step(sw, [0.01, 0.01, 0.03, 0.95])
    assert d.state == "CRASH" and d.flat
    assert Switcher().step(LABELS, CALM, CALM).flat          # nothing is active until it is earned


# ---- sizing ----------------------------------------------------------------

def test_sizing_is_quarter_kelly_scaled_by_confidence_and_zero_without_edge():
    good = {"kelly": 0.40}
    f, _ = sizing.fraction(good, 0.9, 0.2, 1.0, cap=0.10, verified=True)
    assert f == pytest.approx(0.25 * 0.40 * 0.9 * 0.8)
    assert sizing.fraction(good, 0.9, 0.2, 0.5, 0.10, True)[0] == pytest.approx(f / 2)
    assert sizing.fraction(good, 0.9, 0.6, 1.0, 0.10, True)[0] < f          # less sure, smaller
    assert sizing.fraction(good, 0.9, 0.2, 0.0, 0.10, True)[0] == 0.0       # crash or uncertain
    assert sizing.fraction({"kelly": 0.0}, 0.9, 0.2, 1.0, 0.10, True)[0] == 0.0
    assert sizing.fraction({"kelly": 5.0}, 1.0, 0.0, 1.0, 0.10, True)[0] == 0.10   # the cap wins
    assert sizing.fraction(good, 0.9, 0.2, 1.0, 0.10, False)[0] == sizing.PROBE     # unverified


def test_kelly_and_calibration():
    assert sizing.kelly(0.5, 1.0, 1.0) == 0.0
    assert sizing.kelly(0.6, 1.0, 1.0) == pytest.approx(0.2)
    assert sizing.kelly(0.4, 0.5, 0.3) > 0
    trades = [{"pnl": 10, "entry_price": 1.0, "contracts": 1}] * 6 + \
             [{"pnl": -10, "entry_price": 1.0, "contracts": 1}] * 4
    e = sizing.edge(trades)
    assert e["win_rate"] == 0.6 and e["kelly"] == pytest.approx(0.6 / 0.1 - 0.4 / 0.1)
    fills = lambda p, won, lost: [(p, True)] * won + [(p, False)] * lost
    assert sizing.brier(fills(0.6, 6, 4)) == pytest.approx(0.24)
    assert not sizing.calibrated(fills(0.6, 6, 4))                         # too few fills
    assert sizing.calibrated(fills(0.6, 24, 16))
    assert not sizing.calibrated(fills(0.9, 20, 20))                       # badly calibrated
    assert sizing.reliability(fills(0.6, 24, 16)) == [{"predicted": "0.6-0.8", "fills": 40, "won": 0.6}]
    assert regime.entropy([1.0, 0.0]) == pytest.approx(0.0, abs=1e-9)
    assert regime.entropy([0.25] * 4) == pytest.approx(1.0)
