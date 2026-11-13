"""The daily job — the only file that actually touches the brokerage account.
=============================================================================

READ THIS FIRST IF YOU ARE NOT A CODER
--------------------------------------
Once a day this file does five things, in order:

    1. downloads fresh prices (Yahoo Finance, free, no login);
    2. asks the strategy what the percentages should be today;
    3. looks at what the account currently holds, and how far off it is;
    4. if something is more than 2% away from target, buys/sells to fix it,
       otherwise does nothing (trading too often just burns fees);
    5. writes down what it decided, in plain files anyone can open.

Those written records are the transparency evidence the competition asks for.
They are committed to GitHub automatically by the workflow, so the trade log and
equity curve are public and cannot be quietly edited.

WHAT IT WILL NEVER DO
---------------------
* It never trades a real-money account: it checks the broker address and stops
  if the word "paper" is not in it.
* It never borrows money (the strategy cannot output more than 100%).
* It never trades when the US market is closed: it asks the broker first.

HOW TO RUN IT
-------------
    python -m scripts.run_daily --signal-only   # just show today's plan, safe
    python -m scripts.run_daily --dry-run       # show the orders it would send
    python -m scripts.run_daily                 # actually place paper orders
    python -m scripts.run_daily --snapshot      # record end-of-day account value
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# Make "import scripts...." and "import src...." work no matter which folder the
# command is run from. This is a convenience for beginners, not cleverness.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.data import load_prices, load_vix, universe            # noqa: E402
from scripts import guards                                           # noqa: E402
from src.strategies.adaptive_trend import ATAConfig, AdaptiveTrendAllocationStrategy  # noqa: E402

# Where the audit files live. Both folders are committed to the repo on purpose.
LOG_DIR = ROOT / "logs"
RES_DIR = ROOT / "results"
LOG_DIR.mkdir(exist_ok=True)
RES_DIR.mkdir(exist_ok=True)

# If every holding is already within 2% of its target, do nothing.
# Without this the bot would nudge positions every week for no benefit and pay
# trading costs for it. Override with the environment variable DRIFT_BAND.
DRIFT_BAND = float(os.environ.get("DRIFT_BAND", "0.02"))
# Optional safety catch: set LIVE_START=2026-11-20 to make the bot dry-run
# everything before that date. Empty means "start whenever the workflow runs".
LIVE_START = os.environ.get("LIVE_START", "")
# Ignore orders smaller than this many dollars (broker minimums / rounding).
MIN_ORDER_NOTIONAL = float(os.environ.get("MIN_ORDER_NOTIONAL", "25"))
# Optional: pin the account. If set and the broker reports a different account
# number, the run refuses to trade — this is how you catch a rotated or wrong
# API key before it writes to somebody else's account.
EXPECTED_ACCOUNT = os.environ.get("APCA_EXPECTED_ACCOUNT_ID", "").strip()

# Logging: timestamped lines, useful when a GitHub Action fails at 6am.
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("ata.live")


# =========================================================================== #
# Small utilities
# =========================================================================== #
def append_csv(path: Path, row: Dict[str, Any]) -> None:
    """Add one line to a CSV file, creating it with a header row the first time.

    Append-only is the point: history is never rewritten, so the record stays
    trustworthy.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(row)


# Words that mean "this will never work, stop immediately". We must not retry
# these: a wrong password does not fix itself by waiting 20 seconds.
FATAL_MARKERS = ("401", "403", "unauthorized", "forbidden", "credentials")


def with_retry(fn, tries: int = 3, wait: int = 20):
    """Try a network call up to `tries` times; give up politely on bad logins.

    Yahoo Finance and brokers occasionally hiccup. Retrying a hiccup is sensible;
    retrying a rejected password is not, so those errors stop the run at once
    with a message that says what to fix.
    """
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:                     # noqa: BLE001 - re-raised below
            message = str(exc).lower()
            if any(marker in message for marker in FATAL_MARKERS):
                raise SystemExit(
                    f"Alpaca rejected the request ({exc}).\n"
                    "   • check APCA_API_KEY / APCA_API_SECRET in .env or GitHub secrets\n"
                    "   • paper keys only work against https://paper-api.alpaca.markets"
                )
            log.warning("attempt %d/%d failed: %s", attempt, tries, exc)
            if attempt == tries:
                raise
            time.sleep(wait)


