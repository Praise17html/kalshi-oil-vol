"""Fair-value model for Kalshi WTI contracts.

Every Kalshi oil contract pays $1 if the WTI front-month settle lands on one side of a
strike (or inside a range). If the settle S_T is lognormal with annualised volatility
sigma, then under the forward measure:

    P(S_T > K) = N(d2),   d2 = [ln(F/K) - 0.5 * sigma^2 * tau] / (sigma * sqrt(tau))

where F is today's forward (≈ the front-month futures price) and tau is the time to
settlement in years. A whole ladder of contracts therefore pins down two numbers:
where the market thinks the price is centred (F) and how wide it thinks the outcome
distribution is (sigma). This project is about the second number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

TICK = 0.01  # WTI settles are quoted to the cent


# ---------------------------------------------------------------------------
# Probabilities
# ---------------------------------------------------------------------------
def prob_above(F: float, K: np.ndarray | float, sigma: float, tau: float) -> np.ndarray:
    """P(S_T > K) for a lognormal S_T with forward F."""
    K = np.asarray(K, dtype=float)
    if tau <= 0 or sigma <= 0:
        return (F > K).astype(float)
    sd = sigma * math.sqrt(tau)
    d2 = (np.log(F / K) - 0.5 * sd * sd) / sd
    return norm.cdf(d2)


def contract_prob(strike_type: str, floor: float | None, cap: float | None,
                  F: float, sigma: float, tau: float) -> float:
    """Model probability that a single Kalshi contract settles YES.

    Kalshi strike types (from the Market object):
      greater            -> YES if S > floor       less            -> YES if S < cap
      greater_or_equal   -> YES if S >= floor      less_or_equal   -> YES if S <= cap
      between            -> YES if floor <= S <= cap  (both ends inclusive, e.g. 96.00-96.99)
    The settle is quoted in whole cents, so a printed settle s covers the continuous
    interval [s - half a tick, s + half a tick). Using those edges makes a full set of
    ranges plus its two tails add up to exactly 1.
    """
    h = TICK / 2
    st = (strike_type or "").lower()
    if st == "greater":
        return float(prob_above(F, floor + h, sigma, tau))
    if st == "greater_or_equal":
        return float(prob_above(F, floor - h, sigma, tau))
    if st == "less":
        return float(1.0 - prob_above(F, cap - h, sigma, tau))
    if st == "less_or_equal":
        return float(1.0 - prob_above(F, cap + h, sigma, tau))
    if st == "between":
        return float(prob_above(F, floor - h, sigma, tau) - prob_above(F, cap + h, sigma, tau))
    raise ValueError(f"Unsupported strike_type: {strike_type!r}")


def ladder_probs(strike_types, floors, caps, F, sigma, tau) -> np.ndarray:
    return np.array([
        contract_prob(st, fl, cp, F, sigma, tau)
        for st, fl, cp in zip(strike_types, floors, caps)
    ])


# ---------------------------------------------------------------------------
# Fitting the market's implied centre and width
# ---------------------------------------------------------------------------
@dataclass
class ImpliedFit:
    F: float          # market-implied centre (forward)
    sigma: float      # market-implied annualised vol
    rmse: float       # fit error in probability points
    n: int            # contracts used
    ok: bool


def _initial_centre(strike_types, floors, caps, mids) -> float:
    """Rough starting point: the strike where P(above) crosses 0.5, else the mean strike."""
    pts = []
    for st, fl, cp, m in zip(strike_types, floors, caps, mids):
        if st in ("greater", "greater_or_equal") and fl is not None:
            pts.append((fl, m))
    if len(pts) >= 2:
        pts.sort()
        ks, ps = zip(*pts)
        ps = np.array(ps)
        # survival curve falls with strike; interpolate where it crosses 0.5
        if ps.max() >= 0.5 >= ps.min():
            return float(np.interp(0.5, ps[::-1], np.array(ks)[::-1]))
    vals = [v for v in list(floors) + list(caps) if v is not None and not math.isnan(v)]
    return float(np.mean(vals)) if vals else 70.0


def fit_implied(strike_types, floors, caps, mids, tau, weights=None,
                sigma0: float = 0.40) -> ImpliedFit:
    """Least-squares fit of (F, sigma) to a ladder of mid prices.

    Weights let you trust tight, liquid quotes more than wide ones (e.g. 1/spread).
    """
    strike_types = list(strike_types)
    floors = [None if (f is None or (isinstance(f, float) and math.isnan(f))) else float(f) for f in floors]
    caps = [None if (c is None or (isinstance(c, float) and math.isnan(c))) else float(c) for c in caps]
    mids = np.asarray(mids, dtype=float)
    n = len(mids)
    if n < 3 or tau <= 0:
        return ImpliedFit(float("nan"), float("nan"), float("nan"), n, False)
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)

    F0 = _initial_centre(strike_types, floors, caps, mids)

    def loss(x):
        F, sig = math.exp(x[0]), math.exp(x[1])
        p = ladder_probs(strike_types, floors, caps, F, sig, tau)
        return float(np.sum(w * (p - mids) ** 2) / np.sum(w))

    res = minimize(loss, x0=[math.log(F0), math.log(sigma0)], method="Nelder-Mead",
                   options={"xatol": 1e-6, "fatol": 1e-10, "maxiter": 4000})
    F, sig = math.exp(res.x[0]), math.exp(res.x[1])
    rmse = math.sqrt(loss(res.x))
    ok = bool(res.success) and 0.02 < sig < 5.0
    return ImpliedFit(F, sig, rmse, n, ok)


# ---------------------------------------------------------------------------
# Fees (Kalshi general schedule; check /series for multipliers on oil series)
# ---------------------------------------------------------------------------
def taker_fee_per_contract(price: float, contracts: int = 10, multiplier: float = 1.0,
                           rate: float = 0.07) -> float:
    """Kalshi taker fee: round_up(M * 0.07 * C * P * (1 - P)) to the cent, per contract.

    Rounding is per order, so small orders pay proportionally more.
    """
    raw = multiplier * rate * contracts * price * (1.0 - price)
    return math.ceil(round(raw * 100, 9)) / 100.0 / contracts


def maker_fee_per_contract(price: float, contracts: int = 10, multiplier: float = 0.0) -> float:
    """Maker fee: same shape at 0.0175; the default multiplier on most series is 0."""
    if multiplier == 0:
        return 0.0
    return taker_fee_per_contract(price, contracts, multiplier, rate=0.0175)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def log_loss(p, y, eps: float = 1e-4) -> float:
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def z_distance(strike_type, floor, cap, F, sigma, tau) -> float:
    """How many standard deviations a contract's strike sits from the centre.

    Used to bucket contracts into centre / shoulder / tail. For ranges, the nearer edge counts.
    """
    if not (F > 0 and sigma > 0 and tau > 0):
        return float("nan")
    sd = sigma * math.sqrt(tau)
    st = (strike_type or "").lower()
    if st in ("greater", "greater_or_equal"):
        k = floor
    elif st in ("less", "less_or_equal"):
        k = cap
    else:
        lo, hi = math.log(floor / F) / sd, math.log((cap + TICK / 2) / F) / sd
        if lo <= 0 <= hi:
            return 0.0
        return min(abs(lo), abs(hi))
    return abs(math.log(k / F) / sd)
