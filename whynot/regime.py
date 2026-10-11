"""Layer 2 of the owner's philosophy: the regime detector.

A Gaussian Hidden Markov Model reads daily SPY candles and says how likely each
hidden market state is. It only advises. What to do about a state is decided by
plain code in switching.py, sizing.py and risk.py.

No lookahead, in three places:
  - every feature for a candle uses that candle and earlier ones only;
  - features are standardized with the mean and spread of candles up to that one;
  - state probabilities are FORWARD-FILTERED (`forward` below): the probability for
    a candle uses candles up to it and nothing after. hmmlearn's own predict_proba
    and predict are smoothed/Viterbi, which read the future, and are never called.
A decision for trading day D uses candles dated strictly before D."""

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.stats import multivariate_normal


SEED = 7
RESTARTS = 8
LOOKBACK = 20                   # candles behind realized vol and volume ratio
TREND = 50                      # the trend feature: distance of the close from its 50-candle average
BURN_IN = 60                    # rows dropped while the expanding standardization settles
FEATURE_NAMES = ["log return", "realized volatility", "range", "volume ratio", "trend"]
# Labels are handed out by volatility, calmest first. What a label permits is in POLICY.
LADDERS = {2: ["CALM_UP", "STRESS"],
           3: ["CALM_UP", "CHOP", "STRESS"],
           4: ["CALM_UP", "CHOP", "STRESS", "CRASH"],
           5: ["CALM_UP", "CALM", "CHOP", "STRESS", "CRASH"]}
# state -> size multiplier. STRESS trades reduced, CRASH is flat. Code owns this.
POLICY = {"CALM_UP": 1.0, "CALM": 1.0, "CHOP": 1.0, "STRESS": 0.5, "CRASH": 0.0}
DANGER = ("STRESS", "CRASH")
CLOSE_BIC, CLOSE_OOS = 0.01, 0.01       # "scores are close": within 1%
RANK_MARGIN = 0.25                      # how far out of volatility order states may sit before labels are reassigned
TRANS_TOL, MEAN_TOL = 0.15, 0.5         # refit drift tolerances (probability, z-units); see docs/GUIDELINES.md


@dataclass(frozen=True)
class Candle:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


# ---- the state engine: five small numeric features --------------------------

def raw_features(candles: list) -> tuple:
    """(days, X). Row i describes candles[i + TREND] using it and earlier candles."""
    c = np.array([k.close for k in candles], dtype=float)
    h = np.array([k.high for k in candles], dtype=float)
    lo = np.array([k.low for k in candles], dtype=float)
    v = np.array([k.volume for k in candles], dtype=float)
    r = np.diff(np.log(c))                         # r[i] is the return INTO candle i + 1
    rows, days = [], []
    for t in range(TREND, len(candles)):
        window = r[t - LOOKBACK:t]                 # the 20 returns ending at candle t
        rows.append([
            r[t - 1],
            math.log(max(window.std(ddof=1), 1e-6)),
            math.log(h[t] / lo[t]) if h[t] > lo[t] else 0.0,
            math.log(v[t] / v[t - LOOKBACK:t].mean()),
            c[t] / c[t - TREND + 1:t + 1].mean() - 1.0,
        ])
        days.append(candles[t].day)
    return days, np.array(rows).reshape(-1, len(FEATURE_NAMES))


def standardize(X: np.ndarray) -> np.ndarray:
    """Expanding z-score: row t is scaled by the mean and spread of rows 0..t only."""
    n = np.arange(1, len(X) + 1)[:, None]
    mean = np.cumsum(X, axis=0) / n
    var = np.maximum(np.cumsum(X * X, axis=0) / n - mean * mean, 1e-12)
    return (X - mean) / np.sqrt(var)


def observations(candles: list) -> tuple:
    """(days, Z) ready for the model, burn-in removed."""
    days, X = raw_features(candles)
    if len(X) <= BURN_IN:
        return [], np.empty((0, len(FEATURE_NAMES)))
    return days[BURN_IN:], standardize(X)[BURN_IN:]


# ---- the model --------------------------------------------------------------

