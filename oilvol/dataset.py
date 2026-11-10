"""Turn raw markets + candlesticks into a tidy snapshot table.

One row = one contract, observed `hours_before` hours before its event closes, with:
  - the Kalshi bid/ask/mid at that moment
  - the event's market-implied centre (F) and width (sigma) fitted across the ladder
  - OVX- and realised-vol-based model probabilities using the market's own centre
  - the outcome (1 if the contract settled YES)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import model
from .fred import asof_value

ET = ZoneInfo("America/New_York")
TRADING_HOURS_PER_YEAR = 252 * 23  # CME Globex WTI trades ~23h a day


def globex_open(dt_utc: datetime) -> bool:
    """Is CME Globex crude open at this instant? Sun 18:00 ET -> Fri 17:00 ET, daily 17:00-18:00 break.
    Exchange holidays are ignored (small effect)."""
    et = dt_utc.astimezone(ET)
    wd, hr = et.weekday(), et.hour  # Mon=0 ... Sun=6
    if wd == 5:                      # Saturday
        return False
    if wd == 6:                      # Sunday: opens 18:00
        return hr >= 18
    if wd == 4 and hr >= 17:         # Friday after 17:00
        return False
    return hr != 17                  # daily maintenance hour


def tau_years(start_ts: int, end_ts: int, step_minutes: int = 15) -> float:
    """Trading time between two unix timestamps, in years of trading hours."""
    if end_ts <= start_ts:
        return 0.0
    t = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ts, tz=timezone.utc)
    step = timedelta(minutes=step_minutes)
    open_steps = 0
    while t < end:
        if globex_open(t):
            open_steps += 1
        t += step
    return open_steps * step_minutes / 60.0 / TRADING_HOURS_PER_YEAR


def quote_at(candles: pd.DataFrame, ticker: str, snap_ts: int, max_age_s: int = 3 * 3600):
    """Bid/ask from the last candle ending at or before snap_ts."""
    c = candles[(candles["ticker"] == ticker) & (candles["end_ts"] <= snap_ts)
                & (candles["end_ts"] > snap_ts - max_age_s)]
    if c.empty:
        return np.nan, np.nan
    row = c.loc[c["end_ts"].idxmax()]
    return row["bid"], row["ask"]


def valid_quote(bid: float, ask: float) -> bool:
    return (not np.isnan(bid)) and (not np.isnan(ask)) and 0.0 <= bid < ask <= 1.0


def informative(quotes, max_spread: float = 0.10, lo: float = 0.03, hi: float = 0.97):
    """Quotes that actually pin down the distribution.

    Contracts stuck at 1-2c or 98-99c only say 'unlikely' / 'likely'; their prices sit on
    the tick floor and get marked up, so including them biases the fitted width and centre.
    """
    return [(m, b, a) for m, b, a in quotes if a - b <= max_spread and lo <= (a + b) / 2 <= hi]


def build_snapshots(markets: pd.DataFrame, candles: pd.DataFrame, sigma_ovx: pd.Series,
                    sigma_rv: pd.Series | None = None, hours_before=(24, 6),
                    max_fit_spread: float = 0.10, center: pd.Series | None = None) -> pd.DataFrame:
    """center: optional hourly WTI futures closes (index = unix seconds, bar end). When given,
    p_ovx_ext / p_rv_ext use the futures price as the centre instead of Kalshi's own."""
    from .futures import price_asof
    markets = markets[markets["result"].isin(["yes", "no"])].copy()
    rows = []
    for event, grp in markets.groupby("event_ticker"):
        close_ts = int(grp["close_ts"].max())
        close_et = datetime.fromtimestamp(close_ts, tz=timezone.utc).astimezone(ET)
        for h in hours_before:
            snap_ts = close_ts - int(h * 3600)
            if "open_ts" in grp and grp["open_ts"].notna().any() and snap_ts < grp["open_ts"].min():
                continue  # ladder not listed yet
            tau = tau_years(snap_ts, close_ts)
            snap_day = datetime.fromtimestamp(snap_ts, tz=timezone.utc).astimezone(ET)
            s_ovx = asof_value(sigma_ovx, pd.Timestamp(snap_day.date()))
            s_rv = asof_value(sigma_rv, pd.Timestamp(snap_day.date())) if sigma_rv is not None else np.nan

            q = []
            for _, m in grp.iterrows():
                bid, ask = quote_at(candles, m["ticker"], snap_ts)
                if valid_quote(bid, ask):
                    q.append((m, bid, ask))
            if len(q) < 3 or tau <= 0:
                continue

            F_ext = price_asof(center, snap_ts)
            fit_set = informative(q, max_fit_spread)
            fit = model.fit_implied(
                [m["strike_type"] for m, _, _ in fit_set],
                [m["floor_strike"] for m, _, _ in fit_set],
                [m["cap_strike"] for m, _, _ in fit_set],
                [(b + a) / 2 for _, b, a in fit_set],
                tau,
                weights=[1.0 / max(a - b, 0.01) for _, b, a in fit_set],
            )
            for m, bid, ask in q:
                st, fl, cp = m["strike_type"], m["floor_strike"], m["cap_strike"]
                row = {
                    "event_ticker": event, "ticker": m["ticker"], "series": event.split("-")[0],
                    "strike_type": st, "floor": fl, "cap": cp,
                    "close_ts": close_ts, "settle_date": close_et.date().isoformat(),
                    "weekday": close_et.strftime("%a"), "is_eia_wednesday": close_et.weekday() == 2,
                    "hours_before": h, "snap_ts": snap_ts, "tau": tau,
                    "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": ask - bid,
                    "y": 1 if m["result"] == "yes" else 0,
                    "sigma_ovx": s_ovx, "sigma_rv": s_rv,
                    "fit_F": fit.F, "fit_sigma": fit.sigma, "fit_rmse": fit.rmse, "fit_ok": fit.ok,
                    "fit_n": fit.n, "F_ext": F_ext,
                }
                if fit.ok:
                    row["p_fit"] = model.contract_prob(st, fl, cp, fit.F, fit.sigma, tau)
                    row["p_ovx"] = model.contract_prob(st, fl, cp, fit.F, s_ovx, tau) if s_ovx > 0 else np.nan
                    row["p_rv"] = model.contract_prob(st, fl, cp, fit.F, s_rv, tau) if s_rv > 0 else np.nan
                    row["z"] = model.z_distance(st, fl, cp, fit.F, fit.sigma, tau)
                if F_ext > 0:
                    row["p_ovx_ext"] = model.contract_prob(st, fl, cp, F_ext, s_ovx, tau) if s_ovx > 0 else np.nan
                    row["p_rv_ext"] = model.contract_prob(st, fl, cp, F_ext, s_rv, tau) if s_rv > 0 else np.nan
                rows.append(row)
    return pd.DataFrame(rows)
