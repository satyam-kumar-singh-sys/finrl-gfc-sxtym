"""The time machine: how the strategy would have done in the past.
===============================================================

READ THIS FIRST IF YOU ARE NOT A CODER
--------------------------------------
A backtest replays history: it pretends we started with $100,000 on some date,
asks the strategy every Friday what to hold, applies the price changes of the
following days, and reports what the account would be worth at the end.

It is the honesty check for the whole project. Two rules make it believable:

  1. NO PEEKING. On any Friday the strategy only sees prices up to that Friday.
     The trade is assumed to happen the NEXT trading day, which is what a real
     bot does. (Getting this wrong is the classic way backtests lie.)
  2. COSTS ARE PAID. Every time money is moved, a small fee is subtracted —
     10 basis points = 0.10% of the amount moved. Ignoring fees flatters a
     strategy that trades a lot.

The strategy code used here is EXACTLY the same file the live bot uses
(src/strategies/adaptive_trend.py). That is deliberate: the past performance
you read on the README is produced by the same logic that will trade the paper
account, not by a separate "research version" that might behave differently.

WHAT IT PRINTS
--------------
    a table of headline statistics (return, wobble, Sharpe, worst loss, ...)
    a year-by-year table next to the S&P 500
    a breakdown by era, to show consistency rather than one lucky decade

HOW TO RUN IT
-------------
    python -m scripts.backtest                  # default: 2007 → today
    python -m scripts.backtest --start 2016-01-01 --end 2025-12-31
    python -m scripts.backtest --save --plot    # save tables + write the chart
    python -m scripts.backtest --sweep          # try 45 settings variants
    python -m scripts.backtest --cost-bps 20    # assume double the fees
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from scripts.data import load_prices, load_vix, universe
from src.strategies.adaptive_trend import ATAConfig, AdaptiveTrendAllocationStrategy

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
TD = 252


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
def decision_dates(index: pd.DatetimeIndex, freq: str = "W-FRI") -> pd.DatetimeIndex:
    """The dates on which the strategy is allowed to make a decision.

    Default: the last trading day of each week (a Friday, unless the market was
    shut). Weekly is a deliberate choice — checking every day makes the bot
    trade far more, and the extra trading costs more than it earns.
    """
    s = pd.Series(index, index=index)
    if freq.upper().startswith("W"):
        period = s.dt.to_period("W")
        last = s.groupby(period).last()
        return pd.DatetimeIndex(last.values)
    if freq.upper().startswith("M"):
        last = s.groupby(s.dt.to_period("M")).last()
        return pd.DatetimeIndex(last.values)
    return index


def build_targets(close: pd.DataFrame, dates: Iterable[pd.Timestamp],
                  cfg: ATAConfig) -> pd.DataFrame:
    """Ask the strategy, once per decision date, "what should we hold?".

    The result is a table: one row per decision date, one column per ticker, and
    each value is that ticker's share of the account (0 if not held). Note that
    each call only receives prices up to that date — that is the no-peeking rule
    enforced in code rather than in good intentions.
    """
    strat = AdaptiveTrendAllocationStrategy(cfg)
    rows = {}
    for d in dates:
        res = strat.compute(close.loc[:d])
        rows[d] = {r.symbol: r.weight for r in res.weights.itertuples()}
    tgt = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=close.columns).fillna(0.0)
    return tgt.sort_index()


def apply_no_trade_band(targets: pd.DataFrame, band: float = 0.02) -> pd.DataFrame:
    """Ignore small changes; only re-trade when something moves more than 2%.

    Without this, a holding drifting from 44.0% to 44.5% would trigger a trade,
    and the fees would quietly eat the return. The live bot applies the same 2%
    rule, so the backtest experiences the same costs it will really pay.
    """
    if band <= 0:
        return targets
    out = pd.DataFrame(0.0, index=targets.index, columns=targets.columns)
    current = pd.Series(0.0, index=targets.columns)
    for d in targets.index:
        candidate = targets.loc[d]
        if (candidate - current).abs().max() > band:
            current = candidate
        out.loc[d] = current
    return out


def simulate(close: pd.DataFrame, targets: pd.DataFrame, lag: int = 1,
             cost_bps: float = 10.0) -> pd.DataFrame:
    """Turn a list of weekly decisions into a day-by-day account balance.

    Three ingredients, applied in this order:
      1. `held`  — what we actually owned each day (the decision, delayed by one
         day so we never trade on information from the same day's close);
      2. `gross` — the money those holdings made or lost that day;
      3. `costs` — the fee for changing positions, subtracted to give `net`.
    """
    dates = close.index
    held = targets.reindex(dates).ffill().fillna(0.0).shift(lag).fillna(0.0)
    rets = close.pct_change().fillna(0.0)
    gross = (held * rets).sum(axis=1)
    turnover = targets.reindex(dates).ffill().fillna(0.0).diff().abs().sum(axis=1).fillna(0.0)
    costs = turnover * cost_bps / 10_000.0
    net = gross - costs
    return pd.DataFrame({
        "gross": gross,
        "cost": costs,
        "ret": net,
        "equity": (1.0 + net).cumprod(),
        "turnover": turnover,
        "invested": held.sum(axis=1),
        "cash": 1.0 - held.sum(axis=1),
    })


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def metrics(ret: pd.Series, rf: float = 0.02) -> Dict[str, float]:
    """Summarise a stream of daily returns into the numbers judges care about.

    In plain terms:
      cagr              average yearly growth, compounded
      ann_vol           how much the account swings per year (lower = calmer)
      sharpe            return per unit of swing — the headline quality measure
      sortino           same idea, but only punishing downward swings
      max_dd            the worst peak-to-trough loss (the pain test)
      calmar            yearly return divided by that worst loss
      win_rate_monthly  share of months that ended in the black
    """
    r = pd.Series(ret).dropna()
    if len(r) < 20:
        return {}
    eq = (1 + r).cumprod()
    years = len(r) / TD
    cagr = float(eq.iloc[-1] ** (1 / years) - 1)
    ann_vol = float(r.std(ddof=1) * math.sqrt(TD))
    excess = r.mean() * TD - rf
    downside = r[r < 0]
    dvol = float(downside.std(ddof=1) * math.sqrt(TD)) if len(downside) > 5 else float("nan")
    dd = float((eq / eq.cummax() - 1).min())
    monthly = (1 + r).resample("ME").prod() - 1
    return {
        "total_return": float(eq.iloc[-1] - 1),
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sharpe": float(excess / ann_vol) if ann_vol > 0 else float("nan"),
        "sortino": float(excess / dvol) if np.isfinite(dvol) and dvol > 0 else float("nan"),
        "max_dd": dd,
        "calmar": float(cagr / abs(dd)) if dd < 0 else float("nan"),
        "win_rate_daily": float((r > 0).mean()),
        "win_rate_monthly": float((monthly > 0).mean()),
        "worst_month": float(monthly.min()),
        "best_month": float(monthly.max()),
        "n_sessions": len(r),
    }


def fmt(rows: Dict[str, Dict[str, float]]) -> None:
    """Print a metrics dictionary as a tidy aligned table."""
    cols = ["cagr", "ann_vol", "sharpe", "sortino", "max_dd", "calmar", "win_rate_monthly"]
    head = f"{'':<22}" + "".join(f"{c:>12}" for c in cols)
    print(head)
    print("-" * len(head))
    for name, m in rows.items():
        if not m:
            continue
        print(f"{name:<22}" + "".join(f"{m[c]:>12.3f}" for c in cols))


def benchmark_returns(close: pd.DataFrame) -> Dict[str, pd.Series]:
    """The comparison horses: simply holding the S&P 500 (SPY), the Nasdaq 100
    (QQQ), or a classic 60% shares / 40% bonds mix."""
    r = close.pct_change().fillna(0.0)
    out: Dict[str, pd.Series] = {}
    if "SPY" in r:
        out["SPY (buy & hold)"] = r["SPY"]
    if "QQQ" in r:
        out["QQQ (buy & hold)"] = r["QQQ"]
    if {"SPY", "IEF"}.issubset(r.columns):
        out["60/40 SPY-IEF"] = 0.6 * r["SPY"] + 0.4 * r["IEF"]
    return out


# --------------------------------------------------------------------------- #
# Plot
# --------------------------------------------------------------------------- #
def plot_equity(sim: pd.DataFrame, benches: Dict[str, pd.Series], path: Path) -> None:
    """Draw the two charts: account growth (log scale) and the drawdowns under it."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # pragma: no cover
        print("matplotlib not installed — skipping plot")
        return
    fig, ax = plt.subplots(2, 1, figsize=(11, 8), sharex=True,
                           gridspec_kw={"height_ratios": [2, 1]})
    eq = sim["equity"]
    ax[0].plot(eq.index, eq.values, lw=1.6, label="ATA v1.0 (net of 10 bp)")
    for name, r in benches.items():
        ax[0].plot(r.index, (1 + r.loc[eq.index]).cumprod().values, lw=1.0, alpha=0.8, label=name)
    ax[0].set_yscale("log")
    ax[0].set_title("Adaptive Trend Allocation — equity curve (log scale)")
    ax[0].legend(loc="upper left", fontsize=8)
    ax[0].grid(alpha=0.3)
    dd = eq / eq.cummax() - 1
    ax[1].fill_between(dd.index, dd.values * 100, 0, color="tab:red", alpha=0.35)
    ax[1].set_title("Drawdown (%)")
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    print(f"plot → {path}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest the ATA strategy")
    ap.add_argument("--start", default="2007-06-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--lag", type=int, default=1)
    ap.add_argument("--freq", default="W-FRI")
    ap.add_argument("--drift-band", type=float, default=0.02,
                    help="no-trade band vs current holdings (0 disables)")
    ap.add_argument("--refresh", action="store_true", help="re-download prices")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    cfg = ATAConfig()
    syms = universe()
    close = load_prices(syms, start="2006-01-01", refresh=args.refresh)
    vix = load_vix(start="2006-01-01", refresh=args.refresh)  # informational only

    if args.sweep:
        import itertools
        rows = []
        for tvol, ma, sat in itertools.product([None, 0.08, 0.10, 0.12, 0.15], [150, 200, 250], [0.2, 0.3, 0.4]):
            c = ATAConfig(target_vol=tvol or 0.99, trend_window=ma, satellite_total=sat)
            tgt = apply_no_trade_band(
                build_targets(close, decision_dates(close.loc[args.start:].index, args.freq), c),
                args.drift_band)
            m = metrics(simulate(close, tgt, args.lag, args.cost_bps)["ret"])
            rows.append({"target_vol": tvol, "trend_window": ma, "satellite_total": sat, **m})
            print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in rows[-1].items()}, flush=True)
        df = pd.DataFrame(rows)
        print("\nSharpe:", df["sharpe"].describe()[["min", "25%", "50%", "75%", "max"]].round(3).to_dict())
        print("MaxDD :", df["max_dd"].describe()[["min", "50%", "max"]].round(3).to_dict())
        if args.save:
            RESULTS.mkdir(exist_ok=True)
            df.to_csv(RESULTS / "backtest_sweep.csv", index=False)
        return

    dd = decision_dates(close.loc[args.start: args.end].index, args.freq)
    targets = apply_no_trade_band(build_targets(close, dd, cfg), args.drift_band)
    sim = simulate(close, targets, args.lag, args.cost_bps)
    sim = sim.loc[args.start: args.end]

    benches = {k: v.loc[sim.index] for k, v in benchmark_returns(close).items()}
    rows = {"ATA v1.0 (net)": metrics(sim["ret"]), "ATA v1.0 (gross)": metrics(sim["gross"])}
    rows.update({k: metrics(v) for k, v in benches.items()})

    print(f"\nSample: {sim.index[0].date()} → {sim.index[-1].date()}  "
          f"({len(sim)} sessions / {len(sim)/TD:.1f} years)   costs {args.cost_bps:.0f} bp   lag {args.lag}d\n")
    fmt(rows)
    print(f"\nturnover {sim['turnover'].mean()*100:.2f}%/session   cost drag {sim['cost'].mean()*TD*100:.2f}%/yr   "
          f"avg invested {sim['invested'].mean():.1%}   avg cash {sim['cash'].mean():.1%}")

    yearly = pd.DataFrame({"ATA": (1 + sim["ret"]).groupby(sim.index.year).prod() - 1,
                           **{k: (1 + v).groupby(v.index.year).prod() - 1 for k, v in benches.items()}})
    print("\nCalendar-year returns (%)")
    print((yearly * 100).round(1).to_string())

    print("\nSub-periods")
    sub = {}
    for label, (a, b) in {"2007-2011": ("2007", "2011"), "2012-2016": ("2012", "2016"),
                          "2017-2020": ("2017", "2020"), "2021-now": ("2021", "2100")}.items():
        sub[label] = metrics(sim["ret"].loc[a:b])
        sub[label + " (SPY)"] = metrics(benches["SPY (buy & hold)"].loc[a:b])
    fmt(sub)

    if args.save or args.plot:
        RESULTS.mkdir(exist_ok=True)
        sim.to_csv(RESULTS / "backtest_daily.csv")
        yearly.to_csv(RESULTS / "backtest_yearly.csv")
        payload = {
            "strategy": cfg.name, "version": "1.0",
            "sample": [str(sim.index[0].date()), str(sim.index[-1].date())],
            "cost_bps": args.cost_bps, "lag_days": args.lag,
            "drift_band": args.drift_band,
            "metrics": rows,
            "avg_turnover_per_session": float(sim["turnover"].mean()),
            "avg_invested": float(sim["invested"].mean()),
            "config": {k: v for k, v in cfg.__dict__.items()},
        }
        (RESULTS / "backtest_metrics.json").write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nsaved → {RESULTS}")
    if args.plot:
        plot_equity(sim, benches, ROOT / "figs" / "ata_equity.png")


if __name__ == "__main__":
    main()
