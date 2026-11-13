# Reading the code without being a programmer

A guided tour of this repository for reviewers, judges, or anyone curious who
does not write Python. Every file listed below has plain-English comments at the
top explaining what it does — you can read those comments alone and understand
the system. Nothing important is hidden in clever code.

---

## The one-minute version

Every US trading day this bot does exactly one of three things:

1. **checks** whether each of its funds is still going up (two simple tests);
2. **adjusts** the account so that the percentages match today's answer;
3. **writes down** what it decided and why, in files a human can open.

That is the entire strategy. The sophistication is in *what it refuses to do*:
it will not buy something that is falling, it will not borrow, it will not
concentrate, and it will not shake when the market shakes.

## The four questions the code answers

| Question | File | Plain-English answer |
|---|---|---|
| "What should I hold?" | `src/strategies/adaptive_trend.py` | The brain. Turns prices into a target list of percentages. |
| "Where do prices come from?" | `scripts/data.py` | Downloads daily closing prices from Yahoo Finance and caches them. |
| "Should I trust the past numbers?" | `scripts/backtest.py` | Replays history using that same brain, paying fees, never peeking ahead. |
| "Do it for real, then write it down" | `scripts/run_daily.py` | Sends the orders to the paper account and appends to the public logs. |
| "How did it actually do?" | `scripts/report.py` | Turns the daily log into a chart and the Sharpe / drawdown table. |
| "Is anything broken?" | `tests/test_adaptive_trend.py` | 18 automatic checks that run in two seconds. |
| "Who presses start every morning?" | `.github/workflows/live.yml` | GitHub's scheduler. Your laptop can stay off. |
| "Is anything quietly broken?" | `scripts/monitor.py` | Five independent health checks; opens an issue if one fails. |
| "Where can someone look at it?" | `scripts/dashboard.py` | Builds one self-contained web page from the logs. |

## File by file

### `src/strategies/adaptive_trend.py` — the brain (start here)

The settings panel at the top (`ATAConfig`) is written for humans: it says, in
comments, that SPY is "a fund of the 500 biggest US companies, 45% of the
account" and that `trend_window: 200` means "is the price above its own average
over the last ~10 months?". Change a number there and the strategy changes; there
is no second place to edit.

Then four stages, in order:

* **Select** — for each fund, two checks: *is it above its 200-day average?* and
  *has it made money over six months?* Both must pass.
* **Allocate** — the two stock funds get 45% and 25% when they pass. The
  diversifier funds (gold, two kinds of government bonds, commodities) share 30%
  between whichever of them pass, giving calmer ones a bigger slice.
* **Time** — anything that failed its check has its money moved to Treasury
  bills (ticker BIL), which pays interest and barely moves.
* **Risk** — measure how much the resulting mix is likely to swing. If it would
  swing more than 12% a year, shrink every risky holding and hold the difference
  as cash. It can never invest more than 100% (no borrowing), and there is a
  10% dead-zone so it does not fuss over tiny changes.

### `scripts/run_daily.py` — the hands

This is the only file that talks to the broker. Five steps, in order: refresh
prices → compute targets → compare with what the account holds → trade only if
something is more than 2% off → write the decision and the reasoning to `logs/`.

Two safety stops are worth pointing out:

* it refuses to run if the broker address is not a **paper** (pretend money)
  address — even if someone accidentally supplied real keys;
* it logs in, and if the login is rejected it stops immediately with a message
  saying what to fix, rather than retrying a wrong password.

### `scripts/backtest.py` — the time machine

Replays history. The two rules that make the result believable are written in
the file's opening comment: **no peeking** (a Friday decision only sees data up
to that Friday, and the trade happens the next trading day) and **costs are
paid** (0.10% of everything moved). It prints a table of headline statistics, a
year-by-year comparison with simply buying the S&P 500, and a breakdown by era.

### `scripts/report.py` — the scoreboard

Reads the daily log the bot has been writing and produces the chart plus the
numbers a judge asks for. It can also rebuild the curve directly from Alpaca's
own records (`--from-alpaca`), so a missed run cannot create a gap in the story.

### `tests/test_adaptive_trend.py` — the safety net

Eighteen automatic checks, all on made-up prices, all offline. The interesting
ones: *in a crash the bot really does move the money into Treasury bills*, *no
holding ever exceeds its cap*, *the percentages never add up to more than 100%*,
and *the same input always gives the same output*.

### `scripts/monitor.py` — the overseer that watches the plumbing

Not the market. It checks five things a trend strategy cannot notice about
itself: is the data fresh, did the runs actually happen, has the loss gone past
the backtested worst case, did any order ever reach the broker, and do the logged
targets still look like the strategy (long only, ≤100%). It never promotes or
demotes anything, because with ~20 daily observations a performance-based rule
would be reacting to noise.

### `scripts/dashboard.py` — one page, no server

Writes a single `docs/index.html` with all styling inline and the chart embedded
as a data URI. No CDN, no hosting dependency, no API call at view time — so it
cannot quietly break, and it can never show a number that disagrees with the
committed trade log. GitHub Pages can serve it directly from `/docs`.

### `.github/workflows/live.yml` — the autopilot

GitHub runs this on a schedule. Every weekday at 09:45 New York it trades, at
16:15 New York it records the closing balance, and after each run it commits the
logs back into the repository. Commented line by line.

## Reading the outputs

The files the bot writes are deliberately simple, because they are the evidence:

```
logs/decisions.csv      one row per day: what was decided, why, and the full target
logs/trades.csv         one row per order actually sent to the broker
results/daily_equity.csv  one row per day: the account's closing value
results/last_target.json  what the bot last aimed for (used to detect drift)
```

Everything is append-only. Nothing is edited after the fact, which is why the
record is worth something.

## Following one decision, end to end

1. Friday 16:00 New York — prices close.
2. Monday 09:45 — the workflow wakes up and downloads the data.
3. `adaptive_trend.py` runs the four stages and returns, say,
   `SPY 44%, BIL 54%, +2% buffer`.
4. `run_daily.py` compares that with the account; if a gap is wider than 2%, it
   asks the broker to rebalance.
5. It appends the reasoning — including which trend checks passed — to
   `logs/decisions.csv`, and the broker's confirmations to `logs/trades.csv`.
6. At 16:15 the account's closing value is appended to
   `results/daily_equity.csv`.
7. All three files are committed to GitHub, where they stay visible forever.

## If you only read three things

1. The settings panel at the top of `src/strategies/adaptive_trend.py` — that is
   the whole strategy in twenty commented lines.
2. The five numbered steps in `scripts/run_daily.py::rebalance`.
3. The two honesty rules in the opening comment of `scripts/backtest.py`.

Between them they contain the entire idea, the entire operation, and the entire
reason to believe the numbers.
