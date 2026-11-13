"""Tests for the pre-trade guards (scripts/guards.py).

These pin down the refusals, because a guard that silently stops working is
worse than no guard: you would believe you were protected. All checks are pure
functions — no broker, no network.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts import guards


# --------------------------------------------------------------------------- #
# Kill switch
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on", "paused"])
def test_kill_switch_recognises_common_spellings(monkeypatch, value):
    monkeypatch.setenv("TRADING_PAUSED", value)
    assert guards.trading_paused() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_kill_switch_is_off_when_unset_or_false(monkeypatch, value):
    monkeypatch.setenv("TRADING_PAUSED", value)
    assert guards.trading_paused() is False


def test_kill_switch_blocks_everything(monkeypatch):
    monkeypatch.setenv("TRADING_PAUSED", "true")
    res = guards.evaluate(target={"SPY": 0.98})
    assert res.ok is False
    assert any("TRADING_PAUSED" in b for b in res.blockers)


# --------------------------------------------------------------------------- #
# Account identity and state
# --------------------------------------------------------------------------- #
def _account(**over):
    base = {"account_number": "PA3TEST123", "status": "ACTIVE", "equity": "100000.00",
            "trading_blocked": False, "account_blocked": False}
    base.update(over)
    return base


def test_account_guard_accepts_a_healthy_account():
    res = guards.account_guard(_account())
    assert res.ok
    assert res.notes["account_number"] == "PA3TEST123"


def test_account_guard_blocks_a_different_account():
    """The failure mode that actually costs money: a rotated key writing elsewhere."""
    res = guards.account_guard(_account(), expected_account="PA3OTHER999")
    assert not res.ok
    assert "account mismatch" in res.blockers[0]


def test_account_guard_blocks_blocked_flags():
    res = guards.account_guard(_account(trading_blocked=True))
    assert not res.ok
    assert any("trading_blocked" in b for b in res.blockers)


def test_account_guard_blocks_non_active_status_and_zero_equity():
    assert not guards.account_guard(_account(status="ACCOUNT_CLOSED")).ok
    assert not guards.account_guard(_account(equity="0")).ok


def test_unpinned_account_identity_is_a_warning_not_a_block():
    res = guards.account_guard(_account())
    assert res.ok
    assert any("APCA_EXPECTED_ACCOUNT_ID" in w for w in res.warnings)


# --------------------------------------------------------------------------- #
# Target sanity
# --------------------------------------------------------------------------- #
def test_target_guard_accepts_the_real_strategy_output():
    res = guards.target_guard({"BIL": 0.539, "SPY": 0.441},
                              universe={"SPY", "BIL", "QQQ"})
    assert res.ok
    assert res.notes["target_total"] == "0.9800"


@pytest.mark.parametrize("bad,needle", [
    ({}, "empty"),
    ({"SPY": 1.2}, "borrow"),
    ({"SPY": -0.2, "BIL": 0.5}, "negative"),
    ({"SPY": 0.8}, "cap"),
    ({"TSLA": 0.5}, "outside the universe"),
])
def test_target_guard_blocks_broken_weights(bad, needle):
    res = guards.target_guard(bad, universe={"SPY", "BIL"})
    assert not res.ok
    assert any(needle in b for b in res.blockers)


# --------------------------------------------------------------------------- #
# Data freshness
# --------------------------------------------------------------------------- #
def test_data_guard_accepts_a_fresh_signal_and_a_weekend_gap():
    assert guards.data_guard("2026-11-13", "2026-11-13").ok
    assert guards.data_guard("2026-11-13", "2026-11-15").ok      # weekend


def test_data_guard_blocks_stale_and_future_signals():
    stale = guards.data_guard("2026-11-01", "2026-11-13")
    future = guards.data_guard("2026-12-01", "2026-11-13")
    assert not stale.ok and "stale" in stale.blockers[0]
    assert not future.ok and "future" in future.blockers[0]


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_fingerprint_is_stable_and_short():
    a, b = guards.code_fingerprint(), guards.code_fingerprint()
    assert a == b
    assert len(a) == 16


def test_provenance_rows_are_appended_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(guards, "LOGS", tmp_path)
    for action in ("allowed", "allowed", "blocked"):
        guards.write_provenance(code_sha="abc123", account_number="PA3X", action=action)
    df = pd.read_csv(tmp_path / "provenance.csv")
    assert len(df) == 3
    assert list(df["action"]) == ["allowed", "allowed", "blocked"]


# --------------------------------------------------------------------------- #
# Combined verdict
# --------------------------------------------------------------------------- #
def test_evaluate_combines_every_guard():
    res = guards.evaluate(
        info=_account(trading_blocked=True),
        target={"SPY": 1.5},
        universe={"SPY"},
        signal_date="2026-11-01",
        today_ny="2026-11-13",
    )
    assert res.ok is False
    joined = " ".join(res.blockers)
    assert "trading_blocked" in joined
    assert "borrow" in joined
    assert "stale" in joined
    assert res.notes["code_sha"]
