"""Optional: an independent centre for the model from WTI futures prices.

The cleanest version of this study takes the centre (F) from the futures market and the
width (sigma) from the options market, so Kalshi prices are never used to price Kalshi.
Free hourly CL=F bars come from Yahoo Finance via `pip install yfinance` (about 2 years
of hourly history). CL=F is the continuous front month; it can differ from Kalshi's
front-month definition for a couple of days around each monthly roll - check those days.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

CSV_NAME = "futures_CL_hourly.csv"   # columns: ts (unix seconds, bar end in UTC), close


def download_cl_hourly(out_dir: str | Path = "data", period: str = "365d") -> Path | None:
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed - skipping futures centre (pip install yfinance to enable).")
        return None
    df = yf.download("CL=F", period=period, interval="60m", progress=False, auto_adjust=False)
    if df is None or df.empty:
        print("yfinance returned no CL=F data.")
        return None
    close = df["Close"]
    if isinstance(close, pd.DataFrame):      # newer yfinance returns (field, ticker) columns
        close = close.iloc[:, 0]
    idx = pd.DatetimeIndex(close.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    # yfinance stamps hourly bars at their START; Kalshi candles are stamped at their END
    out = pd.DataFrame({"ts": idx.as_unit("s").asi8 + 3600, "close": close.to_numpy()}).dropna()
    path = Path(out_dir) / CSV_NAME
    out.to_csv(path, index=False)
    print(f"CL=F hourly: {len(out)} bars saved to {path}")
    return path


def load_center(data_dir: str | Path) -> pd.Series | None:
    path = Path(data_dir) / CSV_NAME
    if not path.exists():
        return None
    df = pd.read_csv(path)
    return pd.Series(df["close"].to_numpy(), index=df["ts"].astype("int64")).sort_index()


def price_asof(center: pd.Series | None, ts: int, max_age_s: int = 3 * 3600) -> float:
    if center is None or center.empty:
        return float("nan")
    s = center[(center.index <= ts) & (center.index > ts - max_age_s)]
    return float(s.iloc[-1]) if len(s) else float("nan")


def latest_price() -> float:
    """Most recent CL=F price for the forward logger (Yahoo quotes are ~10 min delayed)."""
    try:
        import yfinance as yf
        h = yf.Ticker("CL=F").history(period="5d", interval="1m")
        return float(h["Close"].dropna().iloc[-1])
    except Exception:
        return float("nan")
