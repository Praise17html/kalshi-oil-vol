"""Step 1: download settled Kalshi WTI markets, their hourly quotes, and FRED vol data.

Usage (from the repo folder):
    python download.py                         # KXWTI daily + KXWTIW weekly, last 120 days
    python download.py --since 2026-01-01      # longer history
    python download.py --max-events 5          # quick smoke test

Writes data/markets.csv, data/candles.csv and caches raw responses in data/raw/.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd

from oilvol import fred, futures
from oilvol.kalshi import KalshiClient, markets_frame, parse_candles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="+", default=["KXWTI", "KXWTIW"])
    ap.add_argument("--since", default=(datetime.now(timezone.utc) - timedelta(days=120)).date().isoformat())
    ap.add_argument("--window-hours", type=int, default=80, help="hours of quotes to pull before each close")
    ap.add_argument("--max-events", type=int, default=None)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    client = KalshiClient(cache_dir=f"{args.out}/raw")
    cutoff = client.historical_cutoff()
    print(f"Historical cutoff (market_settled_ts): {cutoff.get('market_settled_ts')}")

    since_ts = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
    frames = []
    for s in args.series:
        info = client.series(s)
        print(f"{s}: {info.get('title', '')} | fee_type={info.get('fee_type')} fee_multiplier={info.get('fee_multiplier')}")
        live = markets_frame(client.markets(s, status="settled"), "live")
        hist = markets_frame(client.historical_markets(s), "historical")
        df = pd.concat([live, hist], ignore_index=True)
        if df.empty:
            print(f"  no settled markets found for {s}")
            continue
        df = df.drop_duplicates("ticker", keep="first")
        df = df[df["close_ts"] >= since_ts]
        if args.max_events:
            last_close = df.groupby("event_ticker")["close_ts"].max().sort_values()
            keep = list(last_close.index[-args.max_events:])  # most recent events, by close time
            df = df[df["event_ticker"].isin(keep)]
        print(f"  {df['event_ticker'].nunique()} events, {len(df)} contracts since {args.since}")
        frames.append(df)

    if not frames:
        raise SystemExit("Nothing downloaded - check the series tickers on kalshi.com.")
    markets = pd.concat(frames, ignore_index=True)
    markets.to_csv(f"{args.out}/markets.csv", index=False)

    candles, failed = [], []
    for i, m in enumerate(markets.itertuples(), 1):
        end_ts = int(m.close_ts)
        start_ts = end_ts - args.window_hours * 3600
        historical = m.source == "historical"  # where the market list itself came from
        try:
            raw = client.candlesticks(m.event_ticker.split("-")[0], m.ticker, start_ts, end_ts,
                                      period=60, historical=historical)
        except Exception as e:  # one bad market shouldn't stop the whole download
            failed.append((m.ticker, str(e)[:150]))
            continue
        candles.append(parse_candles(m.ticker, raw))
        if i % 100 == 0:
            print(f"  candles: {i}/{len(markets)}")
    if failed:
        print(f"  skipped {len(failed)} contracts whose prices couldn't be fetched, e.g. {failed[0][0]}")
        pd.DataFrame(failed, columns=["ticker", "error"]).to_csv(f"{args.out}/failed_candles.csv", index=False)
    pd.concat(candles, ignore_index=True).to_csv(f"{args.out}/candles.csv", index=False)

    for sid in ["OVXCLS", "DCOILWTICO"]:
        s = fred.fetch_series(sid, cache_dir=f"{args.out}/raw", refresh=True)
        print(f"FRED {sid}: {len(s)} obs, last {s.index[-1].date()} = {s.iloc[-1]:.2f}")
    futures.download_cl_hourly(args.out)  # optional independent centre (needs: pip install yfinance)
    print("Done. Next: python analyse.py")


if __name__ == "__main__":
    main()
