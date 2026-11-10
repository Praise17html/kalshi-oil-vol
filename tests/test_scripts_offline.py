"""Run download.py and forward_log.py end to end against a fake Kalshi API (no network)."""
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import download
import forward_log
from oilvol import fred, futures, model
from oilvol.kalshi import KalshiClient

CLOSE = "2026-11-05T19:30:00Z"
CLOSE_TS = int(datetime(2026, 11, 5, 19, 30, tzinfo=timezone.utc).timestamp())
STRIKES = [68.99, 69.99, 70.99, 71.99, 72.99, 73.99]


def fake_market(i, k, result="no", status="finalized", close=CLOSE, bid=None, ask=None):
    m = {"ticker": f"KXWTI-26NOV05-T{k}", "event_ticker": "KXWTI-26NOV05", "strike_type": "greater",
         "floor_strike": k, "cap_strike": None, "open_time": "2026-11-04T15:00:00Z", "close_time": close,
         "status": status, "result": result, "volume_fp": "100.00", "rules_primary": "..."}
    if bid is not None:
        m.update({"yes_bid_dollars": f"{bid:.4f}", "yes_ask_dollars": f"{ask:.4f}"})
    return m


def fake_get(self, path, params=None, retries=5):
    if path == "/historical/cutoff":
        return {"market_settled_ts": "2026-10-01T00:00:00Z", "trades_created_ts": "2026-10-01T00:00:00Z",
                "orders_updated_ts": "2026-10-01T00:00:00Z"}
    if path.startswith("/series/") and path.count("/") == 2:
        return {"series": {"title": "WTI daily", "fee_type": "quadratic", "fee_multiplier": 1}}
    if path == "/markets":
        return {"markets": [fake_market(i, k, "yes" if k < 71 else "no") for i, k in enumerate(STRIKES)], "cursor": ""}
    if path == "/historical/markets":
        return {"markets": [], "cursor": ""}
    if path.endswith("/candlesticks"):
        k = float(path.split("-T")[-1].split("/")[0])
        p = float(model.prob_above(71.2, k, 0.4, 1 / 252))
        b, a = max(0.0, round(p - 0.01, 2)), min(1.0, round(p + 0.01, 2))
        ts = range(params["start_ts"] + 3600, params["end_ts"] + 1, 3600)
        if path.startswith("/historical"):
            return {"candlesticks": [{"end_period_ts": t, "yes_bid": {"close": str(b)}, "yes_ask": {"close": str(a)},
                                      "price": {"close": None}, "volume": "0.00", "open_interest": "0.00"} for t in ts]}
        return {"candlesticks": [{"end_period_ts": t, "yes_bid": {"close_dollars": str(b)},
                                  "yes_ask": {"close_dollars": str(a)}, "price": {"close_dollars": None},
                                  "volume_fp": "0.00", "open_interest_fp": "0.00"} for t in ts]}
    if path == "/events":
        close = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat().replace("+00:00", "Z")
        mk = []
        for i, k in enumerate(STRIKES):
            p = float(model.prob_above(71.2, k, 0.6, 20 / 5796))
            mk.append(fake_market(i, k, "", "active", close, max(0.0, round(p - 0.01, 2)), min(1.0, round(p + 0.01, 2))))
        return {"events": [{"event_ticker": "KXWTI-TEST", "series_ticker": "KXWTI", "markets": mk}], "cursor": ""}
    raise AssertionError(f"unexpected path {path}")


def fake_fred(series_id, cache_dir="data/raw", refresh=False):
    idx = pd.bdate_range("2026-01-01", "2026-12-31")
    vals = np.full(len(idx), 40.0 if series_id == "OVXCLS" else 71.0)
    return pd.Series(vals, index=idx, name=series_id)


def test_download_and_forward_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(KalshiClient, "get", fake_get)
    monkeypatch.setattr(fred, "fetch_series", fake_fred)
    monkeypatch.setattr(futures, "download_cl_hourly", lambda out_dir="data", period="730d": None)
    monkeypatch.setattr(futures, "latest_price", lambda: float("nan"))

    monkeypatch.setattr(sys, "argv", ["download.py", "--series", "KXWTI", "--since", "2026-01-01"])
    download.main()
    m = pd.read_csv(tmp_path / "data/markets.csv")
    c = pd.read_csv(tmp_path / "data/candles.csv")
    assert len(m) == len(STRIKES) and set(m["result"]) == {"yes", "no"}
    assert len(c) > 0 and c["bid"].between(0, 1).all()

    monkeypatch.setattr(sys, "argv", ["forward_log.py", "--series", "KXWTI"])
    forward_log.main()
    logs = list((tmp_path / "forward_log").glob("*.csv"))
    assert len(logs) == 1
    log = pd.read_csv(logs[0])
    assert {"p_ovx", "fit_sigma", "signal"} <= set(log.columns)
    # Kalshi priced 60% vol vs OVX 40%: fitted width should come out near 0.6
    assert abs(log["fit_sigma"].iloc[0] - 0.6) < 0.1


def test_candles_fall_back_when_archive_says_404(tmp_path, monkeypatch):
    import requests

    def get(self, path, params=None, retries=5):
        if path.startswith("/historical"):
            r = requests.Response()
            r.status_code = 404
            r.url = "https://example/" + path
            r.raise_for_status()
        return {"candlesticks": [{"end_period_ts": 1, "yes_bid": {"close_dollars": "0.40"},
                                  "yes_ask": {"close_dollars": "0.42"}, "price": {},
                                  "volume_fp": "0", "open_interest_fp": "0"}]}

    monkeypatch.setattr(KalshiClient, "get", get)
    client = KalshiClient(cache_dir=tmp_path)
    out = client.candlesticks("KXWTI", "KXWTI-26SEP0914-T97.99", 1, 2, historical=True)
    assert out and out[0]["yes_bid"]["close_dollars"] == "0.40"


def test_download_skips_a_market_that_fails_everywhere(tmp_path, monkeypatch):
    import requests

    def get(self, path, params=None, retries=5):
        if path.endswith("/candlesticks") and "T70.99" in path:
            r = requests.Response()
            r.status_code = 404
            r.url = "https://example/" + path
            r.raise_for_status()
        return fake_get(self, path, params, retries)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(KalshiClient, "get", get)
    monkeypatch.setattr(fred, "fetch_series", fake_fred)
    monkeypatch.setattr(futures, "download_cl_hourly", lambda out_dir="data", period="365d": None)
    monkeypatch.setattr(sys, "argv", ["download.py", "--series", "KXWTI", "--since", "2026-01-01"])
    download.main()
    c = pd.read_csv(tmp_path / "data/candles.csv")
    failed = pd.read_csv(tmp_path / "data/failed_candles.csv")
    assert len(failed) == 1 and c["ticker"].nunique() == len(STRIKES) - 1
