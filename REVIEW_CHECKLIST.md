# The 10-minute daily review

One screen, once a day. Not a research session — a *shift handover*.

```bash
python -m scripts.review
```

Exit code 0 means nothing needs you, which is the normal answer. Exit code 1
prints a `CHECK:` line naming the one thing to look at.

## What you are checking, and why these five things

Borrowed from a harder competition (SIG Predictions Cup, autumn 2026), where the
expensive mistakes were **operational**, not analytical: a worker that stopped
and took its own alarm with it; orders sent to an account nobody meant to use; a
dashboard that looked healthy because a human had a browser open while the
process was dead. None of those show up in a Sharpe ratio.

| # | Question | Evidence | If it looks wrong |
|---|---|---|---|
| 1 | **Is it alive?** | last snapshot date, last decision, days since | Actions → `live-paper-trading` → re-run the job. Then RUNBOOK §8. |
| 2 | **Is it trading?** | order rows in `logs/trades.csv`, count of `hold` days | Zero orders while weights already match is correct. Zero for a week is not. |
| 3 | **Inside the risk budget?** | drawdown vs −18.6% (1.5 × the backtest's −12.4%) | A bad week is not a bug. Only the 1.5× breach is a signal to act — and the action is *read*, not interfere. |
| 4 | **Still the same strategy?** | one code fingerprint, one account in `logs/provenance.csv` | If either count is >1, stop and investigate: the 30-day record must describe one strategy on one account. |
| 5 | **Is the clock running?** | sessions logged / 30 | Nothing to do. This is just the countdown to the earliest credible submission. |

## The two rules that make this honest

1. **The dashboard is not monitoring.** `docs/index.html` is rebuilt from the
   committed log after each session, so it is evidence — but it only tells you
   about the past. The *alert* is the GitHub issue: it fires when nobody is
   looking. Never conclude "it's fine" from a page you had to open yourself.
2. **Never change the strategy mid-run.** The frozen rule *is* the entry, and
   check 4 proves you kept it frozen. If you are tempted to "improve" something,
   write it in `STRATEGY.md` §6 as a future idea instead.

## Once a week (2 more minutes)

- Open the public page and the last three `logs/decisions.csv` rows. Do the
  orders match the stated target? (`results/last_target.json` is the record.)
- Skim `results/health.md`. Green ≠ return; green = plumbing.
- If anything surprised you, add a line to `CHANGELOG.md`. Future-you, and the
  judge reading the repo, will want the trail.

## The outage drill (do it once, now)

An alarm you have never heard is not an alarm. Ten minutes, once:

1. **Break it on purpose:** Actions → `live-paper-trading` → *Run workflow*,
   tick `dry_run` — no. Instead, temporarily set the repository variable
   `APCA_EXPECTED_ACCOUNT_ID` to `PA3WRONGACCOUNT`. Guards should refuse the run
   (Actions log shows `guards: BLOCKED — account mismatch`).
2. **Confirm the alarm:** the `alert` job should open an issue labelled
   `bot-alert`, and you should get the GitHub notification/e-mail you configured.
3. **Acknowledge it:** close the issue. That is the acknowledgement, and it is
   what stops a repeat failure from being silently ignored.
4. **Fix it:** delete the variable (or set the true account number) and run the
   job again — it should succeed, and check 4 in `scripts.review` should show a
   single account.

If step 2 produced no notification, the alert path is broken *while everything
else is working* — the best possible time to discover it.
