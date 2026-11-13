# Changelog

All times UTC. The live record must stay append-only: never edit past rows in
`logs/` or `results/` — add a new entry here instead.

## [1.1.0] — 2026-11-13
### Added
- `scripts/monitor.py`: five health checks (data freshness, run cadence, drawdown
  budget vs 1.5x the backtest worst case, broker trading activity, signal sanity)
  writing `results/health.json` / `results/health.md`; `--quiet`, exit 1 on failure.
- `scripts/dashboard.py`: builds a single self-contained `docs/index.html` (inline
  CSS, base64 PNG) from `logs/` + `results/` — no CDN, no server, no API at view
  time, so it works on GitHub Pages and in a sandboxed preview alike.
- `tests/test_monitor.py`: 6 tests (suite: 24).
- `.github/workflows/live.yml`: `alert` job — opens an issue when a trading run fails.
- `.github/workflows/report.yml`: health checks + dashboard build + issue on
  unhealthy; commits `results figs docs`.
- `docs/.nojekyll` so the generated page is served byte-for-byte.

### Fixed
- GitHub Pages build failed on `docs/trading_calendar_guide.md`
  ("source text contains invalid characters for the used encoding UTF-8"): the
  file inherited from upstream was GBK-encoded. Re-saved as UTF-8 (same for
  `examples/weight_allocation_guide.md` and `src/trading/alpaca_manager.py`,
  which could not be imported as Python source while non-UTF-8).
- `scripts/monitor.py`: timezone-naive/aware subtraction crashed the freshness
  check; both sides are now normalised to naive UTC.

### Not adopted (deliberately)
- Live promotion/demotion of strategies with capital reallocation. Over ~20-30
  daily observations the ranking difference between variants is inside the noise
  band, so such a rule selects on luck and adds undocumented parameters.

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
