"""Is oil really more volatile on EIA report days, and does Kalshi price that in?

The report is usually Wednesday 10:30 am ET, moved to Thursday in holiday weeks (see oilvol/eia.py).

For every Kalshi WTI ladder snapshot (from analyse.py), this measures how far WTI futures
actually moved between the snapshot and the 2:30 pm ET settle, then compares three numbers,
split by the weekday the contract settles:

  realised  - the volatility that actually happened (futures move, annualised on the same clock)
  Kalshi    - the volatility Kalshi's ladder priced at the snapshot
  OVX       - the volatility the options market implied that day

If Wednesdays move much more than other days but Kalshi prices them about the same, that is
direct evidence for the EIA-day finding, without needing any more Kalshi data.

    python analyse.py            # first, so outputs/snapshots.csv exists
    python wednesday_check.py
Writes outputs/wednesday_check.md and outputs/wednesday_check.png
"""
from __future__ import annotations

import argparse
from datetime import datetime, time, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from oilvol import eia, futures
from oilvol.dataset import ET, tau_years

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def settle_ts(settle_date: str) -> int:
    d = datetime.fromisoformat(settle_date).date()
    return int(datetime.combine(d, time(14, 30), tzinfo=ET).astimezone(timezone.utc).timestamp())


def rms(x) -> float:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else float("nan")


def build(snaps: pd.DataFrame, center: pd.Series) -> pd.DataFrame:
    ev = snaps[snaps["fit_ok"] == True].drop_duplicates(["event_ticker", "hours_before"]).copy()  # noqa: E712
    rows = []
    for r in ev.itertuples():
        end = settle_ts(r.settle_date)
        start = int(r.snap_ts)
        tau_settle = tau_years(start, end)
        p0, p1 = futures.price_asof(center, start), futures.price_asof(center, end)
        if not (tau_settle > 0 and p0 > 0 and p1 > 0):
            continue
        move = np.log(p1 / p0)
        rows.append({
            "event_ticker": r.event_ticker, "series": r.series, "hours_before": r.hours_before,
            "settle_date": r.settle_date, "weekday": r.weekday, "is_wed": eia.is_release_day(r.settle_date),
            "move_pct": move * 100,
            "realised": abs(move) / np.sqrt(tau_settle),            # annualised, same clock
            # Kalshi's width was fitted to the market's close time; re-express it to the settle
            "kalshi": r.fit_sigma * np.sqrt(r.tau / tau_settle),
            "ovx": r.sigma_ovx,
        })
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    g = df.groupby(by)
    out = pd.DataFrame({
        "days": g.size(),
        "realised vol %": g["realised"].apply(rms) * 100,
        "Kalshi priced %": g["kalshi"].apply(rms) * 100,
        "OVX %": g["ovx"].apply(rms) * 100,
    })
    out["realised / Kalshi"] = out["realised vol %"] / out["Kalshi priced %"]
    out["realised / OVX"] = out["realised vol %"] / out["OVX %"]
    return out.round(2)


