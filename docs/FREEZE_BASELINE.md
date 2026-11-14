# Declared code-freeze baseline

## Decision for this patch

The proposed reproducibility window begins 2026-11-16 00:00 UTC (Monday),
AFTER the setup changes of 13 November. Shipping this file adopts that declaration.
If this date passes before deployment, select and document a new future start
rather than backdating a clean window. This is a project audit boundary, not an
organizer ruling on competition eligibility.

Baseline: code c80e65d3e2a33bf8; configuration e62a22f672d2206a;
paper account PA3XNHACZ. Values were read from deployed code and provenance.

13 November remains a real paper-trading session with holdings and performance
history. It is labelled setup/commissioning for our conservative frozen-window
claim. Do not erase its logs, reset the account, or pretend the initial purchases
occurred on Monday. Monday starts with existing holdings, not a fresh $100,000.

## Monitoring behavior

- Before the declared start: provenance reports that the freeze is pending.
- From the start: logged code, account and configuration must match the declared
  values; absent evidence fails rather than passing silently.
- All historical provenance rows remain intact. Malformed timestamps fail.
- Without a baseline file the previous all-history check remains active.
- This changes monitoring only, not strategy, order execution, or cron jobs.

A report after midnight but before Monday's first trade check can legitimately
say no freeze-window evidence yet. Never fix that by fabricating rows.

## Limits and counting

This patch does not implement a 30-session eligibility counter or rewrite the
performance report to show only this window. Existing reports can include setup
history. Label those correctly and use a separately scoped evaluation when
submitting. Do not equate 30 decisions, duplicate snapshots, or calendar days
with 30 distinct US trading sessions.

The existing optional trading_activity --live-start setting remains unset. That
check only asks whether any orders exist and does not properly scope its trade
rows by start date; it must not be used as proof of evaluation eligibility.
A legitimate frozen strategy may HOLD on Monday and need no new order.

## After pushing

Let the next daily report regenerate results/health.md. Alternatively run the
DAILY-REPORT workflow, not the trading workflow, to refresh the health report.
Close the existing alert issue after the provenance result is verified; closing
an issue is acknowledgment and does not itself fix a check.

Commit summary: Declare forward-looking provenance baseline; preserve setup history