def now_utc() -> str:
    """Current time in UTC, e.g. 2026-11-12T14:45:00+00:00 (for the audit log)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_ny() -> str:
    """Today's date in New York, because the US market runs on New York time."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def alpaca():
    """Connect to the Alpaca PAPER account using the keys in the environment.

    This returns the framework's own broker wrapper (src/trading/alpaca_manager.py),
    which handles order placement, fractional shares, market hours and so on.
    We do not re-implement any of that.

    Two deliberate stops:
      * if the keys are missing → clear message instead of a confusing crash;
      * if the address is not a paper-trading address → refuse to run.
    """
    try:
        from src.trading.alpaca_manager import AlpacaManager, create_alpaca_account_from_env
    except ModuleNotFoundError as exc:               # missing requirements-live.txt
        raise SystemExit(f"missing dependency '{exc.name}' — run: pip install -r requirements-live.txt")

    try:
        account = create_alpaca_account_from_env()
    except ValueError as exc:
        raise SystemExit(
            f"Alpaca credentials not found ({exc}).\n"
            "   cp .env.example .env  and paste APCA_API_KEY / APCA_API_SECRET,\n"
            "   or set them as repository secrets for GitHub Actions."
        )
    if "paper" not in account.base_url:
        raise SystemExit(f"refusing to trade: APCA_BASE_URL={account.base_url} is not a paper endpoint")
    return AlpacaManager([account])


# If a scheduled run was dropped, GitHub will try again on the next cron slot.
# That retry must not re-balance a second time in the same afternoon, so a run
# that finds a decision row newer than this many minutes logs and stops.
RECENT_RUN_MINUTES = 90


def ran_recently(decisions_path: Path, minutes: int = RECENT_RUN_MINUTES) -> Optional[str]:
    """Was a real (non-dry) decision already logged within the last `minutes`?

    Returns the timestamp of that row, or None. Used to make retry slots safe:
    if the 14:45 UTC run succeeded and a retry fires at 15:15 UTC, the retry sees
    a 30-minute-old row and does nothing. If the 14:45 run was *dropped* by
    GitHub, there is no row at all and the retry trades normally.
    """
    if not decisions_path.exists():
        return None
    try:
        df = pd.read_csv(decisions_path)
    except (OSError, ValueError):
        return None
    if df.empty or "time_utc" not in df.columns:
        return None
    if "dry_run" in df.columns:
        df = df[~df["dry_run"].astype(str).str.lower().isin({"true", "1"})]
    if df.empty:
        return None
    last = pd.to_datetime(df["time_utc"].astype(str).str[:19], errors="coerce").max()
    if pd.isna(last):
        return None
    age = (pd.Timestamp.now(tz=None) - last).total_seconds() / 60.0
    return str(df["time_utc"].iloc[-1]) if age <= minutes else None


def cfg_dict() -> Dict[str, Any]:
    """The strategy settings in force, as a plain dictionary.

    Written into logs/provenance.csv on every run so the exact configuration
    behind each decision is recorded next to the code fingerprint. Judges (and
    you, in three weeks) can then prove what was running, not just what the
    current file says.
    """
    from dataclasses import asdict
    return asdict(ATAConfig())


# =========================================================================== #
# Step 2: work out today's target percentages
# =========================================================================== #
def compute_signal(as_of: Optional[str] = None):
    """Download prices, run the strategy, and log the reasoning.

    `refresh=True` forces a fresh download so the decision is never made on
    stale data. VIX (a fear index) is fetched only to print in the log for a
    human reader — it does not influence the decision, so a missing VIX cannot
    change a trade.
    """
    cfg = ATAConfig()
    close = with_retry(lambda: load_prices(universe(), start="2006-01-01", refresh=True))
    vix = load_vix(refresh=False)                 # diagnostics only, never a trade input
    strat = AdaptiveTrendAllocationStrategy(cfg)
    res = strat.compute(close.loc[: pd.Timestamp(as_of)] if as_of else close)
    target = {row.symbol: round(float(row.weight), 4) for row in res.weights.itertuples()}
    if vix is not None and len(vix):
        res.metadata["vix_last"] = round(float(vix.iloc[-1]), 2)
    log.info("signal %s: %s", res.metadata["as_of"], json.dumps(target))
    log.info("risk state=%s scaler=%.2f est_vol=%s", res.metadata["risk_state"],
             res.metadata["vol_scaler"], res.metadata["vol_20d_estimate"])
    return res, target


