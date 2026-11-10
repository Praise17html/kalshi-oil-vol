"""Free daily data from FRED (no API key): OVX and WTI spot.

OVXCLS     CBOE Crude Oil ETF Volatility Index (30-day implied vol on USO options), daily close, in % points.
DCOILWTICO WTI Cushing spot price (EIA), daily. Close to the front-month settle; used for realised vol.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"


def fetch_series(series_id: str, cache_dir: str | Path = "data/raw", refresh: bool = False) -> pd.Series:
    cache = Path(cache_dir) / f"fred_{series_id}.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not cache.exists():
        r = requests.get(FRED_CSV.format(sid=series_id), timeout=30,
                         headers={"User-Agent": "kalshi-oil-vol-research/0.1"})
        r.raise_for_status()
        cache.write_text(r.text)
    return parse_fred_csv(cache.read_text(), series_id)


def parse_fred_csv(text: str, series_id: str) -> pd.Series:
    df = pd.read_csv(StringIO(text))
    date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
    s = pd.to_numeric(df[series_id], errors="coerce")  # FRED marks gaps with "."
    s.index = pd.to_datetime(df[date_col])
    return s.dropna().rename(series_id)


def ovx_sigma(ovx: pd.Series) -> pd.Series:
    """OVX is quoted in vol points (e.g. 45 = 45% annualised)."""
    return (ovx / 100.0).rename("sigma_ovx")


def ewma_realised_vol(price: pd.Series, lam: float = 0.94) -> pd.Series:
    """RiskMetrics-style EWMA of daily log returns, annualised with sqrt(252).

    Value on date d uses returns up to and including d (so use the previous day's value
    for any forecast made before d's close).
    """
    r = np.log(price).diff().dropna()
    var = r.pow(2).ewm(alpha=1 - lam, adjust=False).mean()
    return np.sqrt(var * 252).rename("sigma_rv")


def asof_value(series: pd.Series, when: pd.Timestamp, strictly_before: bool = True) -> float:
    """Last value known before `when` (avoids look-ahead: today's close isn't known intraday)."""
    when = pd.Timestamp(when).tz_localize(None) if pd.Timestamp(when).tzinfo else pd.Timestamp(when)
    cutoff = when.normalize()
    s = series[series.index < cutoff] if strictly_before else series[series.index <= cutoff]
    return float(s.iloc[-1]) if len(s) else float("nan")
