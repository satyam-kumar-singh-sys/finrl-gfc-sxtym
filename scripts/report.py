"""The scoreboard: turn the daily log into the numbers judges ask for.
===================================================================

READ THIS FIRST IF YOU ARE NOT A CODER
--------------------------------------
While the bot trades, it writes one line per day into
results/daily_equity.csv (date, account value, cash). This file reads that
history and produces three things:

    figs/live_equity.png     a picture of the account against the S&P 500
    results/live_metrics.json  the numbers, machine-readable
    results/live_summary.md  a short human summary you can paste into an email

If a scheduled run was ever missed, add `--from-alpaca` and it rebuilds the
curve straight from the broker's own records instead of the local file — the
broker's version is the ground truth.

HOW TO RUN IT
-------------
    python -m scripts.report                 # from the local daily log
    python -m scripts.report --from-alpaca   # from the broker's history
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGS = ROOT / "figs"
TD = 252


def load_equity(from_alpaca: bool = False) -> pd.Series:
    """Read the account value, one point per trading day.

    The post-close snapshot is the last row written each day, so if the bot ran
    twice in a day we keep the later (closing) figure.
    """
    if from_alpaca:
        from src.trading.alpaca_manager import AlpacaManager, create_alpaca_account_from_env

        am = AlpacaManager([create_alpaca_account_from_env()])
        hist = am.get_portfolio_history(timeframe="1D", period="3M")
        eq = pd.Series(hist.get("equity", []), index=pd.to_datetime(hist.get("timestamp", [])))
        if eq.empty:
            raise SystemExit("no portfolio history returned by Alpaca")
        return eq

    path = RESULTS / "daily_equity.csv"
    if not path.exists():
        raise SystemExit(f"{path} not found — run the trading Action first")
    df = pd.read_csv(path)
    col = "date_ny" if "date_ny" in df.columns else df.columns[0]
    df["date"] = pd.to_datetime(df[col])
    # the post-close snapshot is the last row of each day
    return df.groupby("date")["equity"].last().sort_index()


def performance(eq: pd.Series, rf: float = 0.02) -> Dict[str, float]:
    """Compute the competition-relevant statistics from a daily equity curve.

    Same measures as the backtest (see scripts/backtest.py::metrics) so live and
    historical numbers are directly comparable. `rf` is the "risk-free" return we
    demand before calling a return good — 2% a year, roughly what T-bills paid in
    a normal year; it is the benchmark line in the Sharpe ratio.
    """
    r = eq.pct_change().dropna()
    if len(r) < 3:
        return {"sessions": int(len(r)), "note": "not enough data yet"}
    years = len(r) / TD
    total = float(eq.iloc[-1] / eq.iloc[0] - 1)
    cagr = float((1 + total) ** (1 / years) - 1) if years > 0 else float("nan")
    vol = float(r.std(ddof=1) * math.sqrt(TD))
    downside = r[r < 0]
    dvol = float(downside.std(ddof=1) * math.sqrt(TD)) if len(downside) > 1 else float("nan")
    dd = float((eq / eq.cummax() - 1).min())
    return {
        "sessions": int(len(r)),
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "start_equity": float(eq.iloc[0]),
        "end_equity": float(eq.iloc[-1]),
        "total_return": total,
        "annualised_return": cagr,
        "annualised_vol": vol,
        "sharpe": float((r.mean() * TD - rf) / vol) if vol > 0 else float("nan"),
        "sortino": float((r.mean() * TD - rf) / dvol) if np.isfinite(dvol) and dvol > 0 else float("nan"),
        "max_drawdown": dd,
        "calmar": float(cagr / abs(dd)) if dd < 0 and np.isfinite(cagr) else float("nan"),
        "positive_days": float((r > 0).mean()),
    }


def benchmark(eq: pd.Series) -> Optional[pd.Series]:
    """Fetch the S&P 500 (SPY) over the same dates so the chart can compare.

    Optional: if the download fails we still report our own numbers.
    """
    try:
        from scripts.data import load_prices

        spy = load_prices(["SPY"], start=str((eq.index[0] - pd.Timedelta(days=10)).date()))["SPY"]
        return spy.reindex(eq.index).ffill()
    except Exception:
        return None


def plot(eq: pd.Series, spy: Optional[pd.Series], out: Path) -> None:
    """Draw the live equity curve (top) and the underwater/drawdown chart (bottom)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib missing — skipping plot")
        return
    fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    base = eq.iloc[0]
    ax[0].plot(eq.index, (eq / base).values, lw=1.8, label="ATA v1.0 — Alpaca paper")
    if spy is not None and spy.notna().any():
        ax[0].plot(spy.index, (spy / spy.dropna().iloc[0]).values, lw=1.0, label="SPY (buy & hold)")
    ax[0].set_title("Live paper-trading equity curve")
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)
    dd = eq / eq.cummax() - 1
    ax[1].fill_between(dd.index, dd.values * 100, 0, color="tab:red", alpha=0.35)
    ax[1].set_title("Drawdown (%)")
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    print(f"plot → {out}")


def main() -> None:
    """Read the record, print the summary, and write the three output files."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-alpaca", action="store_true")
    ap.add_argument("--rf", type=float, default=0.02)
    args = ap.parse_args()

    eq = load_equity(args.from_alpaca)
    stats = performance(eq, args.rf)
    spy = benchmark(eq)
    if spy is not None and spy.notna().sum() > 2:
        stats["spy_same_period"] = performance(spy.dropna(), args.rf)

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "live_metrics.json").write_text(json.dumps(stats, indent=2))
    plot(eq, spy, FIGS / "live_equity.png")

    trades = None
    tpath = ROOT / "logs" / "trades.csv"
    if tpath.exists():
        trades = pd.read_csv(tpath)
    summary = [
        "## Live paper-trading results",
        "",
        f"- Period: **{stats.get('start')} → {stats.get('end')}** "
        f"({stats.get('sessions')} sessions, {stats.get('sessions', 0)/TD:.2f} years)",
        f"- Equity: **${stats.get('start_equity', 0):,.2f} → ${stats.get('end_equity', 0):,.2f}** "
        f"({stats.get('total_return', 0)*100:+.2f}%)",
        f"- Annualised return: **{stats.get('annualised_return', 0)*100:+.2f}%**  "
        f"· volatility {stats.get('annualised_vol', 0)*100:.2f}%",
        f"- Sharpe: **{stats.get('sharpe', float('nan')):.2f}**  · Sortino "
        f"**{stats.get('sortino', float('nan')):.2f}**  · max drawdown "
        f"**{stats.get('max_drawdown', 0)*100:.2f}%**  · Calmar {stats.get('calmar', float('nan')):.2f}",
    ]
    if "spy_same_period" in stats:
        s = stats["spy_same_period"]
        summary.append(f"- SPY over the same window: {s['total_return']*100:+.2f}% "
                       f"(Sharpe {s['sharpe']:.2f}, max DD {s['max_drawdown']*100:.2f}%)")
    if trades is not None and len(trades):
        summary.append(f"- Orders sent: **{len(trades)}** across {trades['symbol'].nunique()} symbols")
    (RESULTS / "live_summary.md").write_text("\n".join(summary) + "\n")

    print("\n".join(summary))
    print(f"\nwritten → {RESULTS/'live_metrics.json'}, {RESULTS/'live_summary.md'}")


if __name__ == "__main__":
    main()
