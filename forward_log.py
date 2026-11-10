"""Step 3 (daily): log today's model prices BEFORE the contracts settle.

Run it once a day (e.g. ~6 hours before the 2:30 pm ET WTI settle), then commit the new
CSV to GitHub straight away. The commit timestamp proves the forecast came first - that
is your out-of-sample track record.

    python forward_log.py
    git add forward_log && git commit -m "forward log $(date -u +%F)" && git push
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from oilvol import eia, fred, futures, model
from oilvol.dataset import ET, informative, tau_years
from oilvol.kalshi import KalshiClient, to_ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="+", default=["KXWTI", "KXWTIW"])
    ap.add_argument("--buffer", type=float, default=0.02)
    ap.add_argument("--contracts", type=int, default=10)
    ap.add_argument("--out", default="forward_log")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    client = KalshiClient(cache_dir="data/raw")
    ovx = fred.ovx_sigma(fred.fetch_series("OVXCLS", cache_dir="data/raw", refresh=True))
    sigma_ovx = fred.asof_value(ovx, pd.Timestamp(now.date()))
    print(f"OVX (last close before today): {sigma_ovx * 100:.1f}")
    F_ext = futures.latest_price()  # NaN if yfinance isn't installed
    print(f"CL=F futures centre: {F_ext:.2f}" if F_ext > 0 else "No futures price - using Kalshi's own centre")

    rows = []
    for s in args.series:
        for ev in client.open_events(s):
            mk = [m for m in ev.get("markets", []) if m.get("status") in ("active", "open")]
            if not mk:
                continue
            close_ts = max(to_ts(m["close_time"]) for m in mk)
            tau = tau_years(now_ts, close_ts)
            q = []
            for m in mk:
                bid, ask = float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 0)
                if 0 <= bid < ask <= 1:
                    q.append((m, bid, ask))
            if len(q) < 3 or tau <= 0:
                continue
            fit_set = informative(q)
            fit = model.fit_implied([m["strike_type"] for m, _, _ in fit_set],
                                    [m.get("floor_strike") for m, _, _ in fit_set],
                                    [m.get("cap_strike") for m, _, _ in fit_set],
                                    [(b + a) / 2 for _, b, a in fit_set], tau,
                                    weights=[1 / max(a - b, 0.01) for _, b, a in fit_set])
            if not fit.ok:
                continue
            for m, bid, ask in q:
                F = F_ext if F_ext > 0 else fit.F
                p = model.contract_prob(m["strike_type"], m.get("floor_strike"), m.get("cap_strike"),
                                        F, sigma_ovx, tau)
                fb = model.taker_fee_per_contract(ask, args.contracts)
                fs = model.taker_fee_per_contract(bid, args.contracts)
                signal = "buy_yes" if p - ask > fb + args.buffer else ("sell_yes" if bid - p > fs + args.buffer else "")
                rows.append({"logged_utc": now.isoformat(timespec="seconds"), "series": s,
                             "event_ticker": ev["event_ticker"], "ticker": m["ticker"],
                             "strike_type": m["strike_type"], "floor": m.get("floor_strike"),
                             "cap": m.get("cap_strike"), "close_time": m["close_time"],
                             "hours_to_close": round((close_ts - now_ts) / 3600, 2), "tau": tau,
                             "eia_report_day": eia.is_release_day(
                                 datetime.fromtimestamp(close_ts, tz=timezone.utc).astimezone(ET).date()),
                             "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
                             "fit_F": fit.F, "F_ext": F_ext, "fit_sigma": fit.sigma, "sigma_ovx": sigma_ovx,
                             "p_ovx": p, "signal": signal})
    if not rows:
        print("No open WTI ladders with usable quotes right now.")
        return
    df = pd.DataFrame(rows)
    Path(args.out).mkdir(exist_ok=True)
    path = Path(args.out) / f"{now.strftime('%Y-%m-%dT%H%MZ')}.csv"
    df.to_csv(path, index=False)
    ev = df.drop_duplicates("event_ticker")
    for r in ev.itertuples():
        print(f"{r.event_ticker}: centre {r.fit_F:.2f}, Kalshi vol {r.fit_sigma*100:.1f} vs OVX {r.sigma_ovx*100:.1f}"
              f" ({r.hours_to_close:.1f}h to close)")
    sig = df[df["signal"] != ""]
    print(f"\n{len(sig)} signals logged to {path}")
    if len(sig):
        print(sig[["ticker", "bid", "ask", "p_ovx", "signal"]].round(3).to_string(index=False))
    print("\nNow commit it:  git add forward_log && git commit -m 'forward log' && git push")


if __name__ == "__main__":
    main()
