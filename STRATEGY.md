# Strategy note — Adaptive Trend Allocation (ATA) v1.0

*Entry for the AI4Finance Open Trading Challenge (FinRL-X + Alpaca paper trading).*

This is the research record: what was tried, what failed, why the surviving design
looks the way it does, and what could still go wrong. Every number below is
reproducible with `python -m scripts.backtest` on Yahoo Finance data (no
look-ahead, one-day execution lag, 10 bp cost per unit of traded notional).

---

## 1. Objective function

The competition weights **risk-adjusted return 30%**, **max drawdown 20%**,
**stability 20%**, **documentation 15%**, **code quality 15%**, and explicitly
warns that high absolute return without risk control scores poorly.

So the design target was not "beat the S&P". It was:

> maximise Sharpe and Sortino **subject to a hard drawdown budget** and to being
> robust to parameter choice, market regime and cost assumptions.

A strategy that turns $100k into $115k with a 6% max drawdown scores better on
this rubric than one that makes $130k with a 30% drawdown, and it is far more
likely to keep doing it in the live 30-day window.

## 2. What was tested (and rejected)

All rows below were produced with the research harnesses in `scripts/`
(`research_sector_momentum.py`, `research_trend_gated.py`, `research_ata_grid.py`)
on Yahoo Finance daily closes, 2007-06 → 2026-11, 10 bp per unit turnover,
one-session execution lag.

| Candidate | Net Sharpe | Max DD | Verdict |
|---|---|---|---|
| Cross-sectional sector momentum, top-3 of 14 sector/region ETFs, 6-month score, weekly | 0.16 (gross 0.27) | −43.7% | rejected: no edge after costs |
| Same + 200-day filter | 0.21 | −30.1% | rejected |
| Sector momentum, top-5, weekly | 0.31 | −45.0% | rejected |
| Broad-index rotation (SPY/QQQ/IWM/EFA/EEM), top-2, 12-month, weekly | 0.42 | −30.1% | rejected: drawdown |
| Dual-momentum rotation, monthly | 0.51 | −31% | rejected: drawdown |
| **Time-series (absolute) momentum, trend-gated multi-asset** | **0.55** | **−12.4%** | **selected** |

The consistent finding: **relative** (cross-sectional) selection among liquid ETFs
has little edge once costs are paid, and even when it does it concentrates into
one theme and draws down 30–45%. **Absolute** trend filtering has the better
risk-adjusted profile because it removes the left tail (2008, 2020, 2022) and
parks capital in T-bills instead of rotating into the next losing sector.

### 2.1 First-hand comparison with the framework's own strategy

Rather than take the published Adaptive Rotation numbers on trust, the official
engine was run twice over the identical window (2018-01-01 → 2026-10-31),
changing only the asset universe inside `AdaptiveRotationConf_v1.2.1.yaml`:

| Configuration | Sharpe | CAGR | Max DD | Calmar |
|---|---|---|---|---|
| Adaptive Rotation, **shipped** universe (AAPL, MSFT, NVDA, META, AMZN, GOOGL, TSLA, XOM, CVX, COP, FCX, BHP, GLD, SLV, TLT, IEF, XLU, XLV, IAU, SHY, UUP) | **1.17** | 25.1% | −23.3% | 1.08 |
| Adaptive Rotation, **unbiased** sector-ETF universe (XLK, XLY, XLF, XLI, XLE, XLB, GLD, SLV, DBC, TLT, IEF, XLU, XLV, SHY, SPY, QQQ, IWM) | 0.74 | 9.4% | −15.0% | 0.63 |
| **ATA v1.0 (this entry)** | 0.70 | 8.4% | **−11.8%** | **0.71** |
| SPY (same window) | 0.69 | 14.2% | −33.7% | 0.42 |
| QQQ (same window) | 0.79 | 17.9% | −35.5% | — |

Read that table carefully: the official strategy's headline Sharpe roughly halves
when its hand-picked mega-cap universe — chosen in 2026, from assets known to
have won 2017-2026 — is replaced by a rules-based universe of the same size. On a
like-for-like basis ATA and Adaptive Rotation land in the same place on Sharpe,
while ATA takes materially less drawdown. This is the single most important
honesty check in this document: **the largest apparent edge in this challenge
space is universe selection, and universe selection done in hindsight is not an
edge you can deploy.** ATA therefore uses only broad, pre-committed ETFs.

