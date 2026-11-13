"""Tests for the live report (scripts/report.py).

The report is the artefact the judges read, and it is regenerated automatically
every evening. These tests pin down the two things that went wrong in practice:

  1. On the first evening the summary claimed "Equity $0.00 -> $0.00", because
     the early return for a short sample skipped the fields the summary prints.
  2. Annualising a handful of days produced Sharpe 5+ out of pure luck.

Everything here is synthetic and offline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.report import MIN_SESSIONS_FOR_ANNUALISED, performance


def _curve(values):
    idx = pd.bdate_range("2026-11-13", periods=len(values), name="date")
    return pd.Series([float(v) for v in values], index=idx)


def test_one_snapshot_reports_the_real_balance():
    """Day one: the balance is real, the ratios are not."""
    stats = performance(_curve([100_000.0]))
    assert stats["snapshots"] == 1
    assert stats["sessions"] == 0
    assert stats["start_equity"] == 100_000.0
    assert stats["end_equity"] == 100_000.0
    assert stats["total_return"] == 0.0
    assert stats["max_drawdown"] == 0.0
    assert stats["provisional"] is True
    # Withheld on purpose — no Sharpe from zero observations.
    assert "sharpe" not in stats


def test_three_snapshots_are_still_provisional():
    stats = performance(_curve([100_000.0, 100_400.0, 100_100.0]))
    assert stats["sessions"] == 2
    assert stats["provisional"] is True
    assert "sharpe" not in stats
    assert stats["total_return"] == pytest.approx(0.001)


def test_ratios_appear_only_after_the_minimum_sample():
    rng = np.random.default_rng(7)
    # One extra snapshot: `sessions` counts *returns*, which is always one fewer.
    values = 100_000.0 * np.cumprod(1 + rng.normal(0.0004, 0.004, MIN_SESSIONS_FOR_ANNUALISED + 1))
    stats = performance(_curve(values))
    assert stats["sessions"] == MIN_SESSIONS_FOR_ANNUALISED
    assert stats["provisional"] is False
    for key in ("sharpe", "sortino", "calmar", "annualised_return", "annualised_vol"):
        assert key in stats


def test_drawdown_is_measured_from_the_peak():
    stats = performance(_curve([100_000.0, 110_000.0, 99_000.0, 101_000.0]))
    assert stats["max_drawdown"] == pytest.approx(-0.10, abs=1e-9)
