"""Make a SYNTHETIC dataset so you can run the whole pipeline before downloading anything.

The fake market has known flaws planted in it, so you can check the analysis finds them:
  - Kalshi prices outcomes 15% wider than the true volatility on normal days
  - On Wednesdays (EIA report) the true volatility is 40% higher; Kalshi knows, OVX doesn't
  - Cheap longshots (< 15c) are marked up by 1.5 cents
None of these numbers say anything about the real Kalshi market.

    python make_demo_data.py && python analyse.py --data data_demo --out outputs_demo
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from oilvol import model
from oilvol.dataset import ET, TRADING_HOURS_PER_YEAR, globex_open

rng = np.random.default_rng(7)
OUT = Path("data_demo")
EIA_BOOST, WIDTH_PREMIUM, LONGSHOT_MARKUP = 1.4, 1.15, 0.015
STEP = 1800  # 30-minute price path


def at_et(d: date, hh: int, mm: int = 0) -> int:
    return int(datetime.combine(d, time(hh, mm), tzinfo=ET).astimezone(timezone.utc).timestamp())


def quote(p: float) -> tuple[float, float]:
    if p < 0.15:
        p += LONGSHOT_MARKUP
    p = min(max(p, 0.005), 0.995)
    return max(0.0, math.floor((p - 0.01) * 100) / 100), min(1.0, math.ceil((p + 0.01) * 100) / 100)


def main():
    days = list(pd.bdate_range("2026-06-01", "2026-11-06").date)
    sigma, sig_true = 0.40, {}
    for d in days:  # true vol wanders slowly
        sigma = float(np.clip(sigma + 0.02 * rng.standard_normal() + 0.05 * (0.40 - sigma), 0.2, 0.8))
        sig_true[d] = sigma

    # one continuous 30-minute price path; it only moves while Globex is open
    t0, t1 = at_et(days[0] - timedelta(days=3), 18), at_et(days[-1], 15)
    grid = np.arange(t0, t1 + STEP, STEP)
    price = np.empty(len(grid))
    cum_tau = np.zeros(len(grid))
    S = 75.0
    for i, ts in enumerate(grid):
        dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        d = dt_utc.astimezone(ET).date()
        dt = (STEP / 3600) / TRADING_HOURS_PER_YEAR if globex_open(dt_utc) else 0.0
        vol = sig_true.get(d, 0.40) * (EIA_BOOST if d.weekday() == 2 else 1.0)
        if dt > 0:
            S *= math.exp(vol * math.sqrt(dt) * rng.standard_normal() - 0.5 * vol * vol * dt)
        price[i] = S
        cum_tau[i] = (cum_tau[i - 1] if i else 0.0) + dt

    def idx(ts: int) -> int:
        return int(np.searchsorted(grid, ts, side="right") - 1)

    settle = {d: round(float(price[idx(at_et(d, 14, 30))]), 2) for d in days}

    ovx = pd.DataFrame({"observation_date": [d.isoformat() for d in days],
                        "OVXCLS": [round(sig_true[d] * 100 * (1 + 0.04 * rng.standard_normal()), 2) for d in days]})
    wti = pd.DataFrame({"observation_date": [d.isoformat() for d in days],
                        "DCOILWTICO": [settle[d] for d in days]})
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    ovx.to_csv(OUT / "raw/fred_OVXCLS.csv", index=False)
    wti.to_csv(OUT / "raw/fred_DCOILWTICO.csv", index=False)

    markets, candles = [], []

    def add_event(series, d, open_ts, close_ts, strikes):
        ev = f"{series}-{d.strftime('%y%b%d').upper()}"
        S_T = settle[d]
        for st, fl, cp in strikes:
            tk = f"{ev}-{'B' if st == 'between' else 'T'}{fl if fl is not None else cp}"
            yes = (S_T > fl) if st == "greater" else (S_T < cp) if st == "less" else (fl <= S_T <= cp)
            markets.append({"ticker": tk, "event_ticker": ev, "strike_type": st, "floor_strike": fl,
                            "cap_strike": cp, "status": "finalized", "result": "yes" if yes else "no",
                            "close_ts": close_ts, "open_ts": open_ts, "source": "synthetic"})
            for end_ts in range(open_ts + 3600, close_ts, 3600):
                i, j = idx(end_ts), idx(close_ts)
                tau = cum_tau[j] - cum_tau[i]
                # the market's width view: true vol x premium, but it knows Wednesday is EIA day
                vol = sig_true[d] * (EIA_BOOST if d.weekday() == 2 else WIDTH_PREMIUM)
                p = model.contract_prob(st, fl, cp, float(price[i]), vol, max(tau, 1e-9))
                bid, ask = quote(p)
                candles.append({"ticker": tk, "end_ts": end_ts, "bid": bid, "ask": ask,
                                "last": np.nan, "volume": 0, "open_interest": 0})

    for k, d in enumerate(days[1:], 1):  # daily ladder: "above $X" strikes, $1 apart
        base = math.floor(settle[days[k - 1]])
        add_event("KXWTI", d, at_et(days[k - 1], 10), at_et(d, 14, 30),
                  [("greater", base + j - 0.01, None) for j in range(-10, 11)])

    for d in [d for d in days if d.weekday() == 4][1:]:  # weekly $1 ranges plus open-ended tails
        mon = d - timedelta(days=4)
        ref = math.floor(float(price[idx(at_et(mon, 10))]))
        strikes = [("less", None, ref - 8.0)] + \
                  [("between", float(k), k + 0.99) for k in range(ref - 8, ref + 8)] + \
                  [("greater", ref + 7.99, None)]
        add_event("KXWTIW", d, at_et(mon, 10), at_et(d, 14, 30), strikes)

    fut = [(ts, round(float(price[idx(ts)]), 2)) for ts in range(int(grid[0]) + 3600, int(grid[-1]), 3600)
           if globex_open(datetime.fromtimestamp(ts - 1, tz=timezone.utc))]
    pd.DataFrame(fut, columns=["ts", "close"]).to_csv(OUT / "futures_CL_hourly.csv", index=False)
    pd.DataFrame(markets).to_csv(OUT / "markets.csv", index=False)
    pd.DataFrame(candles).to_csv(OUT / "candles.csv", index=False)
    print(f"Synthetic data written to {OUT}/ ({len(markets)} contracts, {len(candles)} hourly quotes)")


if __name__ == "__main__":
    main()