### 2.2 Research bugs found and fixed

Two mistakes were found during this work; both had inflated earlier numbers. They
are documented because the process matters as much as the result:

1. returns were computed as `diff(price)/price` (wrong denominator) — fixed to
   `price[t]/price[t−1] − 1` and verified against a benchmark buy-and-hold curve;
2. a decision dated *t* was earning the return of *t* (same-bar execution) —
   fixed to a strict one-session lag. That alone costs ≈ 0.1 Sharpe and is the
   honest number.

An earlier draft also treated the trend filter as a *condition on selection*
rather than as a *gate* that parks capital in T-bills, which silently kept the
book fully invested through bear markets. That is why every closed gate's budget
is routed explicitly to cash in the shipped design.

## 3. The design

```
w_t = R( T( A( S( X_{<=t} ) ) ) )        (FinRL-X weight-centric contract)
```

### Sleeves

| Sleeve | Budget | Assets | Rule |
|---|---|---|---|
| Core | 70% | SPY 45%, QQQ 25% | each leg held only while **close > 200d SMA** *and* **126d momentum > 0** |
| Satellite | 30% | GLD, IEF, TLT, DBC | same gate per asset; open names share the budget **inverse to 20d realised volatility** |
| Cash | remainder | BIL (1–3m T-bills) | every closed gate's budget, plus the vol-target residual |

### Overlays

* **Volatility target 12% annualised** using a 20-day realised covariance of the
  *target* book. The scaler is capped at 1.0 (**never levered**) and only moves
  when it changes by more than 10%, which is what keeps turnover at ~3.6% of the
  book per session.
* **Caps**: no single ETF above 45%, at least 2% always in cash for fees/slippage.
* **Long only, no shorts, no leverage, no derivatives, no intraday trading.**

### Why each piece exists

* *200-day trend* — the classic, slow, well-documented absolute-momentum filter;
  it is what converts a −55% buy-and-hold drawdown into −12%.
* *126-day momentum* — a second, faster confirmation that avoids re-entering a
  name that is above its average only because the average is falling.
* *Inverse-vol satellite weights* — bonds, gold and commodities have very
  different volatilities (IEF ≈ 7%, DBC ≈ 20%); equal weighting would let oil
  dominate the risk of the diversifier sleeve.
* *Volatility target* — normalises risk across regimes so that a calm 2017 and a
  violent 2022 produce similar risk, which is what "performance stability" means
  in practice.
* *BIL instead of "cash"* — T-bills earn ≈ 4–5% in a high-rate world; parking
  there is a real return, not a hole in the portfolio.

## 4. Results

**Backtest 2007-06-01 → 2026-11-12** (19.4 years, 10 bp costs, 1-day lag,
weekly Friday decisions, 2% no-trade band):

| | ATA v1.0 net | ATA v1.0 gross | SPY | QQQ | 60/40 |
|---|---|---|---|---|---|
| CAGR | 6.5% | 7.5% | 10.6% | 15.6% | 8.2% |
| Volatility | 8.6% | 8.5% | 19.7% | 22.4% | 11.3% |
| **Sharpe** | **0.55** | 0.65 | 0.51 | 0.67 | 0.58 |
| **Sortino** | **0.67** | 0.80 | 0.62 | 0.87 | 0.73 |
| **Max drawdown** | **−12.4%** | −11.3% | −55.2% | −53.4% | −31.4% |
| **Calmar** | **0.52** | 0.66 | 0.19 | 0.29 | 0.26 |
| Positive months | 60.3% | 61.5% | 65.4% | 62.4% | 66.7% |

Calendar years (%, net): 2008 **+1.3** (SPY −36.8), 2015 −6.9, 2018 −3.7,
2020 +11.2, 2021 +20.5, 2022 **−5.3** (SPY −18.2), 2024 +16.6, 2026 YTD +3.8.

Sub-period Sharpe (net): 2007-11 **0.15**, 2012-16 0.47, 2017-20 0.69, 2021-now 0.76.
Restricted to the window used for the framework comparison in §2.1
(2018-01 → 2026-10): CAGR 8.4%, vol 9.4%, **Sharpe 0.70**, Sortino 0.84,
max drawdown **−11.8%**, Calmar 0.71 (SPY: 14.2% / 18.9% / 0.69 / 0.85 / −33.7% / 0.42).

