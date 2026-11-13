"""The 10-minute daily review — one screen, no decisions, no typing.
=================================================================

READ THIS FIRST IF YOU ARE NOT A CODER
--------------------------------------
Run this once a day:

    python -m scripts.review

It prints everything a human needs to answer one question — *is the bot doing
what we agreed it would do?* — and nothing else. It never trades, never edits
files, never calls the broker, and never needs an API key.

The checklist it prints is deliberately short. Borrowed from a harder
competition: the failures there were operational (silent downtime, an alarm that
died with the worker, a dashboard showing a date nobody checked), not analytical.
Five checks catch those:

    1. Is it alive?           last snapshot, last decision, days since each
    2. Is it trading?         orders in the live window, or suspicious silence
    3. Is it inside budget?   drawdown vs the backtest's worst case
    4. Is it still the same?  one code fingerprint, one account (provenance)
    5. Is the clock running?  sessions logged vs the 30 required

Exit code 0 = nothing needs you. 1 = read the line marked CHECK.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LOGS = ROOT / "logs"
REQUIRED_SESSIONS = 30


def _read(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return None
    return df if len(df) else None


def _days_since(stamp: str, today: date) -> Optional[int]:
    try:
        return (today - datetime.fromisoformat(str(stamp)[:19].replace(" ", "T")).date()).days
    except (ValueError, TypeError):
        return None


def _health() -> tuple[str, List[str]]:
    """Summarise results/health.json without importing the monitor (keeps this fast)."""
    path = RESULTS / "health.json"
    if not path.exists():
        return "not run yet", []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return "unreadable", []
    failing = [c["name"] for c in data.get("checks", []) if not c.get("ok", True)]
    return ("OK" if not failing else "FAILED: " + ", ".join(failing)), failing


def main() -> None:
    today = datetime.now(timezone.utc).date()
    eq = _read(RESULTS / "daily_equity.csv")
    dec = _read(LOGS / "decisions.csv")
    trd = _read(LOGS / "trades.csv")
    prov = _read(LOGS / "provenance.csv")

    lines: List[str] = []
    attention: List[str] = []

    # 1. alive -----------------------------------------------------------------
    last_snap = None
    if eq is not None:
        col = "date_ny" if "date_ny" in eq.columns else eq.columns[0]
        last_snap = str(eq[col].iloc[-1])
    snap_age = _days_since(last_snap, today) if last_snap else None
    lines.append(f"1. alive            last snapshot: {last_snap or '—'} "
                 f"({snap_age if snap_age is not None else '?'} days ago)")
    if snap_age is None or snap_age > 5:
        attention.append("no recent equity snapshot — did the 21:15 UTC run happen?")

    last_dec, last_action = None, None
    if dec is not None:
        last_dec = str(dec["time_utc"].iloc[-1])
        last_action = str(dec.get("action", pd.Series([""])).iloc[-1])
    lines.append(f"   last decision    {last_dec or '—'}  action={last_action or '—'}")

    # 2. trading ---------------------------------------------------------------
    n_orders = 0 if trd is None else len(trd)
    held = 0
    if dec is not None and "action" in dec.columns:
        held = int((dec["action"] == "hold").sum())
    lines.append(f"2. trading          {n_orders} order rows, {held} explicit 'hold' days")
    if n_orders == 0 and (dec is not None and len(dec) > 3):
        attention.append("several runs, zero orders — normal only if weights already match")

    # 3. risk budget -----------------------------------------------------------
    if eq is not None and len(eq) > 1:
        series = pd.to_numeric(eq["equity"], errors="coerce").dropna()
        dd = float((series / series.cummax() - 1).min())
        peak = float(series.max())
        lines.append(f"3. risk budget      equity ${series.iloc[-1]:,.0f} · peak ${peak:,.0f} "
                     f"· worst drawdown {dd:.2%} (alert below −18.6%)")
        if dd < -0.186:
            attention.append("drawdown beyond 1.5x the backtest worst case — read RUNBOOK §8")
    else:
        lines.append("3. risk budget      not enough snapshots yet")

    # 4. provenance ------------------------------------------------------------
    if prov is not None and "code_sha" in prov.columns:
        shas = sorted(set(prov["code_sha"].astype(str)))
        accounts = sorted(set(prov["account_number"].astype(str)) - {"unknown", "nan"})
        lines.append(f"4. same strategy?   {len(shas)} code version(s)"
                     f"{' (' + shas[0] + ')' if len(shas) == 1 else ': ' + ', '.join(shas)}"
                     f" · {len(accounts)} account(s)"
                     f"{' (' + accounts[0] + ')' if len(accounts) == 1 else ''}")
        if len(shas) > 1:
            attention.append("strategy code changed mid-run — the 30-day rule means one version")
        if len(accounts) > 1:
            attention.append("more than one trading account in the log — check the API keys")
    else:
        lines.append("4. same strategy?   no provenance rows yet")

    # 5. clock -----------------------------------------------------------------
    sessions = 0
    if eq is not None:
        col = "date_ny" if "date_ny" in eq.columns else eq.columns[0]
        sessions = int(pd.to_datetime(eq[col], errors="coerce").dt.date.nunique())
    lines.append(f"5. the clock        {sessions}/{REQUIRED_SESSIONS} sessions logged "
                 f"({max(0, REQUIRED_SESSIONS - sessions)} to go)")

    health, failing = _health()
    lines.append("")
    lines.append(f"health file         {health}")
    if failing:
        attention.append(f"monitor checks failing: {', '.join(failing)}")

    print("\n".join(lines))
    print("-" * 72)
    if attention:
        print("CHECK:")
        for item in attention:
            print(f"  • {item}")
    else:
        print("Nothing needs you. (That is the normal answer most days.)")
    print("-" * 72)
    print("Do not change the strategy: the frozen rule *is* the entry. See REVIEW_CHECKLIST.md.")
    raise SystemExit(1 if attention else 0)


if __name__ == "__main__":
    main()
