"""Step 4 (weekly): score the forward log once contracts have settled.

    python score_forward.py
Prints Brier scores (Kalshi mid vs model) and paper P&L of the logged signals.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from oilvol import model
from oilvol.kalshi import KalshiClient


def fetch_results(client: KalshiClient, tickers: list[str]) -> dict[str, str]:
    out = {}
    for i in range(0, len(tickers), 100):
        chunk = ",".join(tickers[i:i + 100])
        for path in ("/markets", "/historical/markets"):
            data = client.get(path, {"tickers": chunk, "limit": 1000})
            for m in data.get("markets", []):
                if m.get("result") in ("yes", "no"):
                    out[m["ticker"]] = m["result"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="forward_log")
    ap.add_argument("--contracts", type=int, default=10)
    args = ap.parse_args()

    files = sorted(Path(args.log).glob("*.csv"))
    if not files:
        raise SystemExit("No forward logs yet - run forward_log.py first.")
    log = pd.concat([pd.read_csv(f).assign(log_date=f.name[:10]) for f in files], ignore_index=True)
    # If the log ran more than once in a day (by hand and by the scheduler), score only the first run.
    log = log.drop_duplicates(["ticker", "log_date"], keep="first")
    res = fetch_results(KalshiClient(cache_dir="data/raw"), sorted(log["ticker"].unique()))
    log["result"] = log["ticker"].map(res)
    done = log.dropna(subset=["result"]).copy()
    done["y"] = (done["result"] == "yes").astype(int)
    print(f"Logged rows: {len(log)} | settled: {len(done)} | days logged: {log["log_date"].nunique()}")
    if done.empty:
        return
    print(f"Brier  Kalshi mid: {model.brier(done['mid'], done['y']):.4f}   "
          f"Model (OVX width): {model.brier(done['p_ovx'], done['y']):.4f}")

    sig = done[done["signal"].fillna("") != ""].copy()
    if sig.empty:
        print("No settled signals yet.")
        return
    def pnl(r):
        if r.signal == "buy_yes":
            return r.y - r.ask - model.taker_fee_per_contract(r.ask, args.contracts)
        return r.bid - r.y - model.taker_fee_per_contract(r.bid, args.contracts)
    sig["pnl"] = sig.apply(pnl, axis=1)
    print(f"Signals settled: {len(sig)} | mean P&L/contract: ${sig['pnl'].mean():.4f} | "
          f"total: ${sig['pnl'].sum():.2f} per contract | win rate: {(sig['pnl'] > 0).mean():.0%}")
    Path("outputs").mkdir(exist_ok=True)
    sig.to_csv("outputs/scored_signals.csv", index=False)


if __name__ == "__main__":
    main()