@dataclass
class Model:
    startprob: np.ndarray
    transmat: np.ndarray
    means: np.ndarray
    covars: np.ndarray
    labels: list
    through: date | None = None            # last candle used in the fit
    ll_floor: float = -math.inf            # live log-likelihood below this is a drift alarm
    report: dict = field(default_factory=dict)
    version: int = 0                       # its place in a walk-forward sequence of refits

    @property
    def n(self) -> int:
        return len(self.labels)

    def durations(self) -> list:
        """Expected stay in each state, in candles: 1 / (1 - self-transition)."""
        return [float(1.0 / max(1.0 - self.transmat[i, i], 1e-9)) for i in range(self.n)]

    def describe(self) -> list:
        return [{"state": self.labels[i], "mean_return_z": round(float(self.means[i, 0]), 3),
                 "volatility_z": round(float(self.means[i, 1]), 3),
                 "expected_days": round(self.durations()[i], 1),
                 "transitions": [round(float(p), 3) for p in self.transmat[i]]}
                for i in range(self.n)]


def _fit_once(Z: np.ndarray, n: int, seed: int, warm: Model | None = None):
    from hmmlearn.hmm import GaussianHMM

    if warm is None:
        hmm = GaussianHMM(n, covariance_type="full", n_iter=200, tol=1e-3, random_state=seed)
    else:
        hmm = GaussianHMM(n, covariance_type="full", n_iter=200, tol=1e-3, random_state=seed,
                          init_params="")
        hmm.startprob_, hmm.transmat_ = warm.startprob.copy(), warm.transmat.copy()
        hmm.means_, hmm.covars_ = warm.means.copy(), warm.covars.copy()
    hmm.fit(Z)
    return hmm


def _best(Z: np.ndarray, n: int, restarts: int | None = None):
    """Best of many restarts from fixed seeds."""
    restarts = restarts or RESTARTS
    best, best_ll = None, -math.inf
    for k in range(restarts):
        try:
            hmm = _fit_once(Z, n, SEED + k)
            ll = hmm.score(Z)
        except Exception:
            continue
        if np.isfinite(ll) and ll > best_ll and np.all(hmm.transmat_.sum(axis=1) > 0.99):
            best, best_ll = hmm, ll
    return best, best_ll


def _to_model(hmm, days: list) -> Model:
    order = np.argsort(hmm.means_[:, 1])                    # calmest state first
    model = Model(hmm.startprob_[order], hmm.transmat_[np.ix_(order, order)],
                  hmm.means_[order], hmm.covars_[order], list(LADDERS[hmm.n_components]),
                  through=days[-1] if days else None)
    return model


def _params(n: int, d: int) -> int:
    return (n - 1) + n * (n - 1) + n * d + n * d * (d + 1) // 2


