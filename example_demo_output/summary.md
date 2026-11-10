# Kalshi WTI vs OVX - results

Events: 136 | contract snapshots: 5268 | series: KXWTI, KXWTIW | 2026-06-02 to 2026-11-06

## Q1. Width: Kalshi-implied vol vs OVX

Ratio > 1 means Kalshi prices a wider spread of outcomes than the options market.

|                |   count |   25% |   50% |   75% |
|:---------------|--------:|------:|------:|------:|
| ('KXWTI', 6)   |     100 |  1.19 |  1.26 |  1.37 |
| ('KXWTI', 24)  |     113 |  1.13 |  1.22 |  1.32 |
| ('KXWTIW', 6)  |      21 |  1.17 |  1.24 |  1.36 |
| ('KXWTIW', 24) |      22 |  1.19 |  1.28 |  1.34 |

By day type (median ratio):

|                            |   ratio_kalshi_to_ovx |
|:---------------------------|----------------------:|
| ('KXWTI', 'EIA Wednesday') |                  1.45 |
| ('KXWTI', 'other days')    |                  1.21 |
| ('KXWTIW', 'other days')   |                  1.26 |

## Q2. Accuracy (Brier score, lower is better)

| group   |    n |   Brier: Kalshi mid |   Brier: OVX width + Kalshi centre |   Brier: OVX width + futures centre |   Brier: Realised vol + futures centre |   Brier: Lognormal fit to Kalshi |
|:--------|-----:|--------------------:|-----------------------------------:|------------------------------------:|---------------------------------------:|---------------------------------:|
| all     | 4743 |              0.0329 |                             0.0334 |                              0.0334 |                                 0.0333 |                           0.0327 |

By distance from the centre:

| group                 |    n |   Brier: Kalshi mid |   Brier: OVX width + Kalshi centre |   Brier: OVX width + futures centre |   Brier: Realised vol + futures centre |   Brier: Lognormal fit to Kalshi |
|:----------------------|-----:|--------------------:|-----------------------------------:|------------------------------------:|---------------------------------------:|---------------------------------:|
| centre (<0.5 sd)      |  321 |              0.226  |                             0.2276 |                              0.2276 |                                 0.2284 |                           0.2261 |
| shoulder (0.5-1.5 sd) |  528 |              0.1311 |                             0.1354 |                              0.1353 |                                 0.1345 |                           0.131  |
| tail (>1.5 sd)        | 3894 |              0.0036 |                             0.0035 |                              0.0035 |                                 0.0036 |                           0.0035 |

EIA Wednesdays vs other days:

| group         |    n |   Brier: Kalshi mid |   Brier: OVX width + Kalshi centre |   Brier: OVX width + futures centre |   Brier: Realised vol + futures centre |   Brier: Lognormal fit to Kalshi |
|:--------------|-----:|--------------------:|-----------------------------------:|------------------------------------:|---------------------------------------:|---------------------------------:|
| EIA Wednesday |  945 |              0.0416 |                             0.0441 |                              0.0442 |                                 0.0452 |                           0.0414 |
| other days    | 3798 |              0.0307 |                             0.0307 |                              0.0307 |                                 0.0304 |                           0.0306 |

Calibration of Kalshi mids (is there a longshot bias in oil?):

| price_bin   |    n |   avg_price |   hit_rate |
|:------------|-----:|------------:|-----------:|
| [0.0, 0.05) | 2247 |       0.017 |      0.002 |
| [0.05, 0.1) |  168 |       0.071 |      0.03  |
| [0.1, 0.2)  |  199 |       0.147 |      0.156 |
| [0.2, 0.3)  |  139 |       0.248 |      0.216 |
| [0.3, 0.4)  |   95 |       0.353 |      0.284 |
| [0.4, 0.5)  |   76 |       0.445 |      0.487 |
| [0.5, 0.6)  |   65 |       0.553 |      0.508 |
| [0.6, 0.7)  |   75 |       0.646 |      0.587 |
| [0.7, 0.8)  |   69 |       0.744 |      0.681 |
| [0.8, 0.9)  |  111 |       0.85  |      0.829 |
| [0.9, 0.95) |   93 |       0.925 |      0.903 |
| [0.95, 1.0) | 1931 |       0.988 |      0.997 |

## Q3. Backtest at 24h before close (taker fees, 10-contract orders, +0.02 buffer)

**Model: OVX width + futures centre**

| group   |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:--------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| all     |      238 |       73 |                     -0.0246 |               -5.852 |     0.8193 |             -0.8914 |

| group                 |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:----------------------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| centre (<0.5 sd)      |       27 |       23 |                     -0.0137 |               -0.369 |     0.4815 |             -0.1572 |
| shoulder (0.5-1.5 sd) |      172 |       70 |                     -0.0224 |               -3.857 |     0.8488 |             -0.7909 |
| tail (>1.5 sd)        |       39 |       32 |                     -0.0417 |               -1.626 |     0.9231 |             -0.9787 |

| group         |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:--------------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| EIA Wednesday |      113 |       22 |                     -0.0868 |               -9.811 |     0.7788 |              -1.872 |
| other days    |      125 |       51 |                      0.0317 |                3.959 |     0.856  |               1.128 |

**Model: realised vol + futures centre**

| group   |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:--------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| all     |      237 |       64 |                     -0.0156 |               -3.706 |       0.73 |             -0.6025 |

| group                 |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:----------------------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| centre (<0.5 sd)      |       41 |       27 |                      0.0566 |                2.32  |     0.6341 |              0.9186 |
| shoulder (0.5-1.5 sd) |      152 |       58 |                     -0.0327 |               -4.971 |     0.7303 |             -1.1034 |
| tail (>1.5 sd)        |       44 |       33 |                     -0.024  |               -1.055 |     0.8182 |             -0.7723 |

| group         |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:--------------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| EIA Wednesday |       81 |       18 |                     -0.0618 |               -5.002 |     0.7778 |             -1.1637 |
| other days    |      156 |       46 |                      0.0083 |                1.296 |     0.7051 |              0.2973 |

**Model: OVX width + Kalshi centre**

| group   |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:--------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| all     |      264 |       83 |                     -0.0211 |               -5.576 |     0.8258 |             -0.8333 |

| group                 |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:----------------------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| centre (<0.5 sd)      |       28 |       23 |                      0.0019 |                0.052 |     0.5    |              0.023  |
| shoulder (0.5-1.5 sd) |      194 |       82 |                     -0.0212 |               -4.112 |     0.8505 |             -0.7912 |
| tail (>1.5 sd)        |       42 |       34 |                     -0.0361 |               -1.516 |     0.9286 |             -0.9097 |

| group         |   trades |   events |   mean P&L per contract ($) |   total ($/contract) |   win rate |   t-stat (by event) |
|:--------------|---------:|---------:|----------------------------:|---------------------:|-----------:|--------------------:|
| EIA Wednesday |      115 |       22 |                     -0.0791 |               -9.095 |     0.7826 |             -1.7775 |
| other days    |      149 |       61 |                      0.0236 |                3.519 |     0.8591 |              0.8821 |


_Caveats: OVX is 30-day implied vol on USO options, not a daily WTI option; candle bid/ask are hourly closes, not fills; fees assume the general schedule (check the series' fee multiplier); a small sample means wide error bars._
