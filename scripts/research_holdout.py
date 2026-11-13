"""Did we tune the strategy into a corner? A train / hold-out check.
==================================================================

THE QUESTION
------------
The shipped settings were chosen while looking at the whole 2007-2026 history.
That is normal, and it is also the most common way a backtest lies: pick the
configuration that won, then quote its score as if it were a forecast.

The honest test is to *pretend* we only had the past. So:

    TRAIN     2007-06 → 2019-12   choose the best configuration here, blind.
    HOLD-OUT  2020-01 → 2026-11   never used for the choice. Report what it does.

What we want to see is not a high hold-out Sharpe (nobody can promise that). We
want to see that the ranking is *stable*: configurations that were better in the
train window are still better in the hold-out window. If the ranking were noise,
the best-in-train configuration would land anywhere in the hold-out table.

Run:
    python -m scripts.research_holdout

Writes results/holdout_study.csv.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.backtest import apply_no_trade_band, decision_dates, metrics, simulate
from scripts.data import load_prices, universe
from src.strategies.adaptive_trend import ATAConfig, AdaptiveTrendAllocationStrategy

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

START, END = "2007-06-01", "2026-11-12"
TRAIN_END = "2019-12-31"
HOLDOUT_START = "2020-01-01"
COST_BPS, BAND, LAG, WINDOW = 10.0, 0.02, 1, 420

VOL_TARGETS = (0.08, 0.10, 0.12, 0.15)
TREND_WINDOWS = (150, 200, 250)
SAT_BUDGETS = (0.20, 0.30, 0.40)


def targets_for(close: pd.DataFrame, dates, cfg: ATAConfig) -> pd.DataFrame:
    """Weekly targets from the production strategy, with the no-trade band."""
    strat = AdaptiveTrendAllocationStrategy(cfg)
    rows = {}
    for d in dates:
        res = strat.compute(close.loc[:d].iloc[-WINDOW:])
        rows[d] = {r.symbol: r.weight for r in res.weights.itertuples()}
    tgt = (pd.DataFrame.from_dict(rows, orient="index")
           .reindex(columns=close.columns).fillna(0.0).sort_index())
    return apply_no_trade_band(tgt, BAND)


def main() -> None:
    symbols = universe()
    close = load_prices(symbols, start="2005-01-01", end=END)
    close = close.loc[START:END].ffill().dropna(axis=1, how="any")
    dates = decision_dates(close.index, "W-FRI")
    print(f"{len(close)} sessions, {len(dates)} weekly decisions, "
          f"{len(VOL_TARGETS) * len(TREND_WINDOWS) * len(SAT_BUDGETS)} configurations")

    rows = []
    for vt in VOL_TARGETS:
        for ma in TREND_WINDOWS:
            for sat in SAT_BUDGETS:
                cfg = ATAConfig(target_vol=vt, trend_window=ma, satellite_total=sat)
                sim = simulate(close, targets_for(close, dates, cfg), lag=LAG, cost_bps=COST_BPS)
                tr = metrics(sim.loc[:TRAIN_END, "ret"])
                ho = metrics(sim.loc[HOLDOUT_START:, "ret"])
                rows.append({
                    "target_vol": vt, "trend_window": ma, "satellite_total": sat,
                    "train_sharpe": tr["sharpe"], "train_max_dd": tr["max_dd"],
                    "holdout_sharpe": ho["sharpe"], "holdout_max_dd": ho["max_dd"],
                    "holdout_cagr": ho["cagr"],
                })
                print(f"  vol {vt:.2f} ma {ma} sat {sat:.2f} → "
                      f"train {tr['sharpe']:.3f} / holdout {ho['sharpe']:.3f}")

    df = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    df.to_csv(RESULTS / "holdout_study.csv", index=False, float_format="%.4f")

    # ---- the two numbers that matter -------------------------------------- #
    best_train = df.loc[df["train_sharpe"].idxmax()]
    df["holdout_rank"] = df["holdout_sharpe"].rank(ascending=False).astype(int)
    df["train_rank"] = df["train_sharpe"].rank(ascending=False).astype(int)
    rank_of_best = int(df.loc[best_train.name, "holdout_rank"])
    spearman = float(df["train_sharpe"].corr(df["holdout_sharpe"], method="spearman"))

    shipped = df[(df.target_vol == 0.12) & (df.trend_window == 200) & (df.satellite_total == 0.30)].iloc[0]

    print("\n" + "=" * 74)
    print(f"configurations                      {len(df)}")
    print(f"best in TRAIN  (vol {best_train.target_vol:.2f}, ma {best_train.trend_window},"
          f" sat {best_train.satellite_total:.2f})")
    print(f"  train Sharpe {best_train.train_sharpe:.3f} → HOLD-OUT Sharpe "
          f"{best_train.holdout_sharpe:.3f}  (rank {rank_of_best} of {len(df)} in hold-out)")
    print(f"shipped config (vol 0.12, ma 200, sat 0.30)")
    print(f"  train Sharpe {shipped.train_sharpe:.3f} → hold-out Sharpe "
          f"{shipped.holdout_sharpe:.3f}  (hold-out rank {int(shipped.holdout_rank)} of {len(df)})")
    print(f"Spearman rank correlation train vs hold-out: {spearman:+.3f}")
    print(f"configurations with positive Sharpe in BOTH windows: "
          f"{int(((df.train_sharpe > 0) & (df.holdout_sharpe > 0)).sum())} of {len(df)}")
    print(f"hold-out Sharpe range: {df.holdout_sharpe.min():.3f} … {df.holdout_sharpe.max():.3f} "
          f"(median {df.holdout_sharpe.median():.3f})")
    print("=" * 74)
    print(f"saved → {RESULTS / 'holdout_study.csv'}")


if __name__ == "__main__":
    main()