def select_and_fit(days: list, Z: np.ndarray) -> Model:
    """Choose 2 to 5 states by BIC and out-of-sample log-likelihood, preferring the
    simpler model when the scores are close, then fit that model on everything."""
    cut = int(len(Z) * 0.8)
    table = {}
    for n in LADDERS:
        full, ll = _best(Z, n)
        head, _ = _best(Z[:cut], n, restarts=max(3, RESTARTS // 2))
        if full is None or head is None:
            continue
        oos = float(np.sum(forward(_to_model(head, days[:cut]), Z)[1][cut:])) / (len(Z) - cut)
        table[n] = {"bic": -2 * ll + _params(n, Z.shape[1]) * math.log(len(Z)),
                    "oos_ll_per_candle": oos, "hmm": full}
    if not table:
        raise RuntimeError("no HMM could be fitted")
    best_bic = min(t["bic"] for t in table.values())
    best_oos = max(t["oos_ll_per_candle"] for t in table.values())
    close = [n for n, t in sorted(table.items())
             if t["bic"] - best_bic <= CLOSE_BIC * abs(best_bic)
             and best_oos - t["oos_ll_per_candle"] <= CLOSE_OOS * abs(best_oos)]
    chosen = close[0] if close else min(table, key=lambda n: table[n]["bic"])
    model = _to_model(table[chosen]["hmm"], days)
    model.report = {"chosen": chosen, "candles": len(Z),
                    "selection": {n: {"bic": round(t["bic"], 1),
                                      "oos_ll_per_candle": round(t["oos_ll_per_candle"], 4)}
                                  for n, t in table.items()}}
    _set_floor(model, Z)
    return model


def refit(old: Model, days: list, Z: np.ndarray) -> tuple:
    """Walk-forward refit on the expanding window. Returns (model, drift reasons).
    The fit starts from the old model and new states are matched to old ones by
    their statistics, so a label keeps meaning the same thing."""
    hmm = _fit_once(Z, old.n, SEED, warm=old)
    cost = np.linalg.norm(hmm.means_[:, None, :] - old.means[None, :, :], axis=2)
    new_idx, old_idx = linear_sum_assignment(cost)
    order = new_idx[np.argsort(old_idx)]                    # position i = old state i
    model = Model(hmm.startprob_[order], hmm.transmat_[np.ix_(order, order)],
                  hmm.means_[order], hmm.covars_[order], list(old.labels), through=days[-1],
                  report=dict(old.report))
    drift = []
    shift_t = float(np.abs(model.transmat - old.transmat).max())
    shift_m = float(np.abs(model.means - old.means).max())
    if shift_t > TRANS_TOL:
        drift.append(f"transition matrix moved {shift_t:.2f} (tolerance {TRANS_TOL})")
    if shift_m > MEAN_TOL:
        drift.append(f"state means moved {shift_m:.2f} (tolerance {MEAN_TOL})")
    model, changed = rank_by_volatility(model)
    if changed:
        drift.append("states re-ranked by volatility: a label no longer matched its statistics, so labels were reassigned")
    _set_floor(model, Z)
    return model, drift


def rank_by_volatility(model: Model) -> tuple:
    """(model, changed). Labels are a volatility ladder, calmest first. Matching states to
    the old ones keeps a label on the same cluster, but over many refits a cluster can
    change character. When states sit out of volatility order by more than RANK_MARGIN,
    they are re-sorted so every label means its rank again. Near-ties are left alone."""
    vol = model.means[:, 1]
    if not any(vol[i] > vol[j] + RANK_MARGIN for i in range(model.n) for j in range(i + 1, model.n)):
        return model, False
    order = np.argsort(vol, kind="stable")
    return Model(model.startprob[order], model.transmat[np.ix_(order, order)], model.means[order], model.covars[order],
                 list(model.labels), through=model.through, ll_floor=model.ll_floor, report=model.report,
                 version=model.version), True


def _set_floor(model: Model, Z: np.ndarray):
    """The alarm level for live log-likelihood: the 1st percentile of the 20-candle
    average seen in the fit."""
    ll = forward(model, Z)[1]
    if len(ll) > LOOKBACK * 3:
        rolling = np.convolve(ll, np.ones(LOOKBACK) / LOOKBACK, mode="valid")
        model.ll_floor = float(np.percentile(rolling, 1))


# ---- forward filtering: the only probabilities any decision may use ---------

def _emission(model: Model, Z: np.ndarray) -> np.ndarray:
    return np.column_stack([multivariate_normal.logpdf(Z, model.means[i], model.covars[i],
                                                       allow_singular=True).reshape(-1)
                            for i in range(model.n)])


def forward(model: Model, Z: np.ndarray) -> tuple:
    """(filtered, loglik). filtered[t] = P(state at t | candles 0..t). Row t is computed
    from rows 0..t and is never revised by what comes after."""
    logb = _emission(model, Z)
    filtered, loglik = np.zeros((len(Z), model.n)), np.zeros(len(Z))
    alpha = model.startprob
    for t in range(len(Z)):
        prior = alpha if t == 0 else alpha @ model.transmat
        m = logb[t].max()
        joint = prior * np.exp(logb[t] - m)
        total = joint.sum()
        if not np.isfinite(total) or total <= 0:
            raise FloatingPointError(f"filter collapsed at row {t}")
        alpha = joint / total
        filtered[t], loglik[t] = alpha, math.log(total) + m
    return filtered, loglik


def step(model: Model, alpha: np.ndarray | None, z: np.ndarray) -> tuple:
    """One candle of the forward filter: (filtered, loglik) from yesterday's filtered
    probabilities and today's observation. alpha=None starts the filter."""
    logb = _emission(model, z.reshape(1, -1))[0]
    prior = model.startprob if alpha is None else alpha @ model.transmat
    m = logb.max()
    joint = prior * np.exp(logb - m)
    total = joint.sum()
    if not np.isfinite(total) or total <= 0:
        raise FloatingPointError("filter collapsed")
    return joint / total, math.log(total) + m


def next_state(model: Model, filtered_row: np.ndarray) -> np.ndarray:
    """P(state on the next candle | candles so far)."""
    return filtered_row @ model.transmat


def entropy(p: np.ndarray) -> float:
    """0 when the model is certain, 1 when every state is equally likely."""
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0)
    return float(-(p * np.log(p)).sum() / math.log(len(p))) if len(p) > 1 else 0.0