def uplift(df: pd.DataFrame, col: str, n_boot: int = 4000, seed: int = 1):
    """EIA-report-day / other-days ratio of RMS volatility, with a bootstrap 90% interval."""
    wed, oth = df.loc[df["is_wed"], col].to_numpy(), df.loc[~df["is_wed"], col].to_numpy()
    if len(wed) < 3 or len(oth) < 3:
        return np.nan, np.nan, np.nan
    point = rms(wed) / rms(oth)
    rng = np.random.default_rng(seed)
    boots = [rms(rng.choice(wed, len(wed))) / rms(rng.choice(oth, len(oth))) for _ in range(n_boot)]
    lo, hi = np.percentile(boots, [5, 95])
    return point, lo, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="outputs")
    args = ap.parse_args()
    out = Path(args.out)

    snaps_path = out / "snapshots.csv"
    if not snaps_path.exists():
        raise SystemExit(f"{snaps_path} not found - run python analyse.py first.")
    center = futures.load_center(args.data)
    if center is None:
        raise SystemExit("No futures file - run: python -c \"from oilvol import futures; futures.download_cl_hourly('data')\"")
    snaps = pd.read_csv(snaps_path)

    closes = pd.to_datetime(snaps.drop_duplicates("event_ticker")["close_ts"], unit="s", utc=True)
    close_clock = closes.dt.tz_convert(ET).dt.strftime("%H:%M").value_counts().head(5)

    df = build(snaps, center)
    if df.empty:
        raise SystemExit("No snapshots could be matched to futures prices.")
    df.to_csv(out / "wednesday_check.csv", index=False)
    daily = df[df["series"] == "KXWTI"]

    md = ["# Wednesday check: realised vs priced volatility\n",
          f"{df['event_ticker'].nunique()} events matched to CL=F futures, "
          f"{df['settle_date'].min()} to {df['settle_date'].max()}.\n",
          "Kalshi market close times (ET), most common: "
          + ", ".join(f"{k} ({v})" for k, v in close_clock.items()) + "\n"]

    md.append("## The headline: how much bigger are EIA-report-day moves, and does Kalshi price it?\n")
    md.append("Ratio of volatility on EIA report days to other days (1.00 = no difference), daily KXWTI "
              "ladder, with a bootstrap 90% interval. Report days are Wednesdays, or Thursdays in holiday weeks:\n")
    rows = []
    for h in sorted(daily["hours_before"].unique(), reverse=True):
        d = daily[daily["hours_before"] == h]
        for col, label in [("realised", "Actually happened (futures)"), ("kalshi", "Kalshi priced"),
                           ("ovx", "OVX implied")]:
            p, lo, hi = uplift(d, col)
            rows.append({"snapshot": f"{h:g}h before", "measure": label, "EIA day / other days": round(p, 2),
                         "90% interval": f"{lo:.2f} to {hi:.2f}"})
    md.append(pd.DataFrame(rows).to_markdown(index=False) + "\n")

    md.append("## By weekday (daily KXWTI)\n")
    md.append("realised / Kalshi above 1 means the market under-priced the moves that actually came.\n")
    for h in sorted(daily["hours_before"].unique(), reverse=True):
        t = summarise(daily[daily["hours_before"] == h], ["weekday"]).reindex(WEEKDAYS).dropna(how="all")
        md.append(f"**{h:g} hours before settle**\n")
        md.append(t.to_markdown() + "\n")

    if (df["series"] == "KXWTIW").any():
        md.append("## Weekly ladder (KXWTIW, settles Fridays; the window includes Wednesday's report)\n")
        md.append(summarise(df[df["series"] == "KXWTIW"], ["hours_before"]).to_markdown() + "\n")

    md.append("\n_Notes: futures prices are hourly closes (the bar ending at or before each time), so moves in "
              "the last half hour before the 2:30 pm settle are missed; CL=F is the continuous front month and "
              "can differ from Kalshi's contract for a couple of days around each monthly roll. Each Wednesday is "
              "one observation per snapshot time, so intervals stay wide until the sample grows._\n")
    (out / "wednesday_check.md").write_text("\n".join(md))

    h_plot = max(daily["hours_before"].unique())
    t = summarise(daily[daily["hours_before"] == h_plot], ["weekday"]).reindex(WEEKDAYS).dropna(how="all")
    fig, ax = plt.subplots(figsize=(7.5, 4))
    x = np.arange(len(t))
    w = 0.27
    ax.bar(x - w, t["realised vol %"], w, label="Actually happened", color="#1D9E75")
    ax.bar(x, t["Kalshi priced %"], w, label="Kalshi priced", color="#D85A30")
    ax.bar(x + w, t["OVX %"], w, label="OVX implied", color="#888780")
    ax.set_xticks(x, t.index)
    ax.set_ylabel("annualised volatility (%)")
    ax.set_title(f"Oil volatility by settle day, {h_plot:g}h before settle (daily ladder)")
    ax.legend(frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "wednesday_check.png", dpi=150)
    plt.close(fig)

    print("\n".join(md))
    print(f"\nWrote {out}/wednesday_check.md and {out}/wednesday_check.png")


if __name__ == "__main__":
    main()
