# Rules ledger — AI4Finance Open Trading Challenge

Written down *before* the run, so that "we followed the rules" is checkable
rather than claimed. Source: <https://ai4finance.org/competition> (read
2026-11-12). If the organisers publish an update, this page is the one to edit.

## 1. What the competition asks for

| Item | Requirement | How this repository satisfies it |
|---|---|---|
| Framework | Use the official **FinRL-Trading (FinRL-X)** framework | The framework is vendored intact under `src/`; the strategy is a `BaseStrategy` subclass in `src/strategies/adaptive_trend.py` and is executed through the framework's own `AlpacaManager`/executor. |
| Broker | **Alpaca paper trading**, US equities | `APCA_BASE_URL` is hard-coded to `https://paper-api.alpaca.markets` in the workflow; `run_daily.alpaca()` refuses to run against any endpoint without the word "paper". |
| Minimum period | **≥ 30 trading days** of live paper trading | Scheduled by `.github/workflows/live.yml`; the count is on the dashboard and in `python -m scripts.review` (check 5). |
| Automation | No manual intervention | Cron-triggered GitHub Actions; the only human action is reading alerts. |
| Evidence | Trade log, daily equity curve, reproducible code | `logs/decisions.csv`, `logs/trades.csv`, `results/daily_equity.csv`, `requirements*.txt`, fixed seeds (no randomness in the strategy), 61 unit tests, `STRATEGY.md`. |
| Scoring | 30% Sharpe/Sortino · 20% max drawdown · 20% stability · 15% documentation · 15% code quality | Optimised for exactly that mix — see `STRATEGY.md` §1 (objective function) and §4 (results). |
| Registration | No form, no deadline; rolling "Pilot Quarter", status *Open* | Entry is submitted via the AI4Finance Discord + `contact@ai4finance.org` once 30 sessions exist (`SUBMISSION.md` §3, §6). |

## 2. What this submission deliberately does *not* do

| Not doing | Why |
|---|---|
| Real money | The competition is paper-only; the code cannot point at a live endpoint by accident. |
| Leverage, shorting, options, futures | The strategy is long-only and capped at 100% invested; the pre-trade guard in `scripts/guards.py` refuses any target that breaks that. |
| Multiple accounts | One account, pinned by `APCA_EXPECTED_ACCOUNT_ID` when set, and verified in `logs/provenance.csv` every run. |
| Changing the strategy mid-run | The 30-day record must describe one strategy; the monitor fails a `provenance` check if the code fingerprint changes. |
| External signals (news, sentiment, an LLM in the loop) | Not reproducible; a judge cannot re-run it. Prices and a VIX diagnostic only. |
| Paid data | Yahoo Finance (free) with caching in `data/cache/` (not committed). |

## 3. Objective, stated before the run

**Primary:** maximise the competition score — risk-adjusted return (30%),
drawdown control (20%), stability (20%). Documentation and code quality (30%
combined) are earned by this document, `STRATEGY.md`, the tests and the runbook,
not by marketing.

**Explicitly not the objective:** beating SPY over a 30-day window. Over six
weeks the difference between Sharpe 0.55 and Sharpe 0.0 is mostly luck; the
strategy is built to show a *character* (about half of SPY's upside with a fifth
of its drawdown), and thirty days is a sample, not a verdict. Writing this down
now is what stops a good month from being read as proof and a bad one as failure.

## 4. Definitions this repository uses

- **Session** — a US trading day with a recorded equity snapshot.
- **Drawdown** — worst peak-to-trough fall of the equity curve, negative.
- **Risk budget** — 1.5 × the backtest's worst drawdown (−12.4% → alert at
  −18.6%). Crossing it is a prompt to read, not to intervene.
- **Kill switch** — repository variable `TRADING_PAUSED=true`: orders stop, logging
  continues. Never needed so far; tested in `tests/test_guards.py`.
- **Acknowledgement** — closing the `bot-alert` issue.

## 5. Known limitation, stated up front

The strategy's configuration was chosen while looking at the full 2007–2026
sample, so its headline Sharpe is an in-sample number. What mitigates it: the
45-configuration plateau (`results/backtest_sweep.csv`) shows the shipped setting
sits mid-plateau, not on a peak, and `STRATEGY.md` §5d reports a train/hold-out
split. A judge should treat the live 30-day record as the only genuinely
out-of-sample evidence — which is precisely what this repository publishes.
