## Bot health

- ✅ **fresh_data** — last snapshot 2026-11-13 (1 calendar day old; tolerance 5)
- ✅ **run_cadence** — ramp-up: day 2 of 14, 5 decisions in the last 5 days (want >= 1 — just proving the workflow runs)
- ✅ **drawdown_budget** — not enough history to measure yet
- ✅ **trading_activity** — live start not declared; skipped
- ✅ **signal_sanity** — latest target invests 98.0% across 2 symbols
- ❌ **provenance** — strategy code changed mid-run: 3 fingerprints (5542f53cb781a6cc, c80e65d3e2a33bf8, cf94e07d9023dee2) — the 30-day window must use one version

**Action needed:** see the failing check(s) above, then the troubleshooting table in `RUNBOOK.md` §8.