# =========================================================================== #
# The two jobs the scheduler asks for
# =========================================================================== #
def position_rows(positions, equity: float, as_of: str) -> List[Dict[str, Any]]:
    """Turn the broker's open positions into rows for logs/positions.csv.

    Why this file exists: `logs/trades.csv` records the moment an order was
    *submitted* (status `pending_new`, often before the fill price is known). The
    holdings below are the evidence of what was actually filled — symbol, number
    of shares, average price paid, and current value as a share of the account.
    Together the two files let a reader reconstruct both the intent and the
    result, which is exactly what a judge asking "did it really trade?" needs.

    Pure function: no network, so it is unit-tested.
    """
    rows: List[Dict[str, Any]] = []
    for p in positions or []:
        try:
            qty = float(p.get("qty") or 0)
            mv = float(p.get("market_value") or 0)
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue
        rows.append({
            "date_ny": as_of,
            "time_utc": now_utc(),
            "symbol": p.get("symbol"),
            "qty": round(qty, 6),
            "avg_entry_price": round(float(p.get("avg_entry_price") or 0), 4),
            "market_value": round(mv, 2),
            "weight": round(mv / equity, 6) if equity else "",
            "unrealized_pl": round(float(p.get("unrealized_pl") or 0), 2),
        })
    return rows


def snapshot() -> None:
    """Record the account's value after the close — this builds the equity curve.

    One row per trading day in results/daily_equity.csv. That file is what the
    competition means by "daily equity curve"; scripts/report.py turns it into a
    chart and the Sharpe/Sortino/drawdown table.
    """
    am = alpaca()
    info = with_retry(am.get_account_info)
    row = {
        "date_ny": today_ny(),
        "time_utc": now_utc(),
        "equity": round(float(info.get("equity", 0) or 0), 2),            # total account value
        "cash": round(float(info.get("cash", 0) or 0), 2),                # uninvested money
        "long_market_value": round(float(info.get("long_market_value", 0) or 0), 2),
        "last_equity": round(float(info.get("last_equity", 0) or 0), 2),  # yesterday's value
    }
    # Today's percentage change, computed so the report does not have to.
    row["daily_return"] = (round(row["equity"] / row["last_equity"] - 1, 6)
                           if row["last_equity"] else "")
    append_csv(RES_DIR / "daily_equity.csv", row)
    log.info("snapshot equity=%.2f cash=%.2f", row["equity"], row["cash"])

    # Also record WHAT we own, once per session. This is the fill evidence: the
    # order log shows what we asked for, this shows what we ended up holding.
    positions = with_retry(am.get_positions) or []
    for prow in position_rows(positions, row["equity"], row["date_ny"]):
        append_csv(LOG_DIR / "positions.csv", prow)
    log.info("positions logged: %d (%s)", len(positions),
             ", ".join(f"{p.get('symbol')} {p.get('qty')}" for p in positions) or "none")


