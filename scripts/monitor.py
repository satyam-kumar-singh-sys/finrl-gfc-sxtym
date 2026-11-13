"""The overseer — a health check that watches the bot, not the market.
====================================================================

WHY THIS EXISTS
---------------
A trend strategy needs almost no supervision: it is meant to sit there for six
weeks and do one thing a week. What *does* need supervision is the machinery
around it — did the data arrive? did the run happen? is the account behaving
like the backtest said it would? Those failures are silent and expensive.

So this file is the "overseer" idea, reduced to the part that is statistically
honest: it checks the *plumbing* and the *risk budget*, never the returns. It
deliberately does NOT promote, demote or re-weight anything, because with ~20
daily observations in a 30-trading-day competition, a rule that reacts to
performance is just selecting on noise.

WHAT IT CHECKS
--------------
  1. FRESH DATA      — is there an equity snapshot from a recent US session?
  2. RUN CADENCE     — did decisions actually get logged over the last two weeks?
  3. DRAWDOWN BUDGET — has the account fallen further than the backtest's worst
                       case, with a safety multiple? (backtest: −12.4%)
  4. TRADING ACTUALITY — after the live start, did orders ever get placed?
  5. SIGNAL SANITY   — do the logged targets look like the real strategy
                       (long only, ≤100% invested, plausible cash)?

OUTPUT
------
    results/health.json     machine-readable status of every check
    results/health.md       a short human summary
    exit code 0 = all good, 1 = something needs a human

`--quiet` prints nothing on success, so it can run every day in CI.

Usage
-----
    python -m scripts.monitor
    python -m scripts.monitor --quiet
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LOGS = ROOT / "logs"

# The strategy's own worst case from the 2007-2026 backtest (−12.4%). We alert at
# 1.5x that, i.e. only when reality is materially worse than the model — not when
# the account has a normal bad week.
BACKTEST_MAX_DD = 0.124
DD_ALERT_MULTIPLE = 1.5
# A snapshot older than this many calendar days means the schedule stopped.
MAX_SNAPSHOT_AGE_DAYS = 5
# Expect roughly one decision per US session; be forgiving about holidays.
MIN_DECISIONS_PER_TWO_WEEKS = 6


@dataclass
class Check:
    """One named test with a pass/fail and a human explanation."""

    name: str
    ok: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


# --------------------------------------------------------------------------- #
# Individual checks (pure functions: easy to test, no network, no broker)
# --------------------------------------------------------------------------- #
def check_freshness(equity: Optional[pd.Series], today: Optional[datetime] = None) -> Check:
    """Is there a recent equity snapshot? (Catches a dead schedule.)"""
    if equity is None or equity.empty:
        return Check("fresh_data", False, "no equity snapshots found at all")
    # Work in naive UTC dates: the CSV has dates, the clock has a timezone.
    today = pd.Timestamp(today or datetime.now(timezone.utc)).tz_localize(None)
    last = pd.Timestamp(equity.index[-1]).tz_localize(None) if equity.index.tz is None else pd.Timestamp(equity.index[-1]).tz_convert(None)
    age = int((today.normalize() - last.normalize()).days)

    # A snapshot dated in the future is not "fresh", it is wrong: a clock skew,
    # a hand-edited CSV, or rows written in the wrong timezone. Age alone would
    # call that a pass (negative days <= tolerance), so catch it explicitly.
    if age < -1:
        return Check("fresh_data", False,
                     f"last snapshot {last.date()} is {-age} days in the FUTURE — "
                     "check the machine clock / timezone of whoever wrote it")
    ok = age <= MAX_SNAPSHOT_AGE_DAYS
    return Check("fresh_data", bool(ok),
                 f"last snapshot {last.date()} ({age} calendar "
                 f"{'day' if age == 1 else 'days'} old; tolerance {MAX_SNAPSHOT_AGE_DAYS})")


def check_cadence(decisions: Optional[pd.DataFrame], today: Optional[datetime] = None) -> Check:
    """Were decisions logged over the last two weeks? (Catches a broken workflow.)

    Two regimes, because asking for fourteen days of history on day two is a
    false alarm, and a false alarm every evening for the first week teaches you
    to ignore the alert — which is worse than having no alert at all.

      * RAMP-UP (fewer than 14 days of history): only ask "is the bot alive?" —
        at least one decision in the last five days.
      * STEADY STATE: the full quota, MIN_DECISIONS_PER_TWO_WEEKS.

    The switch is derived from the data itself (the first row in the log), so
    there is no extra setting to forget and nothing to change after week two.
    """
    if decisions is None or decisions.empty:
        return Check("run_cadence", False, "no decisions logged")

    today = pd.Timestamp(today or datetime.now(timezone.utc)).tz_localize(None)
    stamps = decisions["time_utc"].astype(str)

    # How old is the live record? (String slicing is safe: ISO timestamps sort.)
    try:
        first_day = pd.Timestamp(stamps.min()[:10])
    except (ValueError, TypeError):
        first_day = today
    days_of_history = int((today.normalize() - first_day).days) + 1

    if days_of_history < 14:
        alive_cutoff = (today - pd.Timedelta(days=5)).isoformat()
        alive = int((stamps >= alive_cutoff).sum())
        return Check("run_cadence", alive > 0,
                     f"ramp-up: day {days_of_history} of 14, {alive} decision"
                     f"{'' if alive == 1 else 's'} in the last 5 days "
                     f"(want >= 1 — just proving the workflow runs)")

    cutoff = (today - pd.Timedelta(days=14)).isoformat()
    recent = decisions[stamps >= cutoff]
    ok = len(recent) >= MIN_DECISIONS_PER_TWO_WEEKS
    return Check("run_cadence", bool(ok),
                 f"{len(recent)} decisions in the last 14 days "
                 f"(want >= {MIN_DECISIONS_PER_TWO_WEEKS})")


def check_drawdown(equity: Optional[pd.Series]) -> Check:
    """Is the loss still inside the backtested risk budget (with slack)?"""
    if equity is None or len(equity) < 2:
        return Check("drawdown_budget", True, "not enough history to measure yet")
    dd = float((equity / equity.cummax() - 1).min())
    limit = BACKTEST_MAX_DD * DD_ALERT_MULTIPLE
    ok = dd > -limit
    return Check("drawdown_budget", bool(ok),
                 f"worst drawdown {dd:.2%} (alert below -{limit:.1%} = "
                 f"{DD_ALERT_MULTIPLE}x the backtest's -{BACKTEST_MAX_DD:.1%})")


def check_trading_actually_happens(trades: Optional[pd.DataFrame],
                                   live_start: Optional[str] = None) -> Check:
    """After the live period begins, did orders ever reach the broker?"""
    if live_start is None:
        return Check("trading_activity", True, "live start not declared; skipped")
    if trades is None or trades.empty:
        return Check("trading_activity", False,
                     f"no orders logged since live start {live_start}")
    filled = trades[trades.get("status", "") != ""] if "status" in trades else trades
    return Check("trading_activity", True,
                 f"{len(trades)} orders logged ({len(filled)} with a status)")


def check_provenance(prov: Optional[pd.DataFrame]) -> Check:
    """Did the strategy code or the trading account change mid-run?

    One run, one code fingerprint and one account. If either changes after the
    live period started, the 30-day record is no longer a record of a single
    strategy on a single account — which is exactly the thing a judge would be
    right to distrust. Missing file is not a failure (older runs predate it).
    """
    if prov is None or prov.empty:
        return Check("provenance", True, "no provenance rows yet (pre-dates this check)")
    shas = [s for s in prov.get("code_sha", pd.Series(dtype=str)).astype(str).unique() if s]
    accounts = [a for a in prov.get("account_number", pd.Series(dtype=str)).astype(str).unique()
                if a and a != "unknown"]
    if len(shas) > 1:
        return Check("provenance", False,
                     f"strategy code changed mid-run: {len(shas)} fingerprints "
                     f"({', '.join(sorted(shas))}) — the 30-day window must use one version")
    if len(accounts) > 1:
        return Check("provenance", False,
                     f"more than one trading account in the log: {', '.join(sorted(accounts))}")
    detail = f"one code version ({shas[0] if shas else 'n/a'})"
    if accounts:
        detail += f", one account ({accounts[0]})"
    return Check("provenance", True, detail)


def check_signal_sanity(decisions: Optional[pd.DataFrame]) -> Check:
    """Do the logged targets still look like the strategy (long only, <=100%)?"""
    if decisions is None or decisions.empty or "target" not in decisions:
        return Check("signal_sanity", True, "no targets logged yet")
    try:
        last = json.loads(decisions.iloc[-1]["target"])
    except (ValueError, TypeError) as exc:
        # A corrupt row must be *reported*, not crash the thing whose job is to
        # report problems.
        return Check("signal_sanity", False, f"latest target row is unreadable: {exc}")
    if not isinstance(last, dict) or not last:
        return Check("signal_sanity", False, "latest target row is not a weight map")
    total = sum(float(v) for v in last.values())
    negatives = [k for k, v in last.items() if float(v) < 0]
    ok = not negatives and total <= 1.0 + 1e-6 and len(last) >= 1
    return Check("signal_sanity", bool(ok),
                 f"latest target invests {total:.1%} across {len(last)} symbols"
                 + (f"; NEGATIVE weights: {negatives}" if negatives else ""))


# --------------------------------------------------------------------------- #
# Loading and reporting
# --------------------------------------------------------------------------- #
def _read_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        return df if not df.empty else None
    except Exception:
        return None


def load_inputs():
    """Read the three committed evidence files, tolerating any of them missing."""
    eq = _read_csv(RESULTS / "daily_equity.csv")
    equity = None
    if eq is not None:
        col = "date_ny" if "date_ny" in eq.columns else eq.columns[0]
        equity = eq.assign(date=pd.to_datetime(eq[col])).groupby("date")["equity"].last().sort_index()
    return equity, _read_csv(LOGS / "decisions.csv"), _read_csv(LOGS / "trades.csv")


def run_all(live_start: Optional[str] = None, today: Optional[datetime] = None) -> List[Check]:
    equity, decisions, trades = load_inputs()
    return [
        check_freshness(equity, today),
        check_cadence(decisions, today),
        check_drawdown(equity),
        check_trading_actually_happens(trades, live_start),
        check_signal_sanity(decisions),
        check_provenance(_read_csv(LOGS / "provenance.csv")),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="Health checks for the ATA paper-trading bot")
    ap.add_argument("--quiet", action="store_true", help="print nothing when everything passes")
    ap.add_argument("--live-start", default=None, help="YYYY-MM-DD the live period began")
    ap.add_argument("--strict-missing", action="store_true",
                    help="treat 'no data yet' as a failure (off by default, so day 1 is quiet)")
    args = ap.parse_args()

    checks = run_all(live_start=args.live_start)
    failed = [c for c in checks if not c.ok]

    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": not failed,
        "checks": [asdict(c) for c in checks],
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "health.json").write_text(json.dumps(payload, indent=2))

    lines = ["## Bot health", ""]
    lines += [f"- {'✅' if c.ok else '❌'} **{c.name}** — {c.detail}" for c in checks]
    if failed:
        lines += ["", "**Action needed:** see the failing check(s) above, then the "
                      "troubleshooting table in `RUNBOOK.md` §8."]
    (RESULTS / "health.md").write_text("\n".join(lines) + "\n")

    if args.quiet and not failed:
        return
    print("\n".join(str(c) for c in checks))
    print(f"\noverall: {'OK' if not failed else 'ATTENTION NEEDED'}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
