# Changelog

All times UTC. The live record must stay append-only: never edit past rows in
`logs/` or `results/` — add a new entry here instead.

## [1.0.0] — 2026-11-12
### Added
- `src/strategies/adaptive_trend.py`: Adaptive Trend Allocation (ATA) v1.0, a
  FinRL-X `BaseStrategy` implementation (trend-gated core + inverse-vol satellite
  book + 12% volatility target, T-bill cash).
- `scripts/data.py`: cached Yahoo Finance loader with `refresh=True` for live runs.
- `scripts/backtest.py`: net-of-cost backtest through the *production* strategy
  code, metrics, calendar-year table, sub-periods, robustness sweep, equity plot.
- `scripts/run_daily.py`: daily job (signal → drift check → Alpaca paper rebalance
  → audit trail), `--dry-run`, `--snapshot`, `--signal-only`.
- `scripts/report.py`: live equity curve, Sharpe/Sortino/max-DD report,
  submission-ready summary; can rebuild the curve from the broker.
- `tests/test_adaptive_trend.py`: 18 unit tests on synthetic data (no network).
- `.github/workflows/live.yml` (trading schedule + log commits) and `ci.yml`.
- Documentation: `STRATEGY.md`, `RUNBOOK.md`, `SUBMISSION.md`, this file.

### Research notes (what changed during development)
- Rejected cross-sectional sector momentum (net Sharpe −0.07) and FinRL-X
  Adaptive Rotation re-implementation (~0.3 at 20%/day turnover).
- Fixed two research bugs that had inflated early results: wrong return
  denominator, and same-bar execution (now a strict 1-session lag).
- Switched the trend filter from a selection *condition* to a *gate* that moves
  the budget to T-bills; this is what removed the bear-market drawdown.

### Backtest at release
2007-06-01 → 2026-11-12, 10 bp costs, 1-day lag, weekly decisions, 2% no-trade band:
Sharpe 0.545, Sortino 0.665, CAGR 6.5%, max drawdown −12.4%, Calmar 0.52
(SPY same window: 0.51 / 0.62 / 10.6% / −55.2% / 0.19).

### Live
- Paper trading start: `<fill in when the workflow goes live>`
- Account: Alpaca paper (API endpoint `https://paper-api.alpaca.markets`)