The shape of the risk profile is the point: ATA never has a catastrophic year, so
its worst quarter and its maximum drawdown are small, and its Sharpe is achieved
with roughly **half the equity beta** of SPY.

## 5. Robustness

* **Parameter plateau** (45 combinations: vol target 0.08/0.10/0.12/0.15 × trend
  window 150/200/250 × satellite budget 0.20/0.30/0.40 — `results/backtest_sweep.csv`):
  Sharpe **0.39–0.59** (median 0.50), max drawdown **−11.2% to −14.7%**,
  Calmar 0.35–0.54. **Every** combination keeps the drawdown under 15%, and 44%
  of them still beat SPY's Sharpe. The shipped configuration sits mid-plateau,
  not on an edge.
  Within the grid, Sharpe rises slightly with the volatility target (0.12 → 0.55,
  0.15 → 0.57) while drawdown is essentially unchanged; 0.12 is shipped anyway
  because a lower realised risk is worth more than 0.03 Sharpe under a rubric
  that scores drawdown and stability, and because it leaves room for the tail
  scenarios that are not in this sample.
* **Costs** (all else equal): 5 bp → Sharpe 0.60, **10 bp → 0.55**,
  20 bp → 0.44 (max drawdown −13.8%). Turnover is ~3.6% of the book per session,
  so cost assumptions matter — the shipped 10 bp is a deliberately conservative
  paper-trading estimate, and 20 bp is still a respectable 0.44.
* **Execution lag**: 1 session → 0.55; 2 sessions → 0.51. A real, if modest,
  sensitivity, and the reason the live runner never trades on the same close it
  reads.
* **Frequency**: weekly 0.55 vs monthly 0.62 in-sample. Monthly is marginally
  better on Sharpe but takes a deeper drawdown (−13.6% vs −12.4%), reacts to
  trend breaks up to five weeks late, and produces a less informative live
  record. Weekly is shipped for drawdown control and responsiveness.
* **No-trade band**: 0% → 0.54, 2% → 0.55, 5% → 0.55. The result is not a
  turnover artefact.

## 6. Known weaknesses (read this before judging the live results)

1. **It lags in strong bull markets.** Beta ≈ 0.5 means it captured roughly half
   of 2023–2024. In a 30-day window that is entirely a melt-up, ATA can
   underperform a simple SPY buy-and-hold, and with 30 trading days there is no
   statistical power to separate skill from noise (the standard error of a
   30-day Sharpe is ≈ ±1.5).
2. **Whipsaw risk.** A choppy market that crosses the 200-day average repeatedly
   will generate a handful of small losses; the no-trade band limits, but does
   not remove, this.
3. **Rate sensitivity.** Half the defensive book is duration (IEF/TLT). A violent
   rise in yields while equities fall leaves only GLD and T-bills to hide in.
4. **Parameter history.** The strategy family was chosen after looking at the
   whole sample; 2007–2011 (Sharpe 0.15) is a reminder that the edge is
   regime-dependent. Realistic expectation for the live quarter is Sharpe ≈ 0.4.
5. **Simple cost model.** 10 bp per unit turnover approximates retail paper fills;
   real slippage in fast markets could be worse.

## 7. Live operation

* Decisions: Friday close data, orders Monday at 09:45 New York (market orders),
  executed by GitHub Actions (`.github/workflows/live.yml`), no manual step.
* Rebalance only when a target changed or drift exceeds 2%.
* Every run appends to `logs/decisions.csv`, `logs/trades.csv`,
  `results/equity_curve.csv`, and a post-close `results/daily_equity.csv`
  snapshot that produces the submitted equity curve via `scripts/report.py`.

## 8. Reproduce

```bash
pip install -r requirements-live.txt
python -m pytest -q tests                 # 18 unit tests, synthetic data, no network
python -m scripts.backtest --save --plot  # full-sample table + figs/ata_equity.png
python -m scripts.backtest --sweep        # robustness grid
python -m scripts.run_daily --signal-only # today's target vector, no API keys needed
```

Deterministic: the strategy has no stochastic component; tests seed numpy at 42.

*Past performance does not guarantee future results. This is research code, not
investment advice.*