def rebalance(dry_run: bool = False, as_of: Optional[str] = None, force: bool = False) -> None:
    """Compare the account with today's target and, if needed, fix the difference.

    This is the heart of the live bot. Read it top to bottom and you can predict
    exactly what it will do tomorrow.
    """
    # A date gate: if LIVE_START is set and we are before it, only rehearse.
    live_ok = (not LIVE_START) or (today_ny() >= LIVE_START)
    if not live_ok and not dry_run:
        log.info("before LIVE_START=%s (today %s) → forcing dry run", LIVE_START, today_ny())
        dry_run = True

    # 1) What SHOULD we hold?
    res, target = compute_signal(as_of)
    meta = res.metadata

    # 2) What DO we hold?
    am = alpaca()
    info = with_retry(am.get_account_info)
    equity = float(info.get("equity") or info.get("portfolio_value") or 0)
    positions = with_retry(am.get_positions) or []
    # Convert every position from dollars into a share of the account, so it can
    # be compared with the strategy's percentages.
    current = {p["symbol"]: float(p.get("market_value") or 0) / equity
               for p in positions if equity > 0}
    log.info("account equity=%.2f cash=%.2f positions=%d", equity,
             float(info.get("cash", 0) or 0), len(positions))

    # 2b) THE GUARDRAIL. Before any order is considered, check the four things
    #     that a backtest cannot check: kill switch, account identity/status,
    #     whether the weights are sane, and whether the prices are fresh. A
    #     blocked run still writes its log rows — the record shows the refusal.
    guard = guards.evaluate(
        info=info,
        target=target,
        universe=set(universe()),
        signal_date=meta.get("as_of"),
        today_ny=today_ny(),
        expected_account=EXPECTED_ACCOUNT or None,
    )
    log.info(guard.summary())
    paused = (not guard.ok) and guards.pause_only(guard)
    guards.write_provenance(code_sha=guard.notes.get("code_sha", ""),
                            account_number=guard.notes.get("account_number", "unknown"),
                            action="paused" if paused else ("blocked" if not guard.ok else "allowed"),
                            config=cfg_dict())
    if not guard.ok:
        append_csv(LOG_DIR / "decisions.csv", {
            "time_utc": now_utc(), "signal_date": meta["as_of"],
            "action": "paused" if paused else "blocked_by_guard",
            "dry_run": dry_run, "max_drift": "", "risk_state": meta["risk_state"],
            "vol_scaler": meta["vol_scaler"], "est_vol": meta["vol_20d_estimate"],
            "core_w": meta["core_weight"], "satellite_w": meta["satellite_weight"],
            "target": json.dumps(target, sort_keys=True),
            "gates": json.dumps({"__blockers__": guard.blockers}),
        })
        if paused:
            # A pause is a decision, not a fault. Exit 0 so the workflow still
            # commits the log and does NOT open an alert issue — otherwise a
            # deliberate pause would look like a week of outages and train you
            # to ignore the alarm.
            log.warning("TRADING_PAUSED is set → no orders this run. "
                        "The log was still written. Delete the variable to resume.")
            return
        raise SystemExit("guards blocked trading: " + "; ".join(guard.blockers))

    # 2c) RETRY SLOTS. GitHub can drop a scheduled run under load, so the
    #     workflow offers more than one cron slot per session. Those extra slots
    #     are only useful if a repeat run is harmless — hence this check: if a
    #     real decision was already logged in the last 90 minutes, stop here.
    #     (If the earlier slot was dropped there is no such row, and this run
    #     behaves like the first one.)
    if not force:
        previous = ran_recently(LOG_DIR / "decisions.csv")
        if previous:
            log.info("a real run already happened at %s — this is a retry slot, "
                     "doing nothing (action=skipped_recent_run)", previous)
            append_csv(LOG_DIR / "decisions.csv", {
                "time_utc": now_utc(), "signal_date": meta["as_of"], "action": "skipped_recent_run",
                "dry_run": dry_run, "max_drift": "", "risk_state": meta["risk_state"],
                "vol_scaler": meta["vol_scaler"], "est_vol": meta["vol_20d_estimate"],
                "core_w": meta["core_weight"], "satellite_w": meta["satellite_weight"],
                "target": json.dumps(target, sort_keys=True),
                "gates": json.dumps({"__note__": f"previous run {previous}"}),
            })
            return

    # 3) How big is the biggest gap between the two?
    drift = max((abs(target.get(s, 0.0) - current.get(s, 0.0))
                 for s in set(target) | set(current)), default=0.0)
    # A holding we do not own at all and should own counts as a change, even if
    # the gap happens to be small (first run on an empty account).
    changed = any(abs(current.get(s, 0.0)) < 1e-6 and w > 0.005 for s, w in target.items())
    action = "hold"
    result: Dict[str, Any] = {}

    # 4) Trade only if something actually changed or drifted past the band.
    if force or changed or drift > DRIFT_BAND:
        action = "dry_run" if dry_run else "rebalance"
        # The framework's executor does the maths: sell first, then buy with the
        # proceeds, respect market hours, never overspend available cash.
        result = with_retry(lambda: am.execute_portfolio_rebalance(
            target_weights=target, dry_run=dry_run, market_closed_action="skip"))
        if result.get("orders_placed", 0) == 0 and not dry_run:
            # Distinguish "market was shut" from "we looked and nothing to do".
            action = "skipped_market_closed" if not result.get("market_open", True) else "no_orders"
        # One row per order in the public trade log.
        for order in (result.get("orders") or []):
            append_csv(LOG_DIR / "trades.csv", {
                "time_utc": now_utc(), "mode": action,
                "symbol": order.get("symbol"), "side": order.get("side"),
                "qty": order.get("quantity"), "status": order.get("status"),
                "order_id": order.get("order_id"),
                "fill_price": order.get("average_fill_price", ""),
            })
        if not dry_run:
            # Remember the target so next week's drift check has a baseline.
            (RES_DIR / "last_target.json").write_text(json.dumps(
                {"as_of": meta["as_of"], "written": now_utc(), "target": target}, indent=2))
    else:
        log.info("no trade: max drift %.3f <= band %.3f", drift, DRIFT_BAND)

    # 5) Write the explanation, whether or not we traded. `gates` records which
    #    up-trend tests passed, so a reviewer can see why the mix looks like it
    #    does — including on days we chose to do nothing.
    append_csv(LOG_DIR / "decisions.csv", {
        "time_utc": now_utc(), "signal_date": meta["as_of"], "action": action,
        "dry_run": dry_run, "max_drift": round(drift, 4),
        "risk_state": meta["risk_state"], "vol_scaler": meta["vol_scaler"],
        "est_vol": meta["vol_20d_estimate"], "core_w": meta["core_weight"],
        "satellite_w": meta["satellite_weight"], "target": json.dumps(target, sort_keys=True),
        "gates": json.dumps({s: g["open"] for s, g in meta["gates"].items()}),
    })
    append_csv(RES_DIR / "equity_curve.csv", {
        "time_utc": now_utc(), "equity": round(equity, 2),
        "cash": round(float(info.get("cash", 0) or 0), 2), "positions": len(positions),
        "action": action,
    })
    log.info("action=%s drift=%.4f orders=%s", action, drift, result.get("orders_placed", 0))


