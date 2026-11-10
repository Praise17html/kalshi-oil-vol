"""Minimal client for Kalshi's public market-data API (no API key needed).

Docs: https://docs.kalshi.com/getting_started
Kalshi splits data into a live store and a historical archive. Markets that settled
before GET /historical/cutoff -> market_settled_ts only exist in /historical/markets,
and their candlesticks come from /historical/markets/{ticker}/candlesticks.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    def __init__(self, base_url: str = BASE_URL, pause: float = 0.12, cache_dir: str | Path = "data/raw"):
        self.base_url = base_url.rstrip("/")
        self.pause = pause  # stay well under the public rate limit
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "kalshi-oil-vol-research/0.1"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- low level ---------------------------------------------------------
    def get(self, path: str, params: dict | None = None, retries: int = 6) -> dict:
        """GET with retries: waits and tries again on dropped connections, timeouts,
        rate limits (429) and server errors (5xx). Other errors (e.g. 404) raise at once."""
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                r = self.session.get(url, params=params, timeout=60)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = e
                print(f"    network hiccup ({type(e).__name__}), retrying in {min(60, 2 ** (attempt + 1))}s...")
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_error = requests.HTTPError(f"{r.status_code} for {url}", response=r)
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            r.raise_for_status()
            time.sleep(self.pause)
            return r.json()
        raise last_error if last_error else RuntimeError(f"GET failed: {url}")

    def paginate(self, path: str, key: str, params: dict | None = None, limit: int = 1000) -> list[dict]:
        params = dict(params or {})
        params["limit"] = limit
        out, cursor = [], None
        while True:
            if cursor:
                params["cursor"] = cursor
            data = self.get(path, params)
            out.extend(data.get(key, []))
            cursor = data.get("cursor")
            if not cursor:
                return out

    # -- endpoints -----------------------------------------------------------
    def historical_cutoff(self) -> dict:
        return self.get("/historical/cutoff")

    def series(self, series_ticker: str) -> dict:
        return self.get(f"/series/{series_ticker}").get("series", {})

    def markets(self, series_ticker: str, status: str | None = None) -> list[dict]:
        params = {"series_ticker": series_ticker}
        if status:
            params["status"] = status
        return self.paginate("/markets", "markets", params)

    def historical_markets(self, series_ticker: str) -> list[dict]:
        return self.paginate("/historical/markets", "markets", {"series_ticker": series_ticker})

    def open_events(self, series_ticker: str) -> list[dict]:
        return self.paginate("/events", "events",
                             {"series_ticker": series_ticker, "status": "open",
                              "with_nested_markets": "true"}, limit=200)

    def candlesticks(self, series_ticker: str, ticker: str, start_ts: int, end_ts: int,
                     period: int = 60, historical: bool = False) -> list[dict]:
        """Raw candlesticks, cached to disk so re-runs don't hit the API again."""
        cache = self.cache_dir / f"candles_{ticker}_{start_ts}_{end_ts}_{period}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        params = {"start_ts": int(start_ts), "end_ts": int(end_ts), "period_interval": period}
        live = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        hist = f"/historical/markets/{ticker}/candlesticks"
        # Markets near the archive cutoff can sit in either store, so try the likely one first,
        # then the other if it answers 404 / 400.
        last_err = None
        for path in ([hist, live] if historical else [live, hist]):
            try:
                data = self.get(path, params)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (400, 404):
                    last_err = e
                    continue
                raise
            candles = data.get("candlesticks", [])
            cache.write_text(json.dumps(candles))
            return candles
        raise last_err


# ---------------------------------------------------------------------------
# Parsing helpers (handle both live and historical field names)
# ---------------------------------------------------------------------------
def _num(x):
    if x is None or x == "":
        return float("nan")
    return float(x)


def _dist(d: dict | None, field: str):
    """Live candles use close_dollars; historical candles use close."""
    if not d:
        return float("nan")
    if f"{field}_dollars" in d:
        return _num(d.get(f"{field}_dollars"))
    return _num(d.get(field))


def parse_candles(ticker: str, candles: list[dict]) -> pd.DataFrame:
    rows = []
    for c in candles:
        rows.append({
            "ticker": ticker,
            "end_ts": int(c["end_period_ts"]),
            "bid": _dist(c.get("yes_bid"), "close"),
            "ask": _dist(c.get("yes_ask"), "close"),
            "last": _dist(c.get("price"), "close"),
            "volume": _num(c.get("volume_fp", c.get("volume"))),
            "open_interest": _num(c.get("open_interest_fp", c.get("open_interest"))),
        })
    return pd.DataFrame(rows)


def to_ts(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


MARKET_COLS = ["ticker", "event_ticker", "strike_type", "floor_strike", "cap_strike",
               "open_time", "close_time", "status", "result", "yes_bid_dollars",
               "yes_ask_dollars", "last_price_dollars", "volume_fp", "rules_primary"]


def markets_frame(markets: list[dict], source: str) -> pd.DataFrame:
    df = pd.DataFrame([{k: m.get(k) for k in MARKET_COLS} for m in markets])
    if df.empty:
        return df
    for col in ["floor_strike", "cap_strike", "yes_bid_dollars", "yes_ask_dollars",
                "last_price_dollars", "volume_fp"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["close_ts"] = df["close_time"].map(to_ts)
    df["open_ts"] = df["open_time"].map(to_ts)
    df["source"] = source
    return df
