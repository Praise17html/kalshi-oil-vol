"""Step 2: build snapshots and answer the three research questions.

  Q1  Width   - Is the spread of outcomes Kalshi prices wider or narrower than OVX implies?
  Q2  Accuracy - Whose probabilities score better on what actually happened: Kalshi, or the model?
                 Where - centre vs tails, EIA Wednesdays vs other days?
  Q3  Edge    - After Kalshi's fees and the bid/ask spread, would trading the gap have made money?

Usage:
    python analyse.py                    # uses data/
    python analyse.py --data data_demo   # synthetic demo data (see make_demo_data.py)
Writes outputs/summary.md, outputs/*.png, outputs/snapshots.csv, outputs/trades.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from oilvol import fred, futures, model
from oilvol.dataset import build_snapshots

BUCKETS = [(0, 0.5, "centre (<0.5 sd)"), (0.5, 1.5, "shoulder (0.5-1.5 sd)"), (1.5, 99, "tail (>1.5 sd)")]


def bucket(z):
    for lo, hi, name in BUCKETS:
        if lo <= z < hi:
            return name
    return "n/a"


def score_table(df: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    cols = {"Kalshi mid": "mid", "OVX width + Kalshi centre": "p_ovx",
            "OVX width + futures centre": "p_ovx_ext", "Realised vol + futures centre": "p_rv_ext",
            "Lognormal fit to Kalshi": "p_fit"}
    cols = {k: v for k, v in cols.items() if v in df.columns}
    groups = [("all", df)] if by is None else list(df.groupby(by))
    out = []
    for name, g in groups:
        g = g.dropna(subset=list(cols.values()))  # same contracts for every column
        row = {"group": name, "n": len(g)}
        for label, col in cols.items():
            row[f"Brier: {label}"] = model.brier(g[col], g["y"]) if len(g) else np.nan
        out.append(row)
    return pd.DataFrame(out)


def backtest(df: pd.DataFrame, p_col: str, buffer: float, contracts: int) -> pd.DataFrame:
    trades = []
    for r in df.dropna(subset=[p_col]).itertuples():
        p = getattr(r, p_col)
        fee_buy = model.taker_fee_per_contract(r.ask, contracts)
        fee_sell = model.taker_fee_per_contract(r.bid, contracts)
        if p - r.ask > fee_buy + buffer:          # YES looks cheap -> buy YES at the ask
            pnl = r.y - r.ask - fee_buy
            trades.append({**r._asdict(), "side": "buy_yes", "edge": p - r.ask, "pnl": pnl})
        elif r.bid - p > fee_sell + buffer:       # YES looks rich -> sell YES (buy NO) at the bid
            pnl = r.bid - r.y - fee_sell
            trades.append({**r._asdict(), "side": "sell_yes", "edge": r.bid - p, "pnl": pnl})
    return pd.DataFrame(trades)


def pnl_summary(t: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    if t.empty:
        return pd.DataFrame([{"group": "all", "trades": 0}])
    groups = [("all", t)] if by is None else list(t.groupby(by))
    rows = []
    for name, g in groups:
        # trades in the same event share one outcome, so test significance across events
        per_event = g.groupby("event_ticker")["pnl"].sum()
        sd = per_event.std(ddof=1) if len(per_event) > 2 else np.nan
        rows.append({"group": name, "trades": len(g), "events": len(per_event),
                     "mean P&L per contract ($)": g["pnl"].mean(), "total ($/contract)": g["pnl"].sum(),
                     "win rate": (g["pnl"] > 0).mean(),
                     "t-stat (by event)": per_event.mean() / (sd / np.sqrt(len(per_event)))
                     if sd and sd > 0 else np.nan})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--hours-before", nargs="+", type=float, default=[24, 6])
    ap.add_argument("--trade-hours", type=float, default=24, help="snapshot used for the backtest")
    ap.add_argument("--buffer", type=float, default=0.02, help="extra edge required beyond fees")
    ap.add_argument("--contracts", type=int, default=10, help="order size for fee rounding")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(exist_ok=True)

    markets = pd.read_csv(f"{args.data}/markets.csv")
    candles = pd.read_csv(f"{args.data}/candles.csv")
    ovx = fred.parse_fred_csv(Path(f"{args.data}/raw/fred_OVXCLS.csv").read_text(), "OVXCLS")
    wti = fred.parse_fred_csv(Path(f"{args.data}/raw/fred_DCOILWTICO.csv").read_text(), "DCOILWTICO")

    center = futures.load_center(args.data)
    print("Futures centre:", "found" if center is not None else "not found (Kalshi-implied centre only)")
    snaps = build_snapshots(markets, candles, fred.ovx_sigma(ovx), fred.ewma_realised_vol(wti),
                            hours_before=args.hours_before, center=center)
    if snaps.empty:
        raise SystemExit("No usable snapshots - check that candles cover the hours before close.")
    snaps = snaps[snaps["fit_ok"] == True].copy()  # noqa: E712
    snaps["bucket"] = snaps["z"].map(bucket)
    snaps["day_type"] = np.where(snaps["is_eia_wednesday"], "EIA Wednesday", "other days")
    snaps.to_csv(out / "snapshots.csv", index=False)

    md = ["# Kalshi WTI vs OVX - results\n",
          f"Events: {snaps['event_ticker'].nunique()} | contract snapshots: {len(snaps)} | "
          f"series: {', '.join(sorted(snaps['series'].unique()))} | "
          f"{snaps['settle_date'].min()} to {snaps['settle_date'].max()}\n"]

    # ---- Q1 width -------------------------------------------------------
    ev = snaps.drop_duplicates(["event_ticker", "hours_before"])[
        ["event_ticker", "series", "settle_date", "weekday", "day_type", "hours_before",
         "fit_sigma", "sigma_ovx", "sigma_rv", "fit_rmse"]].copy()
    ev["ratio_kalshi_to_ovx"] = ev["fit_sigma"] / ev["sigma_ovx"]
    md.append("## Q1. Width: Kalshi-implied vol vs OVX\n")
    md.append("Ratio > 1 means Kalshi prices a wider spread of outcomes than the options market.\n")
    md.append(ev.groupby(["series", "hours_before"])["ratio_kalshi_to_ovx"]
              .describe()[["count", "25%", "50%", "75%"]].round(2).to_markdown() + "\n")
    md.append("By day type (median ratio):\n")
    md.append(ev.groupby(["series", "day_type"])["ratio_kalshi_to_ovx"].median().round(2)
              .to_frame().to_markdown() + "\n")

    fig, ax = plt.subplots(figsize=(9, 4))
    for s, g in ev[ev["hours_before"] == args.trade_hours].groupby("series"):
        g = g.sort_values("settle_date")
        ax.plot(pd.to_datetime(g["settle_date"]), g["fit_sigma"] * 100, marker=".", lw=1, label=f"{s} implied")
    o = ev.drop_duplicates("settle_date").sort_values("settle_date")
    ax.plot(pd.to_datetime(o["settle_date"]), o["sigma_ovx"] * 100, color="black", lw=1.5, label="OVX")
    ax.set_ylabel("annualised vol (%)")
    ax.set_title(f"Kalshi-implied vol ({args.trade_hours:g}h before close) vs OVX")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out / "q1_width.png", dpi=150)
    plt.close(fig)

    # ---- Q2 accuracy ----------------------------------------------------
    md.append("## Q2. Accuracy (Brier score, lower is better)\n")
    md.append(score_table(snaps).round(4).to_markdown(index=False) + "\n")
    md.append("By distance from the centre:\n")
    md.append(score_table(snaps, "bucket").round(4).to_markdown(index=False) + "\n")
    md.append("EIA Wednesdays vs other days:\n")
    md.append(score_table(snaps, "day_type").round(4).to_markdown(index=False) + "\n")

    bins = [0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, 1.0001]
    snaps["price_bin"] = pd.cut(snaps["mid"], bins, right=False)
    cal = snaps.groupby("price_bin", observed=True).agg(n=("y", "size"), avg_price=("mid", "mean"),
                                                        hit_rate=("y", "mean")).round(3)
    md.append("Calibration of Kalshi mids (is there a longshot bias in oil?):\n")
    md.append(cal.to_markdown() + "\n")
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    ax.plot([0, 1], [0, 1], color="grey", lw=1)
    ax.scatter(cal["avg_price"], cal["hit_rate"], s=np.sqrt(cal["n"]) * 6)
    ax.set_xlabel("Kalshi mid price")
    ax.set_ylabel("share that settled YES")
    ax.set_title("Calibration")
    fig.tight_layout()
    fig.savefig(out / "q2_calibration.png", dpi=150)
    plt.close(fig)

    # ---- Q3 edge after costs -------------------------------------------
    md.append(f"## Q3. Backtest at {args.trade_hours:g}h before close (taker fees, "
              f"{args.contracts}-contract orders, +{args.buffer:.2f} buffer)\n")
    tradeable = snaps[snaps["hours_before"] == args.trade_hours]
    all_trades = []
    models = [("p_ovx_ext", "OVX width + futures centre"), ("p_rv_ext", "realised vol + futures centre"),
              ("p_ovx", "OVX width + Kalshi centre")]
    for p_col, label in [(c, l) for c, l in models if c in tradeable.columns]:
        t = backtest(tradeable, p_col, args.buffer, args.contracts)
        if not t.empty:
            t["model"] = label
            all_trades.append(t)
        md.append(f"**Model: {label}**\n")
        md.append(pnl_summary(t).round(4).to_markdown(index=False) + "\n")
        if not t.empty:
            md.append(pnl_summary(t, "bucket").round(4).to_markdown(index=False) + "\n")
            md.append(pnl_summary(t, "day_type").round(4).to_markdown(index=False) + "\n")
    if all_trades:
        pd.concat(all_trades).to_csv(out / "trades.csv", index=False)

    md.append("\n_Caveats: OVX is 30-day implied vol on USO options, not a daily WTI option; "
              "candle bid/ask are hourly closes, not fills; fees assume the general schedule "
              "(check the series' fee multiplier); a small sample means wide error bars._\n")
    (out / "summary.md").write_text("\n".join(md))
    print("\n".join(md))
    print(f"\nWrote {out}/summary.md and charts.")


if __name__ == "__main__":
    main()