# =========================================================================== #
# Command-line interface
# =========================================================================== #
def main() -> None:
    """Pick the job from the flags. Run with --help to see them all."""
    ap = argparse.ArgumentParser(description="ATA daily paper-trading job")
    ap.add_argument("--dry-run", action="store_true", help="plan orders, place none")
    ap.add_argument("--snapshot", action="store_true", help="record end-of-day equity only")
    ap.add_argument("--date", default=None, help="signal date (default: latest close)")
    ap.add_argument("--force", action="store_true",
                    help="trade even if a real run already happened in the last 90 min")
    ap.add_argument("--signal-only", action="store_true",
                    help="print today's target vector and exit (no Alpaca keys needed)")

    args = ap.parse_args()

    if args.signal_only:
        # Friendly printout for humans. Needs no broker account at all, which
        # makes it the safest first command to try.
        res, target = compute_signal(args.date)
        print(f"\nsignal date : {res.metadata['as_of']}")
        print(f"risk state  : {res.metadata['risk_state']}   vol scaler {res.metadata['vol_scaler']}")
        print(f"est 20d vol : {res.metadata['vol_20d_estimate']}")
        print("\n  symbol   weight  sleeve    gate")
        print("  " + "-" * 52)
        for row in res.weights.itertuples():
            gate = "open" if res.metadata["gates"].get(row.symbol, {}).get("open") else "closed"
            print(f"  {row.symbol:<8} {row.weight:6.2%}  {row.sleeve:<9} {gate}")
        print(f"\n  invested {res.metadata['invested']:.2%}  cash {res.metadata['cash']:.2%}")
    elif args.snapshot:
        snapshot()
    else:
        rebalance(dry_run=args.dry_run, as_of=args.date, force=args.force)


if __name__ == "__main__":
    main()
