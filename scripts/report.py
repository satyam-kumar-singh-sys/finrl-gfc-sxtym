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


# Below this many sessions, yearly figures are statistically meaningless (a
# 4-day run can annualise to "+28%" or "Sharpe 5.2" purely by luck), so we report
# only what the data can actually support.
MIN_SESSIONS_FOR_ANNUALISED = 20


def _plural(n: int, word: str) -> str:
    """`1 snapshot`, `25 snapshots` — small thing, but day-one output is public."""
    return f"{n} {word}" + ("" if int(n) == 1 else "s")


def performance(eq: pd.Series, rf: float = 0.02) -> Dict[str, float]:
    """Compute the competition-relevant statistics from a daily equity curve.

    Same measures as the backtest (see scripts/backtest.py::metrics) so live and
    historical numbers are directly comparable. `rf` is the "risk-free" return we
    demand before calling a return good — 2% a year, roughly what T-bills paid in
    a normal year; it is the benchmark line in the Sharpe ratio.

    Early in the live period there are only a handful of sessions. Annualising
    those would produce impressive-looking nonsense, so annualised return,
    volatility, Sharpe, Sortino and Calmar are withheld (set to None) until there
    are at least `MIN_SESSIONS_FOR_ANNUALISED` sessions. Total return, equity and
    worst drawdown are always reported, because they are true regardless of
    sample size.
    """
    r = eq.pct_change().dropna()

    # ------------------------------------------------------------------ #
    # Facts first. These are true at any sample size, even one snapshot.
    # Getting this order right matters: on the very first evening the report
    # used to say "Equity $0.00 -> $0.00" purely because the early return
    # below skipped the fields the summary prints. Real balance, real dates,
    # real drawdown — always.
    # ------------------------------------------------------------------ #
    out: Dict[str, float] = {
        "snapshots": int(len(eq)),
        "sessions": int(len(r)),          # return observations, one fewer than snapshots
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "start_equity": float(eq.iloc[0]),
        "end_equity": float(eq.iloc[-1]),
        "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
        "max_drawdown": float((eq / eq.cummax() - 1).min()),
        "minimum_sessions_for_annualised": MIN_SESSIONS_FOR_ANNUALISED,
        "provisional": True,              # provisional unless proven otherwise below
    }

    # Two snapshots are not a track record: a single return has no dispersion,
    # so every ratio would be a division by zero dressed up as a number.
    if len(r) < 3:
        out["note"] = (f"only {len(eq)} snapshot(s) logged; "
                       "annualised statistics need at least 3 sessions")
        return out

    total = out["total_return"]
    dd = out["max_drawdown"]
    years = len(r) / TD
    cagr = float((1 + total) ** (1 / years) - 1) if years > 0 else float("nan")
    vol = float(r.std(ddof=1) * math.sqrt(TD))
    downside = r[r < 0]
    dvol = float(downside.std(ddof=1) * math.sqrt(TD)) if len(downside) > 1 else float("nan")
    out.update({
        "positive_days": float((r > 0).mean()),
        "provisional": bool(len(r) < MIN_SESSIONS_FOR_ANNUALISED),
    })
    if len(r) >= MIN_SESSIONS_FOR_ANNUALISED:
        out.update({
            "annualised_return": cagr,
            "annualised_vol": vol,
            "sharpe": float((r.mean() * TD - rf) / vol) if vol > 0 else float("nan"),
            "sortino": float((r.mean() * TD - rf) / dvol) if np.isfinite(dvol) and dvol > 0 else float("nan"),
            "calmar": float(cagr / abs(dd)) if dd < 0 and np.isfinite(cagr) else float("nan"),
        })
    else:
        out["note"] = (f"provisional: {len(r)} sessions < {MIN_SESSIONS_FOR_ANNUALISED}; "
                       "annualised statistics withheld on purpose")
    return out


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

    # Date labels: with a few dozen sessions matplotlib's default ticks collide
    # ("2026-10-29 2026-11-01" on top of each other). A concise formatter keeps
    # the axis readable from week one through the full 30-session run.
    try:
        import matplotlib.dates as mdates
        locator = mdates.AutoDateLocator(minticks=3, maxticks=8)
        fmt = mdates.ConciseDateFormatter(locator)
        for a in ax:
            a.xaxis.set_major_locator(locator)
            a.xaxis.set_major_formatter(fmt)
    except Exception:                                    # pragma: no cover
        pass                                              # labels are cosmetic
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
    provisional = bool(stats.get("provisional"))

    # Build the summary as ONE bullet list plus (optionally) one closing note.
    # Markdown ends a list at the first blank line, so any blank line in the
    # middle would split the facts into two disconnected lists — that is why the
    # "Provisional" note goes last, after every bullet.
    bullets = [
        f"- Period: **{stats.get('start')} → {stats.get('end')}** "
        f"({_plural(stats.get('snapshots', stats.get('sessions', 0)), 'snapshot')}, "
        f"{_plural(stats.get('sessions', 0), 'daily return')})",
        f"- Equity: **${stats.get('start_equity', 0):,.2f} → ${stats.get('end_equity', 0):,.2f}** "
        f"({stats.get('total_return', 0)*100:+.2f}%)",
        f"- Worst peak-to-trough so far: **{stats.get('max_drawdown', 0)*100:.2f}%**",
    ]
    if not provisional:
        bullets += [
            f"- Annualised return: **{stats.get('annualised_return', 0)*100:+.2f}%**  "
            f"· volatility {stats.get('annualised_vol', 0)*100:.2f}%",
            f"- Sharpe: **{stats.get('sharpe', float('nan')):.2f}**  · Sortino "
            f"**{stats.get('sortino', float('nan')):.2f}**  · Calmar {stats.get('calmar', float('nan')):.2f}",
        ]
    if "spy_same_period" in stats:
        sp = stats["spy_same_period"]
        line = f"- SPY over the same window: {sp['total_return']*100:+.2f}%"
        if not sp.get("provisional") and "sharpe" in sp:
            line += f" (Sharpe {sp['sharpe']:.2f}, max DD {sp['max_drawdown']*100:.2f}%)"
        bullets.append(line)
    if trades is not None and len(trades):
        bullets.append(f"- Orders sent: **{len(trades)}** across {trades['symbol'].nunique()} symbols")

    summary = ["## Live paper-trading results", ""] + bullets
    if provisional:
        # Be explicit rather than impressive: annualising a handful of days
        # produces numbers that are pure noise.
        summary += [
            "",
            f"> **Provisional.** Only {_plural(stats.get('snapshots', 0), 'snapshot')} / "
            f"{_plural(stats.get('sessions', 0), 'daily return')} logged so far; "
            f"annualised return, volatility, Sharpe, Sortino and Calmar are withheld "
            f"until {stats.get('minimum_sessions_for_annualised')} sessions exist. "
            "A two-week Sharpe is luck, not evidence.",
        ]
    (RESULTS / "live_summary.md").write_text("\n".join(summary) + "\n")

    print("\n".join(summary))
    print(f"\nwritten → {RESULTS/'live_metrics.json'}, {RESULTS/'live_summary.md'}")


if __name__ == "__main__":
    main()
