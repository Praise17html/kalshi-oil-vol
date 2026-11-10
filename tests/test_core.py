import math
from datetime import datetime, timezone

import numpy as np
import pytest

from oilvol import model
from oilvol.dataset import ET, globex_open, tau_years
from oilvol.fred import ewma_realised_vol, parse_fred_csv
from oilvol.kalshi import parse_candles


def test_prob_above_basic():
    p = model.prob_above(80.0, np.array([70.0, 80.0, 90.0]), 0.4, 1 / 252)
    assert p[0] > 0.99 and p[2] < 0.01
    assert 0.45 < p[1] < 0.5  # slightly below 0.5 because of the -0.5*sigma^2*tau term


def test_ranges_and_tails_sum_to_one():
    F, sig, tau = 75.3, 0.45, 5 / 252
    strikes = [("less", None, 70.0)] + [("between", float(k), k + 0.99) for k in range(70, 81)] + [("greater", 80.99, None)]
    total = sum(model.contract_prob(st, fl, cp, F, sig, tau) for st, fl, cp in strikes)
    assert total == pytest.approx(1.0, abs=1e-6)


def test_fit_recovers_centre_and_width():
    F, sig, tau = 72.4, 0.55, 1 / 252
    st = ["greater"] * 15
    fl = [65.99 + k for k in range(15)]
    mids = model.ladder_probs(st, fl, [None] * 15, F, sig, tau)
    fit = model.fit_implied(st, fl, [None] * 15, mids, tau)
    assert fit.ok
    assert fit.F == pytest.approx(F, rel=1e-3)
    assert fit.sigma == pytest.approx(sig, rel=1e-2)


def test_taker_fee_matches_published_formula():
    # 10 contracts at 50c: 0.07*10*0.25 = 0.175 -> rounds up to $0.18 -> 1.8c per contract
    assert model.taker_fee_per_contract(0.50, contracts=10) == pytest.approx(0.018)
    # 1 contract at 5c: 0.07*0.05*0.95 = 0.0033 -> rounds up to 1c
    assert model.taker_fee_per_contract(0.05, contracts=1) == pytest.approx(0.01)
    assert model.maker_fee_per_contract(0.5) == 0.0


def test_parse_live_and_historical_candles():
    live = [{"end_period_ts": 1, "yes_bid": {"close_dollars": "0.4100"}, "yes_ask": {"close_dollars": "0.4300"},
             "price": {"close_dollars": None}, "volume_fp": "12.00", "open_interest_fp": "50.00"}]
    hist = [{"end_period_ts": 2, "yes_bid": {"close": "0.1000"}, "yes_ask": {"close": "0.1200"},
             "price": {"close": "0.11"}, "volume": "3.00", "open_interest": "9.00"}]
    a, b = parse_candles("X", live), parse_candles("Y", hist)
    assert a.loc[0, "bid"] == 0.41 and a.loc[0, "ask"] == 0.43 and math.isnan(a.loc[0, "last"])
    assert b.loc[0, "bid"] == 0.10 and b.loc[0, "volume"] == 3.0


def test_globex_hours():
    sat = datetime(2026, 11, 7, 12, tzinfo=ET).astimezone(timezone.utc)
    sun_eve = datetime(2026, 11, 8, 19, tzinfo=ET).astimezone(timezone.utc)
    break_hr = datetime(2026, 11, 10, 17, 30, tzinfo=ET).astimezone(timezone.utc)
    assert not globex_open(sat) and globex_open(sun_eve) and not globex_open(break_hr)


def test_tau_skips_weekend():
    fri = int(datetime(2026, 11, 6, 14, 30, tzinfo=ET).timestamp())
    mon = int(datetime(2026, 11, 9, 14, 30, tzinfo=ET).timestamp())
    tue = int(datetime(2026, 11, 10, 14, 30, tzinfo=ET).timestamp())
    assert tau_years(fri, mon) == pytest.approx(tau_years(mon, tue), rel=0.05)


def test_fred_parsing_handles_gaps():
    s = parse_fred_csv("observation_date,OVXCLS\n2026-11-02,45.1\n2026-11-03,.\n2026-11-04,47.0\n", "OVXCLS")
    assert len(s) == 2 and s.iloc[-1] == 47.0
    rv = ewma_realised_vol(parse_fred_csv("DATE,X\n2026-01-01,70\n2026-01-02,71\n2026-01-05,70.5\n", "X"))
    assert (rv > 0).all()


def test_get_retries_after_timeout(monkeypatch, tmp_path):
    import requests
    from oilvol import kalshi

    calls = {"n": 0}

    class Resp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"ok": True}

    def flaky(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ReadTimeout("Read timed out.")
        return Resp()

    monkeypatch.setattr(kalshi.time, "sleep", lambda s: None)
    c = kalshi.KalshiClient(cache_dir=tmp_path, pause=0)
    monkeypatch.setattr(c.session, "get", flaky)
    assert c.get("/markets") == {"ok": True} and calls["n"] == 3


def test_eia_release_days_follow_holiday_schedule():
    from oilvol import eia
    assert eia.is_release_day("2026-11-04")        # normal Wednesday
    assert not eia.is_release_day("2026-11-11")    # Veterans Day week: no Wednesday release
    assert eia.is_release_day("2026-11-12")        # ...it moves to Thursday
    assert not eia.is_release_day("2026-11-05")    # ordinary Thursday
