# Changelog

All times UTC. The live record must stay append-only: never edit past rows in
`logs/` or `results/` — add a new entry here instead.

## [1.3.0] — 2026-11-13
Adopted the transferable half of a competition post-mortem (SIG Predictions Cup,
autumn 2026), where the expensive failures were *operational*: a worker whose
alarm died with it, orders sent to an unverified account, a dashboard that looked
healthy while nothing ran. Those failure modes are not specific to prediction
markets, so the fixes are now in this repo.

### Added
- `scripts/guards.py` (+ `tests/test_guards.py`, 28 tests): pre-trade guards run
  before every rebalance — kill switch, account identity/status, target sanity
  (no shorts, no leverage, 45% per-investment cap with Treasury-bill cash
  exempt), and stale/future-dated signal refusal. A blocked run still logs,
  as `blocked_by_guard`, so the refusal is part of the record.
- **Kill switch:** repository variable `TRADING_PAUSED=true` stops orders and
  keeps logging. **Account pin:** `APCA_EXPECTED_ACCOUNT_ID` refuses to trade if
  the broker reports a different account.
- `logs/provenance.csv` written every run (code fingerprint, account number,
  settings fingerprint) + `provenance` health check that fails if the strategy
  code or the account changes mid-run.
- `scripts/review.py` + `REVIEW_CHECKLIST.md`: the ten-minute daily review
  (alive / trading / inside budget / same strategy / sessions logged) and a
  documented outage drill.
- `docs/RULES_LEDGER.md`: the competition rules mapped one-to-one to the code, the
  study objective stated before the run, and the known in-sample limitation named.
- `scripts/research_holdout.py` + `results/holdout_study.csv`: 36 configurations
  chosen on 2007-2019 and evaluated on 2020-2026. Rank correlation +0.62; 36/36
  positive in both windows; the shipped setting is 20th percentile in train and
  above the hold-out median — i.e. not an overfit peak. Written up in
  `STRATEGY.md` §5d.
- Dashboard: an operational **Status** badge (LIVE / PAUSED / DRY-RUN / STALE /
  NO DATA) derived from the files, not from optimism.
- Alerts now open **one issue per incident** labelled `bot-alert` and comment on
  it while it stays open; closing the issue is the acknowledgement. The alert
  body says what to do, not just that something failed.

### Deliberately not adopted
- **Automatic promotion/demotion of strategies with capital reallocation** — the
  same post-mortem reaches the same conclusion ("do not start with an autonomous
  capital allocator: it automates your weakest assumptions"). At 30 daily
  observations it would be selecting on noise.
- **Shadow accounts / market making / external-venue fair values** — instruments
  and venues this competition does not score; paper equities are the mandate.

## [1.2.0] — 2026-11-13
### Added
- `scripts/research_universe.py` + `results/universe_comparison.csv`: nine
  same-rules universes (from 2 core + 4 satellites up to 15 instruments) compared
  over 2007-2026. Every wider menu scored worse on Sharpe, Sortino and Calmar;
  turnover and costs are near-identical, so the difference is correlation, not
  fees. Written up in `STRATEGY.md` §5b, which also tabulates the 2026-11-13 gate
  state behind the "why are we only in SPY and BIL?" question.
- `docs/` dashboard is also served from `/docs` as before; no change.

### Fixed
- `scripts/dashboard.py`: (a) a "Current target" box stayed empty until the first
  *live* rebalance wrote `results/last_target.json` — it now falls back to the
  newest decision-log row; (b) timestamps rendered as
  `2026-11-13 00:00:00T14:45:31+00:00` because pandas had parsed the column; they
  now print as `2026-11-13 14:45 UTC`.
- `scripts/report.py`: year/date axis labels on the equity chart collided
  (`2026-10-29 2026-11-01`); now uses matplotlib's concise date formatter.
- `scripts/report.py`: a blank line inside the summary split the bullet list in
  two, so the "Provisional" note and the SPY comparison rendered as separate
  lists. The note now always comes last.
- `scripts/monitor.py`: "1 calendar days old" (and "1 decisions") — singular/plural
  in the health text.

## [1.1.1] — 2026-11-13
Two fixes found by simulating the *first evening* of live trading with one
snapshot in `results/daily_equity.csv` — i.e. exactly what tonight looks like.

### Fixed
- `scripts/report.py`: with a one-day sample the summary printed
  `Period: None -> None` and `Equity $0.00 -> $0.00`. The early return for short
  samples skipped the fields the summary prints. Balance, dates and drawdown are
  now always reported; only the annualised ratios are withheld, and the
  provisional note says why. Day-one output is public, so it should not look
  broken.
- `scripts/monitor.py`: `run_cadence` demanded six logged decisions per fourteen
  days from the very first evening, so it would have opened a GitHub issue every
  evening for a week. It is now ramp-aware: during the first fortnight it only
  asks whether the bot is alive (>= 1 decision in five days); the full quota
  applies once fourteen days of history exist. The switch is derived from the
  log itself, so there is nothing to remember after week two.

### Added
- `tests/test_report.py` (4 tests) and 3 ramp-up tests in `tests/test_monitor.py`
  covering both regressions. Suite: **31 passed**.

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
