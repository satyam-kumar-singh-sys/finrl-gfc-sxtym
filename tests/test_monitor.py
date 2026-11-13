"""Automatic checks for the health monitor (scripts/monitor.py).

A non-coder's summary: these prove the overseer shouts in the right situations
and stays quiet in the right ones — stale data fails, normal data passes, a
modest drawdown passes, a catastrophic one fails, and a negative position in the
target vector is caught.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from scripts import monitor


def _equity(values, end="2026-11-12"):
    idx = pd.bdate_range(end=end, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_fresh_data_passes_and_stale_data_fails():
    fresh = _equity([100000, 100100], end="2026-11-12")
    today = datetime(2026, 11, 13, tzinfo=timezone.utc)
    assert monitor.check_freshness(fresh, today).ok is True
    stale = _equity([100000, 100100], end="2026-10-01")
    assert monitor.check_freshness(stale, today).ok is False


def test_missing_equity_is_a_failure_not_a_crash():
    check = monitor.check_freshness(None)
    assert check.ok is False and "no equity snapshots" in check.detail


def test_cadence_requires_recent_decisions():
    today = datetime(2026, 11, 13, tzinfo=timezone.utc)
    busy = pd.DataFrame({"time_utc": [f"2026-11-{d:02d}T14:45:00+00:00" for d in range(1, 13)]})
    assert monitor.check_cadence(busy, today).ok is True
    quiet = pd.DataFrame({"time_utc": ["2026-09-01T14:45:00+00:00"]})
    assert monitor.check_cadence(quiet, today).ok is False


def test_drawdown_alerts_only_beyond_the_backtest_budget():
    # a normal bad patch: -8% peak to trough -> inside the budget
    mild = _equity([100000, 108000, 99400, 100000])
    assert monitor.check_drawdown(mild).ok is True
    # a crisis: -40% -> outside 1.5x the backtest's -12.4%
    crash = _equity([100000, 100000, 60000, 62000])
    assert monitor.check_drawdown(crash).ok is False


def test_signal_sanity_rejects_negative_weights():
    good = pd.DataFrame({"target": ['{"SPY": 0.44, "BIL": 0.54}']})
    assert monitor.check_signal_sanity(good).ok is True
    bad = pd.DataFrame({"target": ['{"SPY": 1.4, "BIL": -0.4}']})
    assert monitor.check_signal_sanity(bad).ok is False


def test_no_trades_after_a_declared_live_start_fails():
    empty = pd.DataFrame()
    assert monitor.check_trading_actually_happens(empty, live_start="2026-11-01").ok is False
    trades = pd.DataFrame({"status": ["filled", "filled"]})
    assert monitor.check_trading_actually_happens(trades, live_start="2026-11-01").ok is True


def test_cadence_is_forgiving_during_ramp_up():
    """Day two of live trading must not raise a false alarm.

    Regression guard: the steady-state quota is six decisions per fourteen days.
    On the first evening the log holds one row, and a naive check would open a
    GitHub issue — every evening for a week. In ramp-up we only ask that the
    workflow is alive.
    """
    import pandas as pd
    from datetime import datetime, timezone
    from scripts.monitor import check_cadence

    day_two = pd.DataFrame({"time_utc": [
        "2026-11-12T02:50:34+00:00",
        "2026-11-13T14:45:02+00:00",
    ]})
    res = check_cadence(day_two, today=datetime(2026, 11, 13, 21, 45, tzinfo=timezone.utc))
    assert res.ok, res.detail
    assert "ramp-up" in res.detail


def test_cadence_still_fails_when_ramp_up_bot_is_dead():
    """No decision for a week during ramp-up is a real failure."""
    import pandas as pd
    from datetime import datetime, timezone
    from scripts.monitor import check_cadence

    stale = pd.DataFrame({"time_utc": ["2026-11-01T14:45:02+00:00"]})
    res = check_cadence(stale, today=datetime(2026, 11, 13, 21, 45, tzinfo=timezone.utc))
    assert not res.ok


def test_cadence_uses_full_quota_after_two_weeks():
    """Once 14 days of history exist, the six-per-fortnight quota applies."""
    import pandas as pd
    from datetime import datetime, timezone
    from scripts.monitor import check_cadence

    thin = pd.DataFrame({"time_utc": [
        "2026-10-01T14:45:02+00:00",
        "2026-11-10T14:45:02+00:00",
        "2026-11-12T14:45:02+00:00",
    ]})
    res = check_cadence(thin, today=datetime(2026, 11, 13, 21, 45, tzinfo=timezone.utc))
    assert not res.ok
    assert "want >= 6" in res.detail


def test_future_dated_snapshot_is_a_failure():
    """Negative age must not be read as "extra fresh"."""
    import pandas as pd
    from datetime import datetime, timezone
    from scripts.monitor import check_freshness

    eq = pd.Series([100_000.0], index=pd.to_datetime(["2026-12-31"]))
    res = check_freshness(eq, today=datetime(2026, 11, 13, tzinfo=timezone.utc))
    assert not res.ok
    assert "FUTURE" in res.detail


def test_unreadable_target_row_does_not_crash_the_monitor():
    """The overseer reports corruption; it does not die on it."""
    import pandas as pd
    from scripts.monitor import check_signal_sanity

    bad = pd.DataFrame({"target": ['{"SPY": 0.4} {"BIL": 0.6}']})
    res = check_signal_sanity(bad)
    assert not res.ok
    assert "unreadable" in res.detail
