"""Does a wider universe help ATA? A same-rules, same-costs comparison.

Question: "why only SPY and BIL — why not many more stocks?" (Asked on
2026-11-13, answered here so the answer is reproducible rather than a claim.)

This script answers it with the production strategy code, not an opinion. Each
variant changes ONLY the ticker list (and the budget split) in ATAConfig; the
trend rule, the 2% no-trade band, the 10 bp cost, the 1-day execution lag and the
volatility target are identical. So any difference in the table is the universe,
which is exactly what we want to isolate.

Run from the repo root:

    python -m scripts.research_universe

Writes results/universe_comparison.csv (the table quoted in STRATEGY.md §5).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.data import load_prices
from scripts.backtest import apply_no_trade_band, simulate, metrics, decision_dates
from src.strategies.adaptive_trend import ATAConfig, AdaptiveTrendAllocationStrategy

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

START, END = "2007-06-01", "2026-11-12"
COST_BPS, BAND, LAG = 10.0, 0.02, 1
WINDOW = 420          # rows handed to the strategy: enough for 200d MA + 126d mom

ALL = ["SPY", "QQQ", "IWM", "EFA", "EEM", "SMH", "GLD", "IEF", "TLT", "DBC",
       "TIP", "LQD", "HYG", "VNQ", "UUP", "XLE", "XLU", "XLV", "XLK", "XLP",
       "SHY", "BIL"]

VARIANTS = {
    # name: (core weights, satellites, satellite budget)
    "V0 baseline (shipped)": ((("SPY", .45), ("QQQ", .25)), ("GLD", "IEF", "TLT", "DBC"), .30),
    "V1 core + US small cap": ((("SPY", .40), ("QQQ", .20), ("IWM", .10)), ("GLD", "IEF", "TLT", "DBC"), .30),
    "V2 core + intl equity": ((("SPY", .40), ("QQQ", .15), ("IWM", .075), ("EFA", .075)), ("GLD", "IEF", "TLT", "DBC"), .30),
    "V3 sats + real assets": ((("SPY", .45), ("QQQ", .25)), ("GLD", "IEF", "TLT", "DBC", "VNQ", "TIP"), .30),
    "V4 sats + credit": ((("SPY", .45), ("QQQ", .25)), ("GLD", "IEF", "TLT", "DBC", "TIP", "LQD", "HYG"), .30),
    "V5 wide core + wide sats": ((("SPY", .30), ("QQQ", .15), ("IWM", .10), ("EFA", .15)),
                                 ("GLD", "IEF", "TLT", "DBC", "VNQ", "TIP"), .30),
    "V6 wide core, big sats": ((("SPY", .30), ("QQQ", .15), ("IWM", .10), ("EFA", .15)),
                               ("GLD", "IEF", "TLT", "DBC", "VNQ", "TIP"), .40),
    "V7 sectors as sats": ((("SPY", .45), ("QQQ", .25)),
                           ("XLK", "XLV", "XLE", "XLU", "XLP", "GLD", "IEF", "TLT"), .30),
    "V8 EM + dollar + reits": ((("SPY", .35), ("QQQ", .20), ("EEM", .075), ("EFA", .075)),
                               ("GLD", "IEF", "TLT", "DBC", "VNQ", "UUP"), .30),
}


def run(close: pd.DataFrame, core, sats, sat_total, dates) -> pd.DataFrame:
    cfg = ATAConfig(core=tuple(core), satellites=tuple(sats), satellite_total=sat_total)
    strat = AdaptiveTrendAllocationStrategy(cfg)
    rows = {}
    for d in dates:
        win = close.loc[:d].iloc[-WINDOW:]
        res = strat.compute(win)
        rows[d] = {r.symbol: r.weight for r in res.weights.itertuples()}
    tgt = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=close.columns).fillna(0.0)
    tgt = apply_no_trade_band(tgt.sort_index(), BAND)
    return simulate(close, tgt, lag=LAG, cost_bps=COST_BPS)


def main() -> None:
    close = load_prices(ALL, start="2005-01-01", end=END)
    close = close.loc[START:END].ffill().dropna(how="all")
    close = close.dropna(axis=1, how="any")          # only tickers with a full run
    dates = decision_dates(close.index, "W-FRI")
    print(f"history {close.index[0].date()} → {close.index[-1].date()}  "
          f"({len(close)} sessions, {len(dates)} weekly decisions)")
    print(f"tickers with full history: {', '.join(close.columns)}\n")

    rows, detail = {}, {}
    for name, (core, sats, st) in VARIANTS.items():
        sim = run(close, core, sats, st, dates)
        m = metrics(sim["ret"])
        rows[name] = m
        detail[name] = sim
        print(f"done: {name}")

    cols = ["cagr", "ann_vol", "sharpe", "sortino", "max_dd", "calmar",
            "win_rate_monthly", "worst_month"]
    RESULTS.mkdir(exist_ok=True)
    pd.DataFrame(rows).T.reindex(columns=cols).to_csv(
        RESULTS / "universe_comparison.csv", float_format="%.4f")
    print(f"\nsaved tables → {RESULTS/'universe_comparison.csv'}")
    print(f"\n{'variant':<28}" + "".join(f"{c:>12}" for c in cols))
    print("-" * (28 + 12 * len(cols)))
    for name, m in rows.items():
        if not m:
            continue
        print(f"{name:<28}" + "".join(f"{m[c]:>12.3f}" for c in cols))

    # Deployment + turnover, which is what "diversification" actually changes.
    print(f"\n{'variant':<28}{'avg invested':>14}{'avg #holdings':>15}{'turnover/day':>14}{'cost/yr':>10}")
    print("-" * 81)
    for name, sim in detail.items():
        invested = sim["invested"].mean()
        cost = sim["cost"].mean() * 252
        print(f"{name:<28}{invested:>13.1%}{'':>15}{sim['turnover'].mean():>13.2%}{cost:>9.2%}")

    # Sub-period stability: a universe that only wins in one era is a lucky pick.
    print("\nSharpe by era")
    eras = [("2007-2012", "2007-06-01", "2012-12-31"), ("2013-2019", "2013-01-01", "2019-12-31"),
            ("2020-2026", "2020-01-01", "2026-11-12")]
    print(f"{'variant':<28}" + "".join(f"{e[0]:>12}" for e in eras))
    print("-" * (28 + 12 * len(eras)))
    for name, sim in detail.items():
        line = ""
        for _, a, b in eras:
            m = metrics(sim.loc[a:b, "ret"])
            line += f"{m.get('sharpe', float('nan')):>12.3f}"
        print(f"{name:<28}{line}")


if __name__ == "__main__":
    main()
