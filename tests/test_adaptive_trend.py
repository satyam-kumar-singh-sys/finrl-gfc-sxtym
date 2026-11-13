"""Automatic checks that the strategy behaves correctly.
=====================================================

READ THIS FIRST IF YOU ARE NOT A CODER
--------------------------------------
These are "unit tests": small, automatic checks that each piece of the code does
what it claims. You do not need to read them to trust the bot, but you should
know what they prove, because they are the reason a reviewer can press one
button and see that the logic is not broken:

  * the measuring tools give the right answer on hand-checked numbers;
  * the percentages always add up to at most 100% (the bot can never borrow);
  * in a crash, the bot really does move the money into Treasury bills;
  * the same input always produces the same output (no hidden randomness);
  * the backtest charges fees and never earns more before costs than after.

They run offline, on made-up prices, in about two seconds, so they cannot be
thrown off by an internet hiccup or a market holiday.

HOW TO RUN THEM
---------------
    python -m pytest -q tests
      → "18 passed" means everything is fine.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.strategies.adaptive_trend import (
    ATAConfig,
    AdaptiveTrendAllocationStrategy,
    above_trend,
    build_strategy,
    enforce_caps,
    inverse_vol_weights,
    momentum,
    realized_vol,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    """A 6-year synthetic panel: trending core, choppy satellite, flat cash."""
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2018-01-01", periods=1500)
    data = {}
    specs = {
        "SPY": (0.00065, 0.0095), "QQQ": (0.00080, 0.0120), "GLD": (0.00030, 0.0080),
        "IEF": (0.00010, 0.0040), "TLT": (0.00005, 0.0100), "DBC": (0.00005, 0.0110),
        "BIL": (0.00008, 0.0002),
    }
    for sym, (mu, sigma) in specs.items():
        steps = rng.normal(mu, sigma, len(idx)).cumsum()
        data[sym] = 100 * np.exp(steps)
    return pd.DataFrame(data, index=idx)


@pytest.fixture()
def strategy() -> AdaptiveTrendAllocationStrategy:
    return AdaptiveTrendAllocationStrategy(ATAConfig())


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def test_momentum_matches_manual_computation():
    s = pd.Series([100.0, 101.0, 102.0, 105.0, 110.0, 121.0])
    assert momentum(s, window=2, skip=0) == pytest.approx(121 / 105 - 1)
    assert momentum(s, window=2, skip=1) == pytest.approx(110 / 102 - 1)


def test_momentum_returns_nan_when_history_is_short():
    assert np.isnan(momentum(pd.Series([1.0, 2.0]), window=126))


def test_above_trend_true_in_uptrend_false_in_downtrend():
    up = pd.Series(np.linspace(10, 20, 250))
    down = pd.Series(np.linspace(20, 10, 250))
    assert above_trend(up, 200) is True
    assert above_trend(down, 200) is False
    assert above_trend(pd.Series([1.0] * 10), 200) is False   # not enough history


def test_realized_vol_scales_with_volatility():
    calm = pd.Series(np.full(60, 0.001))
    wild = pd.Series(np.tile([0.02, -0.02], 30))
    assert realized_vol(calm) == pytest.approx(0.0, abs=1e-9)
    assert realized_vol(wild) > 0.2


def test_inverse_vol_weights_sum_to_budget_and_favour_calm_assets():
    w = inverse_vol_weights(["A", "B"], {"A": 0.10, "B": 0.20}, total=0.30)
    assert sum(w.values()) == pytest.approx(0.30)
    assert w["A"] > w["B"]


def test_inverse_vol_weights_ignore_bad_inputs():
    assert inverse_vol_weights(["A"], {"A": float("nan")}, 0.3) == {}


def test_enforce_caps_clips_names_and_respects_cash_floor():
    w = enforce_caps({"SPY": 0.9, "BIL": 0.05}, max_weight=0.45, min_cash=0.02, cash_symbol="BIL")
    assert w["SPY"] <= 0.45 + 1e-9
    assert sum(w.values()) <= 0.98 + 1e-9


# --------------------------------------------------------------------------- #
# Strategy contract
# --------------------------------------------------------------------------- #
def test_weights_are_a_valid_long_only_portfolio(panel, strategy):
    res = strategy.compute(panel)
    w = res.weights
    assert {"symbol", "weight", "sleeve"}.issubset(w.columns)
    assert (w["weight"] > 0).all()
    assert w["weight"].sum() <= 1.0 + 1e-9
    assert w["weight"].sum() >= 0.95           # capital is deployed or in T-bills
    assert w["symbol"].is_unique


def test_sleeves_respect_the_configured_budget(panel):
    cfg = ATAConfig()
    res = AdaptiveTrendAllocationStrategy(cfg).compute(panel)
    cores = set(cfg.core_symbols())
    core_w = res.weights.loc[res.weights["symbol"].isin(cores), "weight"].sum()
    assert core_w <= sum(w for _, w in cfg.core) + 1e-9


def test_no_leverage_even_with_very_low_realised_volatility(panel):
    calm = panel.copy()
    for col in ("SPY", "QQQ"):
        calm[col] = np.linspace(100, 160, len(calm))       # zero-vol uptrend
    res = build_strategy(target_vol=0.30).compute(calm)
    assert res.weights["weight"].sum() <= 1.0 + 1e-9
    assert res.metadata["vol_scaler"] <= 1.0


def test_bear_market_moves_capital_out_of_the_core(panel):
    """When every sleeve is in a downtrend the book must hide in T-bills."""
    crash = panel.copy()
    for col in ("SPY", "QQQ", "GLD", "IEF", "TLT", "DBC"):
        crash[col] = np.linspace(200, 60, len(crash))
    res = AdaptiveTrendAllocationStrategy(ATAConfig()).compute(crash)
    assert res.metadata["risk_state"] == "defensive"
    assert res.metadata["core_weight"] == pytest.approx(0.0)
    assert res.weights.set_index("symbol").loc["BIL", "weight"] > 0.9


def test_metadata_documents_every_gate(panel, strategy):
    meta = strategy.compute(panel).metadata
    for key in ("as_of", "gates", "vol_scaler", "invested", "cash", "risk_state"):
        assert key in meta
    assert set(meta["gates"]) == set(ATAConfig().core_symbols()) | set(ATAConfig().satellites)


def test_results_are_deterministic(panel):
    a = AdaptiveTrendAllocationStrategy(ATAConfig()).compute(panel).weights
    b = AdaptiveTrendAllocationStrategy(ATAConfig()).compute(panel).weights
    pd.testing.assert_frame_equal(a, b)


def test_generate_weights_honours_target_date(panel, strategy):
    cutoff = panel.index[-100]
    res = strategy.generate_weights({"close": panel}, target_date=str(cutoff.date()))
    assert res.metadata["as_of"] == cutoff.strftime("%Y-%m-%d")


def test_generate_weights_rejects_missing_prices():
    with pytest.raises(KeyError):
        AdaptiveTrendAllocationStrategy(ATAConfig()).generate_weights({"vix": pd.Series([1.0])})


def test_vol_scaler_is_band_limited(panel):
    """The scaler only moves when the implied change exceeds the band."""
    strat = AdaptiveTrendAllocationStrategy(ATAConfig(vol_band=0.10))
    seen = []
    for i in range(260, len(panel), 21):
        seen.append(strat.compute(panel.iloc[:i]).metadata["vol_scaler"])
    jumps = np.abs(np.diff(seen))
    assert (jumps == 0).sum() > 0                        # it does hold sometimes
    assert (jumps[np.nonzero(jumps)] >= 0.10 - 1e-9).all()


# --------------------------------------------------------------------------- #
# Backtest plumbing
# --------------------------------------------------------------------------- #
def test_backtest_is_flag_free_and_charges_costs(panel):
    from scripts.backtest import apply_no_trade_band, build_targets, decision_dates, simulate

    dates = decision_dates(panel.index)
    targets = apply_no_trade_band(build_targets(panel, dates, ATAConfig()), 0.02)
    sim = simulate(panel, targets, lag=1, cost_bps=10.0)
    assert (sim["cost"] >= 0).all()
    assert sim["invested"].max() <= 1.0 + 1e-9
    assert np.isfinite(sim["ret"]).all()
    gross_sim = simulate(panel, targets, lag=1, cost_bps=0.0)
    assert gross_sim["ret"].sum() >= sim["ret"].sum()    # costs only ever hurt


def test_decision_dates_are_weekly(panel):
    from scripts.backtest import decision_dates

    dates = decision_dates(panel.index)
    assert 1 < len(dates) < len(panel)
    assert dates.is_monotonic_increasing
    assert set(pd.Series(dates).dt.day_of_week) <= {4}   # Fridays only
