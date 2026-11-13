"""Pre-trade guards — the last line of defence before money moves.
==============================================================

READ THIS FIRST IF YOU ARE NOT A CODER
--------------------------------------
`run_daily.py` decides what to buy. This file asks a blunt question first:

    "Is anything about this situation obviously wrong?"

If the answer is yes, the order is **not** sent. Nothing here tries to be clever
about the market — these are mechanical checks on the plumbing:

    1. KILL SWITCH   is trading paused on purpose?          (TRADING_PAUSED)
    2. ACCOUNT       is this the account we think it is, and is it usable?
    3. TARGET        do the weights add up, stay long-only, stay within caps?
    4. DATA          is the price data recent, or are we trading on a stale file?
    5. PROVENANCE    is the strategy code the same code we backtested?

WHY THIS FILE EXISTS
--------------------
Borrowed from a hard lesson in a different competition (SIG Predictions Cup,
autumn 2026): the expensive failures were not bad forecasts, they were
*operational* — an agent writing to the wrong account, a worker whose alarm died
with it, a dashboard that looked fine while nothing was running. A strategy that
cannot prove it is trading the right account on the right data is not safe to
run, however good its backtest.

Every check returns a sentence a human can act on. A block is not a crash: the
run still writes its log rows, so the record shows what was refused and why.

Usage
-----
    python -m scripts.guards          # print today's guard report, trade nothing
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LOGS = ROOT / "logs"

# Files whose contents define "the strategy". If one changes mid-run, the live
# record stops being a record of *this* strategy — so the fingerprint is logged
# every run and the health check compares them.
STRATEGY_FILES = (
    "src/strategies/adaptive_trend.py",
    "scripts/run_daily.py",
)

# Trading is blocked if the target looks anything like this.
MAX_SINGLE_WEIGHT = 0.45        # same cap the strategy itself enforces
MIN_CASH = 0.02                 # never invest the last 2%
# The "cash" ticker (1-3 month Treasury bills) is not a concentration risk: it
# is where the strategy parks money when nothing is trending. It is deliberately
# exempt from MAX_SINGLE_WEIGHT — today's real target is 54% BIL, and a guard
# that blocks the strategy's own correct output would be worse than no guard.
CASH_SYMBOL = "BIL"
MAX_PRICE_AGE_DAYS = 5          # a signal older than this is not a signal

# Values of TRADING_PAUSED that mean "do not send orders".
PAUSE_VALUES = {"1", "true", "yes", "y", "on", "paused"}


@dataclass
class GuardResult:
    """The verdict: may we trade today, and what did we check?"""

    ok: bool = True
    blockers: List[str] = field(default_factory=list)   # reasons we must not trade
    warnings: List[str] = field(default_factory=list)   # worth a human look
    notes: Dict[str, str] = field(default_factory=dict) # facts for the audit log

    def block(self, reason: str) -> None:
        self.ok = False
        self.blockers.append(reason)

    def warn(self, reason: str) -> None:
        self.warnings.append(reason)

    def as_dict(self) -> Dict[str, object]:
        return {"ok": self.ok, "blockers": self.blockers,
                "warnings": self.warnings, "notes": self.notes}

    def summary(self) -> str:
        if self.ok:
            base = "guards: OK"
        else:
            base = "guards: BLOCKED — " + "; ".join(self.blockers)
        if self.warnings:
            base += " | warnings: " + "; ".join(self.warnings)
        return base


# --------------------------------------------------------------------------- #
# Individual guards (pure functions: no network, no broker, easy to test)
# --------------------------------------------------------------------------- #
def trading_paused() -> bool:
    """Is the kill switch on?

    Set the GitHub *repository variable* `TRADING_PAUSED` to `true` (Settings →
    Secrets and variables → Actions → Variables) and the bot keeps computing and
    logging but sends no orders. Unset it and trading resumes. That is the whole
    emergency brake — no code change, no lost history.
    """
    return str(os.environ.get("TRADING_PAUSED", "")).strip().lower() in PAUSE_VALUES


def account_guard(info: Mapping[str, object],
                  expected_account: Optional[str] = None) -> GuardResult:
    """Is this the right account, and is it able to trade at all?

    `info` is the broker's own account record. We check identity (an optional
    `APCA_EXPECTED_ACCOUNT_ID` pins it), status, and the two blocked flags —
    a paper account can be blocked after a burst of rejected orders, and a
    blocked account returns "success" for orders that never happen.
    """
    r = GuardResult()
    number = str(info.get("account_number") or info.get("id") or "unknown")
    r.notes["account_number"] = number
    r.notes["account_status"] = str(info.get("status", "unknown"))

    if expected_account:
        want = str(expected_account).strip()
        if want and want != number:
            r.block(f"account mismatch: broker says {number}, expected {want} "
                    "(wrong or rotated API key?)")
    else:
        r.warn("APCA_EXPECTED_ACCOUNT_ID not set — account identity is logged but not pinned")

    status = str(info.get("status", "")).upper()
    if status and status != "ACTIVE":
        r.block(f"account status is {status}, not ACTIVE")

    for flag in ("trading_blocked", "account_blocked"):
        if bool(info.get(flag, False)):
            r.block(f"broker flag {flag} is set — the account cannot place orders")

    try:
        equity = float(info.get("equity") or 0)
    except (TypeError, ValueError):
        equity = 0.0
    r.notes["equity"] = f"{equity:.2f}"
    if equity <= 0:
        r.block("account equity is zero or unreadable")
    return r


def target_guard(target: Mapping[str, float], universe: Optional[set] = None,
                 cash_symbol: str = CASH_SYMBOL) -> GuardResult:
    """Do the weights look like our strategy, or like a bug?

    A long-only, fully-invested-with-a-cash-buffer portfolio is the only thing
    this strategy can legitimately produce. Anything else — a negative weight, a
    120% book, a ticker we did not ask for, one *investment* above the cap —
    means the signal path is broken, so we refuse to act on it.

    The cash ticker is excluded from the per-position cap on purpose: parking
    54% in Treasury bills when nothing is trending is the strategy working, not a
    bug. (Money-market funds are not a concentration bet on one company.)
    """
    r = GuardResult()
    if not target:
        r.block("target is empty")
        return r

    weights = {s: float(w) for s, w in target.items()}
    total = sum(weights.values())
    negatives = [s for s, w in weights.items() if w < 0]
    oversized = [f"{s}={w:.0%}" for s, w in weights.items()
                 if s != cash_symbol and w > MAX_SINGLE_WEIGHT + 1e-6]
    unknown = sorted(set(weights) - universe) if universe else []

    r.notes["target_total"] = f"{total:.4f}"
    r.notes["target_symbols"] = ",".join(sorted(weights))

    if negatives:
        r.block(f"negative weights (would short): {', '.join(negatives)}")
    if total > 1.0 + 1e-6:
        r.block(f"weights sum to {total:.2%} — that would borrow money")
    if total < 1.0 - MIN_CASH - 0.05:
        r.warn(f"weights sum to {total:.2%}: more than the intended cash buffer is idle")
    if oversized:
        r.block(f"single position above {MAX_SINGLE_WEIGHT:.0%} cap: {', '.join(oversized)}")
    if unknown:
        r.block(f"target contains tickers outside the universe: {', '.join(unknown)}")
    return r


def data_guard(signal_date: str, today_ny: str, max_age_days: int = MAX_PRICE_AGE_DAYS) -> GuardResult:
    """Is the signal built on recent prices?

    A stale cache silently re-sends last week's portfolio, which looks like a
    normal "hold" in the logs. Dates are compared as dates, not timestamps, so a
    weekend or a holiday never trips it.
    """
    r = GuardResult()
    try:
        sig = datetime.fromisoformat(str(signal_date)[:10]).date()
        today = datetime.fromisoformat(str(today_ny)[:10]).date()
    except ValueError:
        r.block(f"cannot read the signal date {signal_date!r}")
        return r
    age = (today - sig).days
    r.notes["signal_date"] = sig.isoformat()
    r.notes["signal_age_days"] = str(age)
    if age < -1:
        r.block(f"signal date {sig} is in the future (today {today})")
    elif age > max_age_days:
        r.block(f"signal is {age} days old (limit {max_age_days}) — the data download is stale")
    elif age > 3:
        r.warn(f"signal is {age} days old (long weekend or holiday?)")
    return r


def code_fingerprint() -> str:
    """A short hash of the files that define the strategy.

    Two runs with the same fingerprint ran the same code. That is what makes
    "we did not touch the strategy during the 30 days" checkable rather than a
    promise.
    """
    h = hashlib.sha256()
    for rel in STRATEGY_FILES:
        path = ROOT / rel
        if path.exists():
            h.update(rel.encode())
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


def write_provenance(*, code_sha: str, account_number: str, action: str,
                     config: Optional[Mapping[str, object]] = None) -> Path:
    """Append one row to logs/provenance.csv (its own file — never rewrites history)."""
    import csv

    LOGS.mkdir(exist_ok=True)
    path = LOGS / "provenance.csv"
    row = {
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_sha": code_sha,
        "account_number": account_number,
        "action": action,
        "config_sha": hashlib.sha256(
            json.dumps(config or {}, sort_keys=True, default=str).encode()
        ).hexdigest()[:16],
    }
    is_new = not path.exists()
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(row)
    return path


def pause_only(result: "GuardResult") -> bool:
    """Is this run blocked *only* by the kill switch?

    The distinction matters. A deliberate pause is a normal state: the bot should
    finish cleanly, commit its log and raise no alarm. A genuine problem (wrong
    account, broken weights) must fail loudly. If both are true, the problem wins
    — a pause must never hide a fault.
    """
    return (not result.ok) and bool(result.blockers) and all(
        "TRADING_PAUSED" in b for b in result.blockers
    )


def evaluate(*, info: Optional[Mapping[str, object]] = None,
             target: Optional[Mapping[str, float]] = None,
             universe: Optional[set] = None,
             signal_date: Optional[str] = None,
             today_ny: Optional[str] = None,
             expected_account: Optional[str] = None) -> GuardResult:
    """Run every guard that has enough information, and combine the verdicts."""
    out = GuardResult()

    if trading_paused():
        out.block("TRADING_PAUSED is set — kill switch engaged")

    if info is not None:
        _merge(out, account_guard(info, expected_account))
    if target is not None:
        _merge(out, target_guard(target, universe))
    if signal_date and today_ny:
        _merge(out, data_guard(signal_date, today_ny))

    out.notes["code_sha"] = code_fingerprint()
    return out


def _merge(dst: GuardResult, src: GuardResult) -> None:
    """Fold one guard's result into the running verdict."""
    dst.ok = dst.ok and src.ok
    dst.blockers.extend(src.blockers)
    dst.warnings.extend(src.warnings)
    dst.notes.update(src.notes)


def main() -> None:
    """Print a guard report with no broker calls (safe to run any time)."""
    res = evaluate()
    print(f"kill switch : {'ENGAGED' if trading_paused() else 'off'}")
    print(f"code sha    : {res.notes.get('code_sha')}")
    print(f"verdict     : {'OK' if res.ok else 'BLOCKED'}")
    for b in res.blockers:
        print(f"  BLOCKER: {b}")
    for w in res.warnings:
        print(f"  warning: {w}")


if __name__ == "__main__":
    main()
